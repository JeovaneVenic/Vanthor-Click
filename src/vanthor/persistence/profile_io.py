"""Exportar, importar e duplicar perfis (zonas + automações) em JSON.

Formato versionado e independente do banco: as automações apontam para zonas por uma referência
do próprio arquivo ("z1"), nunca por id interno. Na importação NADA do arquivo é confiável:
tudo passa pelas mesmas validações do editor, dentro de uma transação (tudo ou nada).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from vanthor import __version__
from vanthor.actions.models import ActionSpec
from vanthor.capture.geometry import ScreenRect
from vanthor.capture.zones import MAX_INTERVAL_MS, MIN_INTERVAL_MS, ZONE_COLORS
from vanthor.persistence.database import Database
from vanthor.persistence.repositories import (
    PROFILE_NAME_MAX,
    Profile,
    ProfileRepository,
    RuleRepository,
    ZoneRepository,
)
from vanthor.rules.models import RuleStatus, TriggerSettings
from vanthor.vision.color import ColorCondition

FORMAT: Final = "vanthor-bot-click/profile"
FORMAT_VERSION: Final = 1
MAX_FILE_BYTES: Final = 2_000_000
MAX_ZONES: Final = 200
MAX_RULES: Final = 500
_COORD_LIMIT = 100_000
_SIDE_LIMIT = 20_000
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ProfileFileError(ValueError):
    """Arquivo de perfil inválido. A mensagem diz onde e o quê, em português."""


@dataclass(frozen=True, slots=True)
class ImportReport:
    profile: Profile
    zones: int
    rules: int
    zone_rects: tuple[ScreenRect, ...]


# -- exportar ------------------------------------------------------------------------------------
def export_profile(db: Database, profile_id: int) -> dict[str, Any]:
    profiles, zones_repo, rules_repo = ProfileRepository(db), ZoneRepository(db), RuleRepository(db)
    profile = profiles.get(profile_id)
    if profile is None:
        raise ProfileFileError("Perfil não encontrado")
    zones = zones_repo.list(profile_id)
    refs = {zone.id: f"z{index}" for index, zone in enumerate(zones, start=1)}
    zone_items = []
    for zone in zones:
        draft = zones_repo.color_draft(zone.id)
        zone_items.append(
            {
                "ref": refs[zone.id],
                "name": zone.name,
                "color": zone.color,
                "rect": {"x": zone.rect.x, "y": zone.rect.y, "width": zone.rect.width, "height": zone.rect.height},
                "monitor": zone.monitor,
                "interval_ms": zone.interval_ms,
                "enabled": zone.enabled,
                "color_draft": draft.to_dict() if draft else None,
            }
        )
    rule_items = []
    for rule in rules_repo.list(profile_id):
        config = rule.config_dict()
        rule_items.append(
            {
                "name": rule.name,
                "status": rule.status.value,
                "zone": refs.get(rule.zone_id) if rule.zone_id is not None else None,
                "condition": config["condition"],
                "trigger": config["trigger"],
                "action": config["action"],
            }
        )
    return {
        "format": FORMAT,
        "version": FORMAT_VERSION,
        "app_version": __version__,
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "profile": {"name": profile.name},
        "zones": zone_items,
        "rules": rule_items,
    }


def to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


# -- importar ------------------------------------------------------------------------------------
def parse_profile_file(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_FILE_BYTES:
        raise ProfileFileError("Arquivo grande demais para um perfil (máximo 2 MB)")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileFileError("O arquivo não é um JSON válido") from exc
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise ProfileFileError("Este arquivo não é um perfil do Vanthor Bot Click")
    if data.get("version") != FORMAT_VERSION:
        raise ProfileFileError(
            f"Perfil feito numa versão diferente do formato ({data.get('version')!r}). Atualize o aplicativo."
        )
    return data


def import_profile(
    db: Database,
    data: dict[str, Any],
    name: str | None = None,
    keep_status: bool = False,
) -> ImportReport:
    """Cria um perfil NOVO com o conteúdo de ``data``.

    ``keep_status=False`` (importação de arquivo): toda automação chega PAUSADA, para o usuário
    revisar zonas e pontos de clique antes que algo aja no computador dele.
    """
    zones_in = _list(data, "zones", MAX_ZONES)
    rules_in = _list(data, "rules", MAX_RULES)
    profile_info = data.get("profile")
    base_name = name or (profile_info.get("name") if isinstance(profile_info, dict) else None) or "Perfil importado"
    if not isinstance(base_name, str):
        raise ProfileFileError("perfil.nome precisa ser um texto")

    # Valida TUDO antes de gravar qualquer coisa.
    zones = [_parse_zone(item, i) for i, item in enumerate(zones_in)]
    refs = [z["ref"] for z in zones]
    if len(set(refs)) != len(refs):
        raise ProfileFileError("Há zonas com a mesma referência no arquivo")
    rules = [_parse_rule(item, i, set(refs)) for i, item in enumerate(rules_in)]

    profiles, zone_repo, rule_repo = ProfileRepository(db), ZoneRepository(db), RuleRepository(db)
    with db.transaction():
        profile = profiles.create(profiles.unique_name(base_name[:PROFILE_NAME_MAX]))
        ids: dict[str, int] = {}
        rects: list[ScreenRect] = []
        for zone in zones:
            created = zone_repo.create(profile.id, zone["rect"], zone["monitor"], zone["interval_ms"])
            zone_repo.update(
                created.with_changes(name=zone["name"], color=zone["color"], enabled=zone["enabled"])
            )
            if zone["color_draft"] is not None:
                zone_repo.save_color_draft(created.id, zone["color_draft"])
            ids[zone["ref"]] = created.id
            rects.append(zone["rect"])
        for rule in rules:
            # Regra "sem zona" continua sem zona: aparece como "Sem zona" e não roda até ser editada.
            zone_id = ids.get(rule["zone"]) if rule["zone"] else None
            status = rule["status"] if keep_status else RuleStatus.PAUSED
            rule_repo.create(
                profile.id, rule["name"], zone_id, rule["condition"], rule["trigger"], rule["action"], status
            )
    return ImportReport(profile, len(zones), len(rules), tuple(rects))


def duplicate_profile(db: Database, profile_id: int, name: str | None = None) -> Profile:
    data = export_profile(db, profile_id)
    base = name or f"{data['profile']['name']} (cópia)"
    return import_profile(db, data, name=base, keep_status=True).profile


# -- validação item a item -------------------------------------------------------------------------
def _list(data: dict[str, Any], key: str, limit: int) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise ProfileFileError(f"{key} precisa ser uma lista")
    if len(value) > limit:
        raise ProfileFileError(f"O arquivo tem {len(value)} {key}; o máximo é {limit}")
    return value


def _text(item: dict[str, Any], key: str, where: str, max_len: int, required: bool = True) -> str:
    value = item.get(key)
    if not isinstance(value, str) or (required and not value.strip()):
        raise ProfileFileError(f"{where}.{key}: precisa ser um texto")
    if len(value) > max_len:
        raise ProfileFileError(f"{where}.{key}: texto longo demais (máximo {max_len})")
    return value.strip()


def _int(value: Any, where: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ProfileFileError(f"{where}: número fora do permitido ({low} a {high})")
    return value


def _parse_zone(item: Any, index: int) -> dict[str, Any]:
    where = f"zonas[{index + 1}]"
    if not isinstance(item, dict):
        raise ProfileFileError(f"{where}: precisa ser um objeto")
    rect = item.get("rect")
    if not isinstance(rect, dict):
        raise ProfileFileError(f"{where}.rect: precisa ser um objeto")
    color = item.get("color")
    draft = item.get("color_draft")
    try:
        parsed_draft = ColorCondition.from_dict(draft) if isinstance(draft, dict) else None
    except ValueError as exc:
        raise ProfileFileError(f"{where}.ajuste_de_cor: {exc}") from exc
    if draft is not None and not isinstance(draft, dict):
        raise ProfileFileError(f"{where}.ajuste_de_cor: precisa ser um objeto")
    enabled = item.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ProfileFileError(f"{where}.enabled: precisa ser verdadeiro ou falso")
    return {
        "ref": _text(item, "ref", where, 40),
        "name": _text(item, "name", where, 60),
        "color": color.upper() if isinstance(color, str) and _HEX_COLOR.match(color) else ZONE_COLORS[index % len(ZONE_COLORS)],
        "rect": ScreenRect(
            _int(rect.get("x"), f"{where}.rect.x", -_COORD_LIMIT, _COORD_LIMIT),
            _int(rect.get("y"), f"{where}.rect.y", -_COORD_LIMIT, _COORD_LIMIT),
            _int(rect.get("width"), f"{where}.rect.width", 1, _SIDE_LIMIT),
            _int(rect.get("height"), f"{where}.rect.height", 1, _SIDE_LIMIT),
        ),
        "monitor": _text(item, "monitor", where, 64, required=False),
        "interval_ms": _int(item.get("interval_ms", 100), f"{where}.interval_ms", MIN_INTERVAL_MS, MAX_INTERVAL_MS),
        "enabled": enabled,
        "color_draft": parsed_draft,
    }


def _parse_rule(item: Any, index: int, refs: set[str]) -> dict[str, Any]:
    where = f"automações[{index + 1}]"
    if not isinstance(item, dict):
        raise ProfileFileError(f"{where}: precisa ser um objeto")
    zone = item.get("zone")
    if zone is not None and (not isinstance(zone, str) or zone not in refs):
        raise ProfileFileError(f"{where}.zone: aponta para uma zona que não está no arquivo")
    try:
        condition = ColorCondition.from_dict(item.get("condition"))  # type: ignore[arg-type]
        trigger = TriggerSettings.from_dict(item.get("trigger") or {})
        action = ActionSpec.from_dict(item.get("action") or {})
        status = RuleStatus(item.get("status", RuleStatus.ACTIVE.value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ProfileFileError(f"{where}: {exc}") from exc
    return {
        "name": _text(item, "name", where, 60),
        "zone": zone,
        "condition": condition,
        "trigger": trigger,
        "action": action,
        "status": status,
    }
