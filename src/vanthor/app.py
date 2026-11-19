"""Ponto de entrada: monta os serviços, aplica o tema e abre a janela."""

from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile
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
from vanthor.winsys.input import Win32Input
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
        controller_factory=lambda: InputController(Win32Input()),
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
    return app.exec()


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
