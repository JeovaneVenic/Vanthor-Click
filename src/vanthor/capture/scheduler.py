"""Decide quais zonas capturar em cada ciclo, respeitando a frequência de cada uma.

Zonas com o mesmo retângulo são capturadas uma única vez por ciclo (sem trabalho duplicado).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from vanthor.capture.geometry import ScreenRect
from vanthor.capture.zones import Zone


@dataclass(frozen=True, slots=True)
class CaptureGroup:
    rect: ScreenRect
    zone_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class CaptureBatch:
    """Uma captura física que atende vários grupos (recortados depois)."""

    rect: ScreenRect
    groups: tuple[CaptureGroup, ...]


# Medido: cada captura custa ~8 ms fixos + ~1 ms a cada ~70 mil pixels. Juntar zonas compensa
# enquanto a área extra capturada for pequena perto desse custo fixo.
_MERGE_SLACK_PIXELS = 250_000
_MAX_BATCH_PIXELS = 1_200_000


def plan_batches(groups: list[CaptureGroup], screens: list[ScreenRect]) -> list[CaptureBatch]:
    """Agrupa capturas próximas do MESMO monitor numa só (menos chamadas ao Windows por ciclo).

    Junta dois blocos quando o retângulo que os envolve não passa de (soma das áreas + folga)
    nem de um teto absoluto. Zonas em monitores diferentes nunca se juntam (escala/DPI distintos).
    """
    batches: list[CaptureBatch] = []
    for screen in screens:
        pending = [g for g in groups if screen.contains_rect(g.rect)]
        clusters: list[tuple[ScreenRect, list[CaptureGroup], int]] = []  # (envoltório, grupos, área útil)
        for group in sorted(pending, key=lambda g: (g.rect.y, g.rect.x)):
            merged = False
            for index, (bounds, members, useful) in enumerate(clusters):
                union = _union(bounds, group.rect)
                total_useful = useful + group.rect.area
                if union.area <= min(total_useful + _MERGE_SLACK_PIXELS, _MAX_BATCH_PIXELS):
                    clusters[index] = (union, [*members, group], total_useful)
                    merged = True
                    break
            if not merged:
                clusters.append((group.rect, [group], group.rect.area))
        batches += [CaptureBatch(bounds, tuple(members)) for bounds, members, _ in clusters]
    return batches


def _union(a: ScreenRect, b: ScreenRect) -> ScreenRect:
    left, top = min(a.x, b.x), min(a.y, b.y)
    return ScreenRect(left, top, max(a.right, b.right) - left, max(a.bottom, b.bottom) - top)


_TOLERANCE_S = 0.012  # meio ciclo do motor (25 ms)


class CaptureScheduler:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._zones: dict[int, Zone] = {}
        self._last_run: dict[int, float] = {}

    def set_zones(self, zones: list[Zone]) -> None:
        """Troca as zonas vigiadas; zonas mantidas preservam o ritmo (sem disparo extra)."""
        enabled = {z.id: z for z in zones if z.enabled}
        with self._lock:
            self._zones = enabled
            self._last_run = {zid: t for zid, t in self._last_run.items() if zid in enabled}

    def reset(self) -> None:
        with self._lock:
            self._last_run.clear()

    @property
    def zone_count(self) -> int:
        with self._lock:
            return len(self._zones)

    def due(self, now: float) -> list[CaptureGroup]:
        """Zonas na hora de capturar. ``_last_run`` guarda o HORÁRIO MARCADO da última captura.

        A próxima fica marcada para "marcação anterior + intervalo" (não "agora + intervalo"),
        com tolerância de meio ciclo do motor: um ciclo que chega 1 ms adiantado não empurra a
        captura para o ciclo seguinte, e pequenos desvios não se acumulam.
        """
        groups: dict[ScreenRect, list[int]] = {}
        with self._lock:
            for zone in self._zones.values():
                interval = zone.interval_ms / 1000
                marked = self._last_run.get(zone.id)
                if marked is not None and now < marked + interval - _TOLERANCE_S:
                    continue
                next_mark = now if marked is None else marked + interval
                if now - next_mark > interval:  # atrasou mais de uma volta: recomeça do agora
                    next_mark = now
                self._last_run[zone.id] = next_mark
                groups.setdefault(zone.rect, []).append(zone.id)
        return [CaptureGroup(rect, tuple(sorted(ids))) for rect, ids in groups.items()]
