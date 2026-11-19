"""Vigia as zonas: roda a cada ciclo do motor, captura o que está na hora e guarda o quadro.

A Fase 3 acrescenta a detecção de cor sobre os quadros guardados aqui.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from vanthor.capture.capturer import CaptureError, Image, RegionCapturer
from vanthor.capture.geometry import ScreenRect
from vanthor.capture.scheduler import CaptureScheduler, plan_batches
from vanthor.capture.zones import Zone
from vanthor.core.events import Event, EventBus, EventKind, EventLevel

log = logging.getLogger(__name__)

Capturer = Callable[[ScreenRect], Image]
CapturerFactory = Callable[[], RegionCapturer]
ScreensProvider = Callable[[], list[ScreenRect]]

_SCREENS_REFRESH_S = 5.0  # monitores mudam raramente; reler a cada ciclo seria desperdício


@dataclass(frozen=True, slots=True)
class Frame:
    image: Image
    captured_at: float
    capture_ms: float


@dataclass(frozen=True, slots=True)
class ZoneStatus:
    captures: int
    last_capture_ms: float | None
    error: str | None


class ZoneMonitor:
    def __init__(
        self,
        bus: EventBus,
        screens: ScreensProvider,
        capturer_factory: CapturerFactory = RegionCapturer,
    ) -> None:
        self._bus = bus
        self._screens_provider = screens
        self._factory = capturer_factory
        self._scheduler = CaptureScheduler()
        self._lock = threading.Lock()
        self._names: dict[int, str] = {}
        self._frames: dict[int, Frame] = {}
        self._captures: dict[int, int] = {}
        self._last_ms: dict[int, float] = {}
        self._errors: dict[int, str] = {}
        self._capturer: RegionCapturer | None = None
        self._capturer_thread: int | None = None
        self._screens: list[ScreenRect] = []
        self._screens_read_at = -_SCREENS_REFRESH_S

    # -- configuração (qualquer thread) ----------------------------------------------
    def set_zones(self, zones: list[Zone]) -> None:
        with self._lock:
            self._names = {z.id: z.name for z in zones}
            active = set(self._names)
            self._frames = {k: v for k, v in self._frames.items() if k in active}
            self._errors = {k: v for k, v in self._errors.items() if k in active}
        self._scheduler.set_zones(zones)

    @property
    def zone_count(self) -> int:
        return self._scheduler.zone_count

    def latest_frame(self, zone_id: int) -> Frame | None:
        with self._lock:
            return self._frames.get(zone_id)

    def status(self, zone_id: int) -> ZoneStatus:
        with self._lock:
            return ZoneStatus(self._captures.get(zone_id, 0), self._last_ms.get(zone_id), self._errors.get(zone_id))

    def close_capturer(self) -> None:
        """Rotina de saída da thread do motor: fecha a captura NA thread que a criou.

        O Windows só libera um contexto gráfico (ReleaseDC) na mesma thread que o obteve.
        """
        if self._capturer is not None and self._capturer_thread == threading.get_ident():
            self._capturer.close()
            self._capturer = None
            self._capturer_thread = None

    def release(self) -> None:
        """Rotina de limpeza do motor (roda depois que a thread do motor terminou)."""
        if self._capturer is not None and self._capturer_thread == threading.get_ident():
            self._capturer.close()  # parada por erro: a limpeza roda na própria thread do motor
        elif self._capturer is not None:
            # Não deveria acontecer (close_capturer roda antes); se acontecer, só soltamos a
            # referência: fechar daqui (outra thread) falharia no Windows.
            log.warning("Capturador não foi fechado pela thread do motor")
        self._capturer = None
        self._capturer_thread = None
        self._scheduler.reset()
        with self._lock:
            self._frames.clear()
            self._captures.clear()
            self._last_ms.clear()
            self._errors.clear()

    # -- ciclo do motor (thread do motor) ---------------------------------------------
    def __call__(self, now: float) -> None:
        capturer = self._capturer_for_this_thread()
        screens = self._current_screens(now)
        due = self._scheduler.due(now)
        for group in due:
            if not any(s.contains_rect(group.rect) for s in screens):
                self._mark_error(group.zone_ids, "A zona está fora das telas conectadas")
        # Zonas próximas viram UMA captura recortada depois: cada chamada ao Windows tem custo fixo.
        for batch in plan_batches(due, screens):
            started = time.perf_counter()
            try:
                image = capturer.grab(batch.rect)
            except CaptureError as exc:
                log.warning("Falha ao capturar %s: %s", batch.rect, exc)
                for group in batch.groups:
                    self._mark_error(group.zone_ids, f"Falha na captura: {exc}")
                continue
            elapsed_ms = (time.perf_counter() - started) * 1000
            for group in batch.groups:
                top, left = group.rect.y - batch.rect.y, group.rect.x - batch.rect.x
                crop = image[top : top + group.rect.height, left : left + group.rect.width]
                self._store(group.zone_ids, Frame(crop, now, elapsed_ms), elapsed_ms)

    def _store(self, zone_ids: tuple[int, ...], frame: Frame, elapsed_ms: float) -> None:
        with self._lock:
            for zone_id in zone_ids:
                self._frames[zone_id] = frame
                self._captures[zone_id] = self._captures.get(zone_id, 0) + 1
                self._last_ms[zone_id] = elapsed_ms
                recovered = self._errors.pop(zone_id, None)
                if recovered is not None:
                    self._bus.publish(
                        Event(EventKind.NOTICE, f"{self._names.get(zone_id, 'Zona')}: captura normalizada")
                    )

    def _capturer_for_this_thread(self) -> RegionCapturer:
        # Cada execução do motor roda numa thread nova; o capturador precisa nascer nela.
        ident = threading.get_ident()
        if self._capturer is None or self._capturer_thread != ident:
            if self._capturer is not None:
                self._capturer.close()
            self._capturer = self._factory()
            self._capturer_thread = ident
        return self._capturer

    def _current_screens(self, now: float) -> list[ScreenRect]:
        if now - self._screens_read_at >= _SCREENS_REFRESH_S:
            self._screens = self._screens_provider()
            self._screens_read_at = now
        return self._screens

    def _mark_error(self, zone_ids: tuple[int, ...], message: str) -> None:
        for zone_id in zone_ids:
            with self._lock:
                is_new = self._errors.get(zone_id) != message
                self._errors[zone_id] = message
            # Publica uma vez por episódio: o histórico não enche com o mesmo erro 20× por segundo.
            if is_new:
                name = self._names.get(zone_id, "Zona")
                self._bus.publish(Event(EventKind.ERROR, f"{name}: {message}", EventLevel.WARNING))
