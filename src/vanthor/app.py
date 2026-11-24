"""Ponto de entrada: monta os serviços, aplica o tema e abre a janela."""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from vanthor import APP_ID, APP_NAME, __version__
from vanthor.capture.monitor import ZoneMonitor
from vanthor.core.engine import AutomationEngine
from vanthor.core.events import EventBus
from vanthor.logging_setup import configure_logging
from vanthor.persistence.database import Database
from vanthor.persistence.repositories import (
    ActivityRepository,
    ProfileRepository,
    RuleRepository,
    SettingsRepository,
    ZoneRepository,
)
from vanthor.ui.context import AppContext
from vanthor.ui.main_window import MainWindow
from vanthor.actions.controller import InputController
from vanthor.actions.dispatcher import ActionDispatcher
from vanthor.rules.engine import RuleEngine
from vanthor.ui.session import AutomationSession, WatchPlan, build_watch_plan
from vanthor.ui.theme.stylesheet import apply_theme
from vanthor.ui.translation import install_qt_translation
from vanthor.winsys.desktop import create_app_mutex, set_app_user_model_id
from vanthor.winsys.input import create_input_backend
from vanthor.winsys.monitors import enumerate_monitors
from vanthor.winsys.paths import data_dir, database_path, resource_path

log = logging.getLogger(__name__)


def build_context(db: Database) -> AppContext:
    bus = EventBus()
    engine = AutomationEngine(bus)
    settings = SettingsRepository(db)
    activity = ActivityRepository(db)
    profiles = ProfileRepository(db)
    zones = ZoneRepository(db)
    bus.subscribe(activity.append)  # todo evento vira histórico permanente

    rules = RuleRepository(db)

    # Ordem importa: a captura roda antes das regras no mesmo ciclo (regras leem o quadro novo).
    zone_monitor = ZoneMonitor(bus, screens=lambda: [m.rect for m in enumerate_monitors()])
    dispatcher = ActionDispatcher(
        bus,
        controller_factory=lambda: InputController(create_input_backend()),
        recorder=rules.record,
        screens=lambda: [m.rect for m in enumerate_monitors()],
    )
    def record_real_only(rule_id: int, **counts: int) -> None:
        # O modo de teste não pode inflar as estatísticas reais (detecções, execuções, cliques).
        if not dispatcher.dry_run:
            rules.record(rule_id, **counts)

    dispatcher.set_recorder(record_real_only)
    rule_engine = RuleEngine(bus, zone_monitor.latest_frame, dispatcher, record_real_only)
    # Emergência: a fila para e solta mouse/teclado ANTES de qualquer outra limpeza.
    engine.add_safety_hook(dispatcher.halt)
    engine.add_cleanup_hook(dispatcher.join)
    engine.add_tick_handler(zone_monitor)
    engine.add_tick_handler(rule_engine)
    engine.add_thread_exit_hook(zone_monitor.close_capturer)
    engine.add_cleanup_hook(zone_monitor.release)
    engine.add_cleanup_hook(rule_engine.reset)

    def planner(hidden_zones: set[int], hidden_rules: set[int]) -> WatchPlan:
        profile_id = profiles.active().id
        return build_watch_plan(zones.list(profile_id), rules.list(profile_id), hidden_zones, hidden_rules)

    session = AutomationSession(engine, bus, settings, zone_monitor, rule_engine, dispatcher, planner, profiles)
    return AppContext(
        db=db,
        bus=bus,
        engine=engine,
        session=session,
        settings=settings,
        profiles=profiles,
        rules=rules,
        zones=zones,
        activity=activity,
    )


def main() -> int:
    configure_logging()
    log.info("Iniciando %s %s", APP_NAME, __version__)
    set_app_user_model_id(f"Vanthor.{APP_ID}")
    self_test = "--self-test" in sys.argv
    mutex = None if self_test else create_app_mutex()  # noqa: F841 (mantido vivo até o fim do processo)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Vanthor")
    app.setWindowIcon(QIcon(str(resource_path("brand", "app.ico"))))
    if sys.platform.startswith("linux"):
        # Liga as janelas ao atalho instalado (vanthor-bot-click.desktop): ícone certo no painel do XFCE.
        app.setDesktopFileName("vanthor-bot-click")
    translated = install_qt_translation(app)

    if self_test:
        return _run_self_test(app, translated)

    # Duas instâncias disputariam a tecla de emergência e o mesmo banco.
    lock = QLockFile(str(data_dir() / "instance.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, APP_NAME, "O Vanthor Bot Click já está aberto. Procure-o na barra de tarefas.")
        return 0

    try:
        db = Database(database_path())
    except (sqlite3.DatabaseError, RuntimeError) as exc:
        log.exception("Falha ao abrir o banco de dados")
        QMessageBox.critical(
            None,
            APP_NAME,
            f"Não foi possível abrir os dados do aplicativo.\n\n{exc}\n\nArquivo: {database_path()}",
        )
        return 1

    ctx = build_context(db)
    apply_theme(app)
    _warn_if_linux_compositor_off()
    window = MainWindow(ctx)
    # Quem encerra o app é o "Sair" (ou fechar a janela com o segundo plano desligado),
    # não o Qt ao perceber que nenhuma janela está visível.
    app.setQuitOnLastWindowClosed(False)
    if "--minimized" in sys.argv:
        # Início com o Windows: direto na bandeja (ou minimizado, se o segundo plano estiver desligado).
        if not window.run_in_background():
            window.showMinimized()
    else:
        window.show()

    def on_quit() -> None:
        # Ordem importa: a janela fecha primeiro (para as automações, grava exclusões pendentes do
        # "Desfazer") e só depois o banco fecha. O Qt ainda chama closeEvent depois deste ponto,
        # por isso a janela é marcada como "encerrando" e não consulta mais o banco.
        window.mark_quitting()
        window.close()
        ctx.session.shutdown()
        db.close()
        log.info("Aplicativo encerrado")

    app.aboutToQuit.connect(on_quit)
    wake_timer = _handle_linux_termination(window)  # noqa: F841 (mantido vivo até o fim)
    return app.exec()


def _handle_linux_termination(window: MainWindow) -> QTimer | None:
    """Linux: logout/desligamento enviam SIGTERM. Equivale ao WM_ENDSESSION do Windows: encerra de
    verdade, parando as automações e soltando botões e teclas, em vez de morrer no meio."""
    if not sys.platform.startswith("linux"):
        return None
    import signal

    signal.signal(signal.SIGTERM, lambda *_: window.quit_app())
    signal.signal(signal.SIGHUP, lambda *_: window.quit_app())
    # O Python só executa o tratador quando roda código Python; o laço do Qt é C++. Um timer leve
    # acorda o interpretador a cada meio segundo para o sinal ser atendido.
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(500)
    return timer


def _warn_if_linux_compositor_off() -> None:
    """Linux: sem o compositor do xfwm4, sobreposições transparentes (seleção de zona, conta-gotas,
    pílula "Vigiando") aparecem como blocos opacos. Avisa e diz como ligar, sem impedir o uso."""
    if not sys.platform.startswith("linux"):
        return
    from vanthor.winsys.x11 import compositor_running

    if compositor_running():
        return
    log.warning("Compositor do gerenciador de janelas desligado")
    QMessageBox.warning(
        None,
        APP_NAME,
        "\n\n".join(
            [
                "O compositor do XFCE está desligado.",
                "Sem ele, a seleção de zonas e o conta-gotas não ficam transparentes.",
                "Para ligar: menu → Configurações → Ajustes do gerenciador de janelas → aba Compositor → "
                'marque "Habilitar composição de exibição". Depois, abra o Vanthor de novo.',
            ]
        ),
    )


def _run_self_test(app: QApplication, translated: bool) -> int:
    """Autoteste do executável: usa um banco próprio, não toca nos dados do usuário."""
    import tempfile

    from vanthor.self_test import run_checks, write_report

    holder: dict[str, object] = {}

    def build_window() -> MainWindow:
        db = Database(Path(tempfile.mkdtemp(prefix="vanthor-selftest-")) / "test.db")
        holder["db"] = db
        ctx = build_context(db)
        holder["ctx"] = ctx
        apply_theme(app)
        window = MainWindow(ctx)
        # Sem mostrar nada: o instalador roda o autoteste e nenhuma janela ou ícone pode piscar.
        window._tray.hide()  # noqa: SLF001
        if window.grab().isNull():  # desenha a janela fora da tela: confere fontes, ícones e estilo
            raise RuntimeError("a janela não pôde ser desenhada")
        holder["window"] = window
        return window

    results = run_checks(build_window)
    results["checks"]["traducao"] = {
        "ok": translated,
        "detail": "PT-BR do Qt carregado" if translated else "tradução do Qt ausente",
    }
    results["ok"] = results["ok"] and translated
    write_report(results)
    window = holder.get("window")
    if isinstance(window, MainWindow):
        window.mark_quitting()
        window.close()
    db = holder.get("db")
    if isinstance(db, Database):
        db.close()
    return 0 if results["ok"] else 1
