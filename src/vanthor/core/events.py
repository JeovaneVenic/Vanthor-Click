"""Barramento de eventos thread-safe entre o motor e quem observa (interface, histórico).

Não depende de Qt: o motor publica a partir da própria thread, e cada assinante decide
como atravessar para a sua (a interface usa um sinal Qt com conexão enfileirada).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

log = logging.getLogger(__name__)


class EventKind(StrEnum):
    ENGINE_STARTED = "engine_started"
    ENGINE_PAUSED = "engine_paused"
    ENGINE_RESUMED = "engine_resumed"
    ENGINE_STOPPED = "engine_stopped"
    EMERGENCY_STOP = "emergency_stop"
    DETECTION = "detection"
    ACTION = "action"
    SIMULATED_ACTION = "simulated_action"
    ERROR = "error"
    NOTICE = "notice"  # informação que não muda o estado (ex.: captura voltou ao normal)


class EventLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Event:
    kind: EventKind
    message: str
    level: EventLevel = EventLevel.INFO
    rule_id: int | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[Subscriber] = []

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def publish(self, event: Event) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber(event)
            except Exception:
                # Um assinante com defeito (ex.: falha ao gravar o histórico) não pode
                # derrubar o motor no meio de uma automação. Registramos e seguimos.
                log.exception("Assinante falhou ao processar o evento %s", event.kind)
