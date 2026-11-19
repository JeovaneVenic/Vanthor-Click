from __future__ import annotations

from dataclasses import dataclass

from vanthor.core.engine import AutomationEngine
from vanthor.core.events import EventBus
from vanthor.persistence.database import Database
from vanthor.persistence.repositories import (
    ActivityRepository,
    ProfileRepository,
    RuleRepository,
    SettingsRepository,
    ZoneRepository,
)
from vanthor.ui.session import AutomationSession


@dataclass(frozen=True, slots=True)
class AppContext:
    """Tudo que as telas podem usar. As telas nunca criam serviços por conta própria."""

    db: Database
    bus: EventBus
    engine: AutomationEngine
    session: AutomationSession
    settings: SettingsRepository
    profiles: ProfileRepository
    rules: RuleRepository
    zones: ZoneRepository
    activity: ActivityRepository
