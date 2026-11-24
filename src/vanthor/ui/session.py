"""Ponte entre o motor (thread própria, sem Qt) e a interface (thread principal).

Também amarra a regra de segurança central: a automação só inicia se a tecla de emergência
tiver sido registrada no Windows. Sem freio, não há partida.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal, Slot

from vanthor.actions.dispatcher import ActionDispatcher
from vanthor.capture.monitor import ZoneMonitor
from vanthor.capture.zones import Zone
from vanthor.core.engine import AutomationEngine, EngineState, StopReason
from vanthor.core.events import Event, EventBus, EventKind
from vanthor.persistence.repositories import ProfileError, ProfileRepository, SettingsRepository
from vanthor.rules.engine import RuleEngine
from vanthor.rules.models import Rule
from vanthor.winsys.hotkeys import HotkeyError, create_global_hotkey, parse_hotkey

log = logging.getLogger(__name__)

_LIFECYCLE = {
    EventKind.ENGINE_STARTED,
    EventKind.ENGINE_PAUSED,
    EventKind.ENGINE_RESUMED,
    EventKind.ENGINE_STOPPED,
    EventKind.EMERGENCY_STOP,
}


@dataclass(frozen=True, slots=True)
class WatchPlan:
    """O que vigiar: só zonas ligadas E usadas por regras ativas (zona sem uso não gasta CPU)."""

    zones: list[Zone] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    zone_names: dict[int, str] = field(default_factory=dict)


def build_watch_plan(
    zones: list[Zone], rules: list[Rule], hidden_zones: set[int], hidden_rules: set[int]
) -> WatchPlan:
    enabled = {z.id: z for z in zones if z.enabled and z.id not in hidden_zones}
    runnable = [r for r in rules if r.runnable and r.id not in hidden_rules and r.zone_id in enabled]
    used = {r.zone_id for r in runnable}
    return WatchPlan(
        zones=[z for zid, z in enabled.items() if zid in used],
        rules=runnable,
        zone_names={zid: z.name for zid, z in enabled.items()},
    )


Planner = Callable[[set[int], set[int]], WatchPlan]


class AutomationSession(QObject):
    state_changed = Signal(object)  # EngineState
    event_received = Signal(object)  # Event
    start_blocked = Signal(str)
    profile_changed = Signal(object)  # Profile
    simulated_click = Signal(int, int, str)  # x, y físicos e rótulo (modo de teste)
    _event_from_engine = Signal(object)
    _emergency_from_x11 = Signal()
    _simulated_from_worker = Signal(int, int, str)

    def __init__(
        self,
        engine: AutomationEngine,
        bus: EventBus,
        settings: SettingsRepository,
        zone_monitor: ZoneMonitor,
        rule_engine: RuleEngine,
        dispatcher: ActionDispatcher,
        planner: Planner,
        profiles: ProfileRepository,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._bus = bus
        self._settings = settings
        self._zone_monitor = zone_monitor
        self._rule_engine = rule_engine
        self._dispatcher = dispatcher
        self._planner = planner
        self._profiles = profiles
        # Itens em exclusão (ainda no banco durante o "Desfazer"), que já param de ser vigiados.
        self._hidden_zones: set[int] = set()
        self._hidden_rules: set[int] = set()
        # No Linux (X11) a tecla de emergência chega por uma thread própria: o sinal entrega na thread da sessão.
        self._emergency_from_x11.connect(self.emergency_stop)
        self._hotkey = create_global_hotkey(on_trigger=self._emergency_from_x11.emit)
        self._hwnd: int | None = None
        # Eventos chegam na thread do motor; o sinal interno os entrega na thread desta sessão.
        self._event_from_engine.connect(self._on_event)
        # O clique simulado nasce na thread de ações; o sinal interno o entrega na thread da interface.
        self._simulated_from_worker.connect(self.simulated_click.emit)
        dispatcher.set_simulated_click_listener(self._simulated_from_worker.emit)
        self._unsubscribe = bus.subscribe(self._event_from_engine.emit)

    # -- consultas --------------------------------------------------------------------
    @property
    def engine(self) -> AutomationEngine:
        return self._engine

    @property
    def zone_monitor(self) -> ZoneMonitor:
        return self._zone_monitor

    @property
    def rule_engine(self) -> RuleEngine:
        return self._rule_engine

    @property
    def state(self) -> EngineState:
        return self._engine.state

    def emergency_label(self) -> str:
        try:
            return parse_hotkey(self._settings.load().emergency_hotkey).label
        except HotkeyError:
            return "Esc"

    def is_emergency_hotkey(self, hotkey_id: int) -> bool:
        return hotkey_id == self._hotkey.hotkey_id

    @property
    def dry_run(self) -> bool:
        """A automação atual está em modo de teste? (só faz sentido enquanto não está parada)."""
        return self._engine.state is not EngineState.STOPPED and self._dispatcher.dry_run

    def watching_counts(self) -> tuple[int, int]:
        """(automações, zonas) vigiadas agora."""
        plan = self.current_plan()
        return len(plan.rules), len(plan.zones)

    def current_plan(self) -> WatchPlan:
        return self._planner(self._hidden_zones, self._hidden_rules)

    # -- comandos ---------------------------------------------------------------------
    def attach_window(self, hwnd: int) -> None:
        self._hwnd = hwnd

    def start(self, dry_run: bool = False) -> bool:
        """Inicia a automação. ``dry_run``: modo de teste (nada é clicado nem digitado)."""
        if self._engine.state is not EngineState.STOPPED:
            return False
        if self._hwnd is None:
            self.start_blocked.emit("A janela ainda não está pronta. Tente de novo em instantes.")
            return False
        try:
            hotkey = parse_hotkey(self._settings.load().emergency_hotkey)
        except HotkeyError as exc:
            self.start_blocked.emit(f"A tecla de emergência configurada é inválida ({exc}). Ajuste em Configurações.")
            return False
        if not self._hotkey.register(self._hwnd, hotkey):
            self.start_blocked.emit(
                f"Outro programa já usa {hotkey.label}. Escolha outra tecla de emergência em Configurações "
                "para poder iniciar."
            )
            return False
        self._apply_plan()
        self._dispatcher.arm(dry_run=dry_run, max_clicks_per_minute=self._settings.load().max_clicks_per_minute)
        if not self._engine.start():
            self._dispatcher.halt()
            self._hotkey.unregister()
            return False
        if dry_run:
            self._bus.publish(
                Event(EventKind.NOTICE, "Modo de teste: as automações mostram onde agiriam, sem clicar nem digitar")
            )
        return True

    def reload_zones_excluding(self, excluded: set[int]) -> None:
        self._hidden_zones = set(excluded)
        self.refresh_watch()

    def reload_rules_excluding(self, excluded: set[int]) -> None:
        self._hidden_rules = set(excluded)
        self.refresh_watch()

    def refresh_watch(self) -> None:
        """Aplica na hora mudanças em zonas e regras feitas durante a automação."""
        if self._engine.state is not EngineState.STOPPED:
            self._apply_plan()

    def switch_profile(self, profile_id: int) -> None:
        """Troca o perfil em uso. Só com a automação parada: nada muda no meio de uma execução."""
        if self._engine.state is not EngineState.STOPPED:
            raise ProfileError("Pare as automações para trocar de perfil")
        self._profiles.set_active(profile_id)
        self._hidden_zones.clear()
        self._hidden_rules.clear()
        self.profile_changed.emit(self._profiles.active())

    def toggle_pause(self) -> None:
        if self._engine.state is EngineState.RUNNING:
            self._engine.pause()
            self._dispatcher.clear_pending()  # nada "atrasado" executa depois de retomar
        elif self._engine.state is EngineState.PAUSED:
            self._engine.resume()

    def stop(self) -> None:
        self._engine.stop(StopReason.USER)

    def emergency_stop(self) -> None:
        self._engine.emergency_stop()

    def shutdown(self) -> None:
        self._engine.stop(StopReason.SHUTDOWN)
        self._hotkey.unregister()
        self._unsubscribe()

    # -- interno ----------------------------------------------------------------------
    def _apply_plan(self) -> None:
        plan = self.current_plan()
        self._zone_monitor.set_zones(plan.zones)
        self._rule_engine.set_rules(plan.rules, plan.zone_names)

    @Slot(object)
    def _on_event(self, event: Event) -> None:
        if event.kind in _LIFECYCLE:
            if self._engine.state is EngineState.STOPPED:
                # Cobre também paradas vindas do próprio motor (erro): o Esc volta a ser do usuário.
                self._hotkey.unregister()
            self.state_changed.emit(self._engine.state)
        self.event_received.emit(event)
