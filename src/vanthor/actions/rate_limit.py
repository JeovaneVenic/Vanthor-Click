"""Limite de cliques por minuto: a última trava contra rajadas, valendo para todas as regras juntas."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

_WINDOW_S = 60.0


class ClickRateLimiter:
    """Janela deslizante de 60 s. ``try_take(n)`` reserva n cliques ou recusa a ação INTEIRA.

    Recusar a ação inteira (e não clicar "pela metade") evita deixar um programa num estado
    intermediário, como o primeiro clique de uma sequência sem o segundo.
    """

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        if per_minute < 1:
            raise ValueError("O limite precisa ser de pelo menos 1 clique por minuto")
        self._limit = per_minute
        self._clock = clock
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()

    @property
    def limit(self) -> int:
        return self._limit

    def used(self) -> int:
        with self._lock:
            self._expire(self._clock())
            return len(self._stamps)

    def try_take(self, clicks: int) -> bool:
        if clicks <= 0:
            return True
        with self._lock:
            now = self._clock()
            self._expire(now)
            if len(self._stamps) + clicks > self._limit:
                return False
            self._stamps.extend([now] * clicks)
            return True

    def _expire(self, now: float) -> None:
        while self._stamps and now - self._stamps[0] >= _WINDOW_S:
            self._stamps.popleft()
