from __future__ import annotations

from vanthor.core.events import Event, EventBus, EventKind


def test_publish_reaches_all_subscribers() -> None:
    bus = EventBus()
    a: list[Event] = []
    b: list[Event] = []
    bus.subscribe(a.append)
    bus.subscribe(b.append)
    bus.publish(Event(EventKind.ENGINE_STARTED, "ok"))
    assert len(a) == len(b) == 1


def test_failing_subscriber_does_not_block_others() -> None:
    bus = EventBus()
    received: list[Event] = []

    def broken(_event: Event) -> None:
        raise RuntimeError("falha")

    bus.subscribe(broken)
    bus.subscribe(received.append)
    bus.publish(Event(EventKind.ENGINE_STARTED, "ok"))
    assert len(received) == 1


def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    received: list[Event] = []
    unsubscribe = bus.subscribe(received.append)
    unsubscribe()
    unsubscribe()  # idempotente
    bus.publish(Event(EventKind.ENGINE_STARTED, "ok"))
    assert received == []
