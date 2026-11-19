"""Repositórios: a única porta de entrada para ler e gravar dados de negócio."""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from typing import Any

from vanthor.capture.geometry import ScreenRect
from vanthor.capture.zones import (
    DEFAULT_INTERVAL_MS,
    MAX_INTERVAL_MS,
    MIN_INTERVAL_MS,
    Zone,
    next_zone_color,
    next_zone_name,
)
from vanthor.core.events import Event, EventKind, EventLevel
from vanthor.persistence.database import Database, utc_now_iso
from vanthor.rules.models import ActionSpec, Rule, RuleStatus, TriggerSettings, parse_config
from vanthor.vision.color import ColorCondition

log = logging.getLogger(__name__)


# -- configurações ------------------------------------------------------------------------
MAX_CLICKS_PER_MINUTE_RANGE = (1, 1200)


@dataclass(slots=True)
class AppSettings:
    emergency_hotkey: str = "Esc"
    start_with_windows: bool = False
    max_clicks_per_minute: int = 120  # trava de segurança contra rajadas (inclui o modo de teste)
    notifications: bool = True  # avisos do Windows em parada de emergência e erros
    show_indicator: bool = True  # pílula flutuante "Vigiando" enquanto há automação
    run_in_background: bool = True  # fechar a janela mantém o Vanthor na bandeja (e as automações rodando)

    def __post_init__(self) -> None:
        low, high = MAX_CLICKS_PER_MINUTE_RANGE
        if isinstance(self.max_clicks_per_minute, bool) or not low <= self.max_clicks_per_minute <= high:
            raise ValueError(f"Limite de cliques por minuto: de {low} a {high}")


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def load(self) -> AppSettings:
        stored = {row["key"]: row["value"] for row in self._db.query("SELECT key, value FROM settings")}
        settings = AppSettings()
        for f in fields(AppSettings):
            raw = stored.get(f.name)
            if raw is None:
                continue
            try:
                value: Any = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Configuração %s corrompida; usando o padrão", f.name)
                continue
            default = getattr(settings, f.name)
            # bool é subclasse de int em Python: True não pode virar "1 clique por minuto".
            if isinstance(value, type(default)) and isinstance(value, bool) == isinstance(default, bool):
                setattr(settings, f.name, value)
            else:
                log.warning("Configuração %s com tipo inesperado; usando o padrão", f.name)
        try:
            settings.__post_init__()
        except ValueError:
            log.warning("Limite de cliques fora do permitido no banco; usando o padrão")
            settings.max_clicks_per_minute = AppSettings().max_clicks_per_minute
        return settings

    def save(self, settings: AppSettings) -> None:
        with self._db.transaction() as conn:
            for key, value in asdict(settings).items():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )


# -- perfis e regras ------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Profile:
    id: int
    name: str


@dataclass(frozen=True, slots=True)
class RuleTotals:
    rules: int
    active: int
    detections: int
    clicks: int


PROFILE_NAME_MAX = 40


class ProfileError(ValueError):
    """Operação de perfil recusada (mensagem pronta para o usuário)."""


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    profile: Profile
    zones: int
    rules: int
    active_rules: int


class ProfileRepository:
    _ACTIVE_KEY = "active_profile_id"

    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self) -> list[Profile]:
        rows = self._db.query("SELECT id, name FROM profiles ORDER BY name COLLATE NOCASE")
        return [Profile(r["id"], r["name"]) for r in rows]

    def get(self, profile_id: int) -> Profile | None:
        rows = self._db.query("SELECT id, name FROM profiles WHERE id = ?", (profile_id,))
        return Profile(rows[0]["id"], rows[0]["name"]) if rows else None

    def active(self) -> Profile:
        rows = self._db.query("SELECT value FROM settings WHERE key = ?", (self._ACTIVE_KEY,))
        if rows:
            try:
                match = self.get(int(json.loads(rows[0]["value"])))
            except (ValueError, TypeError, json.JSONDecodeError):
                match = None
            if match is not None:
                return match
        first = self._db.query_one("SELECT id, name FROM profiles ORDER BY id LIMIT 1")
        return Profile(first["id"], first["name"])

    def set_active(self, profile_id: int) -> None:
        if self.get(profile_id) is None:
            raise ProfileError("Esse perfil não existe mais")
        self._db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (self._ACTIVE_KEY, json.dumps(profile_id)),
        )

    def summaries(self) -> list[ProfileSummary]:
        rows = self._db.query(
            """
            SELECT p.id, p.name,
                   (SELECT COUNT(*) FROM zones z WHERE z.profile_id = p.id) AS zones,
                   (SELECT COUNT(*) FROM rules r WHERE r.profile_id = p.id) AS rules,
                   (SELECT COUNT(*) FROM rules r WHERE r.profile_id = p.id AND r.status = 'active'
                        AND r.zone_id IS NOT NULL) AS active_rules
            FROM profiles p ORDER BY p.name COLLATE NOCASE
            """
        )
        return [
            ProfileSummary(Profile(r["id"], r["name"]), r["zones"], r["rules"], r["active_rules"]) for r in rows
        ]

    def create(self, name: str) -> Profile:
        clean = self._valid_name(name)
        now = utc_now_iso()
        cursor = self._db.execute(
            "INSERT INTO profiles (name, created_at, updated_at) VALUES (?, ?, ?)", (clean, now, now)
        )
        return Profile(int(cursor.lastrowid or 0), clean)

    def rename(self, profile_id: int, name: str) -> Profile:
        clean = self._valid_name(name, ignore_id=profile_id)
        self._db.execute(
            "UPDATE profiles SET name = ?, updated_at = ? WHERE id = ?", (clean, utc_now_iso(), profile_id)
        )
        return Profile(profile_id, clean)

    def delete(self, profile_id: int) -> None:
        """Exclui o perfil e, em cascata, as zonas e automações dele."""
        if profile_id == self.active().id:
            raise ProfileError("Troque para outro perfil antes de excluir este")
        if len(self.list()) <= 1:
            raise ProfileError("É preciso manter pelo menos um perfil")
        self._db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

    def unique_name(self, base: str) -> str:
        """``base`` se estiver livre; senão "base 2", "base 3"… (sem estourar o tamanho máximo)."""
        base = base.strip()[:PROFILE_NAME_MAX] or "Perfil"
        taken = {p.name.casefold() for p in self.list()}
        if base.casefold() not in taken:
            return base
        number = 2
        while True:
            suffix = f" {number}"
            candidate = f"{base[: PROFILE_NAME_MAX - len(suffix)]}{suffix}"
            if candidate.casefold() not in taken:
                return candidate
            number += 1

    def _valid_name(self, name: str, ignore_id: int | None = None) -> str:
        clean = " ".join(name.split())
        if not clean:
            raise ProfileError("Dê um nome para o perfil")
        if len(clean) > PROFILE_NAME_MAX:
            raise ProfileError(f"Use até {PROFILE_NAME_MAX} caracteres")
        if any(p.name.casefold() == clean.casefold() and p.id != ignore_id for p in self.list()):
            raise ProfileError(f"Já existe um perfil chamado {clean}")
        return clean


class RuleRepository:
    _COLUMNS = (
        "id, profile_id, name, status, zone_id, config, position, detections_total, executions_total, "
        "last_fired_at"
    )

    def __init__(self, db: Database) -> None:
        self._db = db

    def totals(self, profile_id: int) -> RuleTotals:
        row = self._db.query_one(
            """
            SELECT COUNT(*)                                                    AS rules,
                   COALESCE(SUM(status = 'active' AND zone_id IS NOT NULL), 0) AS active,
                   COALESCE(SUM(detections_total), 0)                          AS detections,
                   COALESCE(SUM(clicks_total), 0)                              AS clicks
            FROM rules WHERE profile_id = ?
            """,
            (profile_id,),
        )
        return RuleTotals(int(row["rules"]), int(row["active"]), int(row["detections"]), int(row["clicks"]))

    def list(self, profile_id: int) -> list[Rule]:
        rows = self._db.query(
            f"SELECT {self._COLUMNS} FROM rules WHERE profile_id = ? ORDER BY position, id", (profile_id,)
        )
        rules: list[Rule] = []
        for row in rows:
            rule = self._to_rule(row)
            if rule is not None:
                rules.append(rule)
        return rules

    def get(self, rule_id: int) -> Rule | None:
        rows = self._db.query(f"SELECT {self._COLUMNS} FROM rules WHERE id = ?", (rule_id,))
        return self._to_rule(rows[0]) if rows else None

    def last_fired_at(self, rule_id: int) -> datetime | None:
        rows = self._db.query("SELECT last_fired_at FROM rules WHERE id = ?", (rule_id,))
        if not rows or rows[0]["last_fired_at"] is None:
            return None
        return datetime.fromisoformat(rows[0]["last_fired_at"])

    def create(
        self,
        profile_id: int,
        name: str,
        zone_id: int | None,
        condition: ColorCondition,
        trigger: TriggerSettings,
        action: ActionSpec,
        status: RuleStatus = RuleStatus.ACTIVE,
    ) -> Rule:
        name = self._clean_name(name)
        position = int(
            self._db.query_one("SELECT COALESCE(MAX(position), -1) + 1 FROM rules WHERE profile_id = ?", (profile_id,))[0]
        )
        draft = Rule(0, profile_id, name, status, zone_id, condition, trigger, action, position)
        now = utc_now_iso()
        cursor = self._db.execute(
            "INSERT INTO rules (profile_id, name, status, zone_id, config, position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (profile_id, name, status.value, zone_id, json.dumps(draft.config_dict()), position, now, now),
        )
        rule = self.get(int(cursor.lastrowid or 0))
        if rule is None:
            raise LookupError("Regra recém-criada não encontrada")
        return rule

    def update(self, rule: Rule) -> None:
        self._db.execute(
            "UPDATE rules SET name = ?, status = ?, zone_id = ?, config = ?, position = ?, updated_at = ? WHERE id = ?",
            (
                self._clean_name(rule.name), rule.status.value, rule.zone_id, json.dumps(rule.config_dict()),
                rule.position, utc_now_iso(), rule.id,
            ),
        )

    def set_status(self, rule_id: int, status: RuleStatus) -> None:
        self._db.execute(
            "UPDATE rules SET status = ?, updated_at = ? WHERE id = ?", (status.value, utc_now_iso(), rule_id)
        )

    def duplicate(self, rule_id: int) -> Rule:
        original = self.get(rule_id)
        if original is None:
            raise LookupError(f"Regra {rule_id} não existe")
        if original.zone_id is None:
            raise ValueError("Escolha uma zona para a regra antes de duplicá-la")
        # A cópia nasce PAUSADA: duas regras idênticas ativas disparariam em dobro sem o usuário perceber.
        return self.create(
            original.profile_id, f"{original.name} (cópia)"[:60], original.zone_id, original.condition,
            original.trigger, original.action, RuleStatus.PAUSED,
        )

    def delete(self, rule_id: int) -> None:
        self._db.execute("DELETE FROM rules WHERE id = ?", (rule_id,))

    def record(self, rule_id: int, *, detections: int = 0, executions: int = 0, clicks: int = 0) -> None:
        """Soma contadores (chamado pela thread do motor a cada detecção/execução)."""
        fired = utc_now_iso() if executions else None
        self._db.execute(
            "UPDATE rules SET detections_total = detections_total + ?, executions_total = executions_total + ?, "
            "clicks_total = clicks_total + ?, last_fired_at = COALESCE(?, last_fired_at) WHERE id = ?",
            (detections, executions, clicks, fired, rule_id),
        )

    @staticmethod
    def _clean_name(name: str) -> str:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("Dê um nome para a automação")
        return cleaned[:60]

    @staticmethod
    def _to_rule(row: sqlite3.Row) -> Rule | None:
        try:
            condition, trigger, action = parse_config(json.loads(row["config"]))
            return Rule(
                id=row["id"],
                profile_id=row["profile_id"],
                name=row["name"],
                status=RuleStatus(row["status"]),
                zone_id=row["zone_id"],
                condition=condition,
                trigger=trigger,
                action=action,
                position=row["position"],
                detections_total=row["detections_total"],
                executions_total=row["executions_total"],
            )
        except (ValueError, json.JSONDecodeError):
            # Regra corrompida (edição manual do banco, importação antiga): fica fora em vez de derrubar a tela.
            log.exception("Regra %s com configuração inválida foi ignorada", row["id"])
            return None


# -- zonas ------------------------------------------------------------------------------------
class ZoneRepository:
    _COLUMNS = "id, profile_id, name, color, x, y, width, height, monitor, interval_ms, enabled, position"

    def __init__(self, db: Database) -> None:
        self._db = db

    def list(self, profile_id: int) -> list[Zone]:
        rows = self._db.query(
            f"SELECT {self._COLUMNS} FROM zones WHERE profile_id = ? ORDER BY position, id", (profile_id,)
        )
        return [self._to_zone(r) for r in rows]

    def get(self, zone_id: int) -> Zone | None:
        rows = self._db.query(f"SELECT {self._COLUMNS} FROM zones WHERE id = ?", (zone_id,))
        return self._to_zone(rows[0]) if rows else None

    def create(
        self, profile_id: int, rect: ScreenRect, monitor: str, interval_ms: int = DEFAULT_INTERVAL_MS
    ) -> Zone:
        existing = self.list(profile_id)
        name = next_zone_name([z.name for z in existing])
        color = next_zone_color([z.color for z in existing])
        position = max((z.position for z in existing), default=-1) + 1
        now = utc_now_iso()
        cursor = self._db.execute(
            "INSERT INTO zones (profile_id, name, color, x, y, width, height, monitor, interval_ms, enabled, "
            "position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (profile_id, name, color, rect.x, rect.y, rect.width, rect.height, monitor, interval_ms, position, now, now),
        )
        zone = self.get(int(cursor.lastrowid or 0))
        if zone is None:
            raise LookupError("Zona recém-criada não encontrada")
        return zone

    def update(self, zone: Zone) -> None:
        name = zone.name.strip()
        if not name:
            raise ValueError("O nome da zona não pode ficar vazio")
        if not MIN_INTERVAL_MS <= zone.interval_ms <= MAX_INTERVAL_MS:
            raise ValueError(f"Frequência fora do permitido: {zone.interval_ms} ms")
        self._db.execute(
            "UPDATE zones SET name = ?, color = ?, x = ?, y = ?, width = ?, height = ?, monitor = ?, "
            "interval_ms = ?, enabled = ?, position = ?, updated_at = ? WHERE id = ?",
            (
                name, zone.color, zone.rect.x, zone.rect.y, zone.rect.width, zone.rect.height, zone.monitor,
                zone.interval_ms, int(zone.enabled), zone.position, utc_now_iso(), zone.id,
            ),
        )

    def delete(self, zone_id: int) -> None:
        self._db.execute("DELETE FROM zones WHERE id = ?", (zone_id,))

    def color_draft(self, zone_id: int) -> ColorCondition | None:
        rows = self._db.query("SELECT color_draft FROM zones WHERE id = ?", (zone_id,))
        if not rows or rows[0]["color_draft"] is None:
            return None
        try:
            return ColorCondition.from_dict(json.loads(rows[0]["color_draft"]))
        except (ValueError, json.JSONDecodeError):
            log.warning("Ajuste de cor salvo na zona %s está corrompido; ignorando", zone_id)
            return None

    def save_color_draft(self, zone_id: int, condition: ColorCondition) -> None:
        self._db.execute(
            "UPDATE zones SET color_draft = ?, updated_at = ? WHERE id = ?",
            (json.dumps(condition.to_dict()), utc_now_iso(), zone_id),
        )

    @staticmethod
    def _to_zone(row: sqlite3.Row) -> Zone:
        return Zone(
            id=row["id"],
            profile_id=row["profile_id"],
            name=row["name"],
            color=row["color"],
            rect=ScreenRect(row["x"], row["y"], row["width"], row["height"]),
            monitor=row["monitor"],
            interval_ms=row["interval_ms"],
            enabled=bool(row["enabled"]),
            position=row["position"],
        )


# -- histórico de atividades -------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class ActivityEntry:
    id: int
    timestamp: datetime
    level: EventLevel
    kind: EventKind
    rule_id: int | None
    message: str


class ActivityRepository:
    def __init__(self, db: Database, keep_last: int = 5000) -> None:
        self._db = db
        self._keep_last = keep_last
        self._writes_since_prune = 0

    def append(self, event: Event) -> None:
        self._db.execute(
            "INSERT INTO activity_log (ts, level, kind, rule_id, message) VALUES (?, ?, ?, ?, ?)",
            (
                event.timestamp.isoformat(timespec="milliseconds"),
                event.level.value,
                event.kind.value,
                event.rule_id,
                event.message,
            ),
        )
        self._writes_since_prune += 1
        if self._writes_since_prune >= 500:
            self.prune()

    def recent(
        self,
        limit: int = 50,
        kinds: set[EventKind] | None = None,
        min_level: EventLevel | None = None,
    ) -> list[ActivityEntry]:
        """Mais recentes primeiro. ``kinds`` e ``min_level`` (aviso/erro) filtram, e combinam com OU."""
        where: list[str] = []
        params: list[object] = []
        if kinds:
            where.append(f"kind IN ({', '.join('?' * len(kinds))})")
            params += [k.value for k in kinds]
        if min_level is not None:
            levels = [EventLevel.WARNING.value, EventLevel.ERROR.value]
            if min_level is EventLevel.ERROR:
                levels = [EventLevel.ERROR.value]
            where.append(f"level IN ({', '.join('?' * len(levels))})")
            params += levels
        clause = f"WHERE {' OR '.join(where)}" if where else ""
        rows = self._db.query(
            f"SELECT id, ts, level, kind, rule_id, message FROM activity_log {clause} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        )
        return [
            ActivityEntry(
                r["id"],
                datetime.fromisoformat(r["ts"]),
                EventLevel(r["level"]),
                EventKind(r["kind"]),
                r["rule_id"],
                r["message"],
            )
            for r in rows
        ]

    def prune(self) -> None:
        """Mantém o histórico limitado para o banco não crescer sem fim."""
        self._db.execute(
            "DELETE FROM activity_log WHERE id <= (SELECT MAX(id) FROM activity_log) - ?",
            (self._keep_last,),
        )
        self._writes_since_prune = 0
