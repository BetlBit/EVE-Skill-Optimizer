from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET

from .config import settings
from .implants import resolve_attribute_implants
from .models import ActiveBooster, AttributeSet, CharacterSkill, CharacterSnapshot, SkillQueueEntry

MAX_EVEMON_FILE_SIZE = 50 * 1024 * 1024


class EvemonImportError(ValueError):
    pass


def _text(parent: ET.Element, name: str, default: str = "") -> str:
    node = parent.find(name)
    if node is None or node.text is None:
        return default
    return node.text.strip()


def _int_text(parent: ET.Element, name: str, default: int = 0) -> int:
    value = _text(parent, name, "")
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise EvemonImportError(f"EVEMon field {name!r} is not an integer: {value!r}") from exc


def _parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value or value.lower() in {"none", "minvalue"}:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return _ensure_utc(datetime.fromisoformat(normalized))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y %H:%M:%S"):
        try:
            return _ensure_utc(datetime.strptime(value, fmt))
        except ValueError:
            continue
    return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _read_xml(path: str | Path) -> tuple[Path, ET.Element]:
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        raise EvemonImportError(f"EVEMon settings file not found: {p}")
    if p.stat().st_size > MAX_EVEMON_FILE_SIZE:
        raise EvemonImportError("EVEMon settings file is unexpectedly large")

    raw = p.read_bytes()
    prefix = raw[:4096].lower()
    # No DTD is expected in EVEMon's Settings serialization. Reject it so the
    # importer cannot be tricked into resolving arbitrary entities.
    if b"<!doctype" in prefix or b"<!entity" in prefix:
        raise EvemonImportError("DOCTYPE/ENTITY is not allowed in EVEMon settings XML")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise EvemonImportError(f"Invalid EVEMon XML: {exc}") from exc

    if root.tag.split("}")[-1] != "Settings":
        raise EvemonImportError("This file does not look like an EVEMon Settings backup")
    return p.resolve(), root


def _character_nodes(root: ET.Element) -> list[ET.Element]:
    characters = root.find("characters")
    if characters is None:
        return []
    return [node for node in characters if node.tag.split("}")[-1] == "ccp"]


def _preview(node: ET.Element) -> dict:
    character_id = _int_text(node, "characterID")
    name = _text(node, "name")
    skills = list(node.findall("./skills/skill"))
    allocated_sp = sum(int(s.attrib.get("skillpoints", "0") or 0) for s in skills)
    return {
        "character_id": character_id,
        "name": name,
        "skills_count": len(skills),
        "allocated_sp": allocated_sp,
        "unallocated_sp": _int_text(node, "freeSkillPoints"),
    }


def list_evemon_characters(path: str | Path) -> list[dict]:
    _, root = _read_xml(path)
    return [_preview(node) for node in _character_nodes(root)]


def _attributes(node: ET.Element) -> AttributeSet:
    attrs = node.find("attributes")
    if attrs is None:
        raise EvemonImportError("Character has no attributes in EVEMon settings")
    return AttributeSet(
        intelligence=_int_text(attrs, "intelligence"),
        memory=_int_text(attrs, "memory"),
        perception=_int_text(attrs, "perception"),
        willpower=_int_text(attrs, "willpower"),
        charisma=_int_text(attrs, "charisma"),
    )


def _booster_attribute_bonus(node: ET.Element) -> AttributeSet:
    attrs = node.find("attributes")
    if attrs is None:
        return AttributeSet.zero()
    amount = _int_text(attrs, "booster", default=0)
    if amount <= 0:
        return AttributeSet.zero()
    return AttributeSet.uniform(amount)


def _active_boosters(node: ET.Element) -> tuple[ActiveBooster, ...]:
    bonus = _booster_attribute_bonus(node)
    if bonus.total == 0:
        return ()
    return (
        ActiveBooster(
            name=None,
            attribute_bonus=bonus,
            expires_at=None,
            source="evemon.attributes.booster",
            data_unavailable=("name", "expires_at"),
        ),
    )


def _skills(node: ET.Element) -> dict[int, CharacterSkill]:
    out: dict[int, CharacterSkill] = {}
    for item in node.findall("./skills/skill"):
        try:
            skill_id = int(item.attrib["typeID"])
            trained_level = int(item.attrib.get("level", 0))
            active_level = int(item.attrib.get("activelevel", trained_level))
            skillpoints = int(item.attrib.get("skillpoints", 0))
        except (KeyError, ValueError) as exc:
            raise EvemonImportError("Malformed EVEMon skill entry") from exc
        out[skill_id] = CharacterSkill(
            skill_id=skill_id,
            trained_level=trained_level,
            active_level=active_level,
            skillpoints=skillpoints,
        )
    return out


def _queue(node: ET.Element) -> tuple[SkillQueueEntry, ...]:
    out: list[SkillQueueEntry] = []
    for item in node.findall("./queue/skill"):
        try:
            skill_id = int(item.attrib["typeID"])
            level = int(item.attrib.get("level", 0))
            start_sp = int(item.attrib.get("startSP", 0) or 0)
            end_sp = int(item.attrib.get("endSP", 0) or 0)
        except (KeyError, ValueError) as exc:
            raise EvemonImportError("Malformed EVEMon queue entry") from exc
        out.append(
            SkillQueueEntry(
                skill_id=skill_id,
                level=level,
                start_sp=start_sp,
                end_sp=end_sp,
                start_time=_parse_dt(item.attrib.get("startTime", "")),
                finish_time=_parse_dt(item.attrib.get("endTime", "")),
            )
        )
    return tuple(out)


def _active_implant_names(node: ET.Element) -> tuple[str, ...]:
    active = node.find("./implants/activeCloneSet")
    if active is None:
        return ()
    names: list[str] = []
    for tag in ("intelligence", "memory", "perception", "willpower", "charisma", "slot6", "slot7", "slot8", "slot9", "slot10"):
        value = _text(active, tag, "")
        if value and value.lower() not in {"none", "0"}:
            names.append(value)
    return tuple(names)


def import_evemon_character(path: str | Path, character_id: int | None = None) -> CharacterSnapshot:
    _, root = _read_xml(path)
    nodes = _character_nodes(root)
    if not nodes:
        raise EvemonImportError("No CCP characters found in EVEMon settings")

    chosen: ET.Element | None = None
    if character_id is not None:
        for node in nodes:
            if _int_text(node, "characterID") == int(character_id):
                chosen = node
                break
        if chosen is None:
            raise EvemonImportError(f"Character {character_id} not found in EVEMon settings")
    elif len(nodes) == 1:
        chosen = nodes[0]
    else:
        ids = ", ".join(str(_int_text(node, "characterID")) for node in nodes)
        raise EvemonImportError(f"More than one character is present; choose character_id. Available: {ids}")

    skills = _skills(chosen)
    implant_names = _active_implant_names(chosen)
    implant_resolution = resolve_attribute_implants(implant_names)
    booster_bonus = _booster_attribute_bonus(chosen)
    return CharacterSnapshot(
        character_id=_int_text(chosen, "characterID"),
        character_name=_text(chosen, "name") or None,
        total_sp=sum(s.skillpoints for s in skills.values()),
        unallocated_sp=_int_text(chosen, "freeSkillPoints"),
        attributes=_attributes(chosen),
        skills=skills,
        implant_names=implant_names,
        implant_attribute_bonus=implant_resolution.attribute_bonus,
        booster_attribute_bonus=booster_bonus,
        active_boosters=_active_boosters(chosen),
        bonus_remaps=_int_text(chosen, "freeRespecs", default=0),
        last_remap_date=_parse_dt(_text(chosen, "lastRespecDate")),
        evemon_last_timed_respec=_parse_dt(_text(chosen, "lastTimedRespec")),
        skill_queue=_queue(chosen),
    )


def snapshot_to_dict(snapshot: CharacterSnapshot) -> dict:
    def iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    return {
        "character_id": snapshot.character_id,
        "character_name": snapshot.character_name,
        "total_sp": snapshot.total_sp,
        "unallocated_sp": snapshot.unallocated_sp,
        "attributes": asdict(snapshot.attributes),
        "base_attributes": asdict(snapshot.base_attributes),
        "implant_attribute_bonus": asdict(snapshot.implant_attribute_bonus),
        "booster_attribute_bonus": asdict(snapshot.booster_attribute_bonus),
        "effective_attributes": asdict(snapshot.effective_attributes),
        "skills": [asdict(s) for s in sorted(snapshot.skills.values(), key=lambda x: x.skill_id)],
        "implants": list(snapshot.implants),
        "implant_names": list(snapshot.implant_names),
        "active_boosters": [
            {
                "name": booster.name,
                "attribute_bonus": asdict(booster.attribute_bonus),
                "expires_at": iso(booster.expires_at),
                "source": booster.source,
                "data_unavailable": list(booster.data_unavailable),
            }
            for booster in snapshot.active_boosters
        ],
        "bonus_remaps": snapshot.bonus_remaps,
        "last_remap_date": iso(snapshot.last_remap_date),
        "accrued_remap_cooldown_date": iso(snapshot.accrued_remap_cooldown_date),
        "evemon_last_timed_respec": iso(snapshot.evemon_last_timed_respec),
        "skill_queue": [
            {
                "skill_id": q.skill_id,
                "level": q.level,
                "start_sp": q.start_sp,
                "end_sp": q.end_sp,
                "start_time": iso(q.start_time),
                "finish_time": iso(q.finish_time),
            }
            for q in snapshot.skill_queue
        ],
    }


def _dict_to_dt(value: str | None) -> datetime | None:
    return _parse_dt(value or "")


def snapshot_from_dict(data: dict) -> CharacterSnapshot:
    skills = {
        int(s["skill_id"]): CharacterSkill(
            skill_id=int(s["skill_id"]),
            trained_level=int(s["trained_level"]),
            active_level=int(s["active_level"]),
            skillpoints=int(s["skillpoints"]),
        )
        for s in data.get("skills", [])
    }
    attrs = data.get("base_attributes") or data["attributes"]
    implant_attrs = data.get("implant_attribute_bonus")
    if implant_attrs is None:
        implant_attrs = resolve_attribute_implants(tuple(str(x) for x in data.get("implant_names", []))).attribute_bonus.__dict__
    booster_attrs = data.get("booster_attribute_bonus") or AttributeSet.zero().__dict__
    active_boosters = tuple(
        ActiveBooster(
            name=item.get("name"),
            attribute_bonus=AttributeSet(**{k: int(v) for k, v in item.get("attribute_bonus", AttributeSet.zero().__dict__).items()}),
            expires_at=_dict_to_dt(item.get("expires_at")),
            source=item.get("source", "evemon"),
            data_unavailable=tuple(str(x) for x in item.get("data_unavailable", [])),
        )
        for item in data.get("active_boosters", [])
    )
    if not active_boosters and AttributeSet(**{k: int(v) for k, v in booster_attrs.items()}).total > 0:
        active_boosters = (
            ActiveBooster(
                attribute_bonus=AttributeSet(**{k: int(v) for k, v in booster_attrs.items()}),
                source="snapshot.booster_attribute_bonus",
                data_unavailable=("name", "expires_at"),
            ),
        )
    queue = tuple(
        SkillQueueEntry(
            skill_id=int(q["skill_id"]),
            level=int(q["level"]),
            start_sp=int(q.get("start_sp", 0)),
            end_sp=int(q.get("end_sp", 0)),
            start_time=_dict_to_dt(q.get("start_time")),
            finish_time=_dict_to_dt(q.get("finish_time")),
        )
        for q in data.get("skill_queue", [])
    )
    return CharacterSnapshot(
        character_id=int(data["character_id"]),
        character_name=data.get("character_name"),
        total_sp=int(data.get("total_sp", 0)),
        unallocated_sp=int(data.get("unallocated_sp", 0)),
        attributes=AttributeSet(**{k: int(v) for k, v in attrs.items()}),
        skills=skills,
        implants=tuple(int(x) for x in data.get("implants", [])),
        implant_attribute_bonus=AttributeSet(**{k: int(v) for k, v in implant_attrs.items()}),
        booster_attribute_bonus=AttributeSet(**{k: int(v) for k, v in booster_attrs.items()}),
        active_boosters=active_boosters,
        implant_names=tuple(str(x) for x in data.get("implant_names", [])),
        bonus_remaps=data.get("bonus_remaps"),
        last_remap_date=_dict_to_dt(data.get("last_remap_date")),
        accrued_remap_cooldown_date=_dict_to_dt(data.get("accrued_remap_cooldown_date")),
        evemon_last_timed_respec=_dict_to_dt(data.get("evemon_last_timed_respec")),
        skill_queue=queue,
    )


def save_snapshot(snapshot: CharacterSnapshot) -> Path:
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    path = settings.runtime_dir / "character_snapshot.json"
    path.write_text(json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_snapshot() -> CharacterSnapshot | None:
    path = settings.runtime_dir / "character_snapshot.json"
    if not path.exists():
        return None
    return snapshot_from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_evemon_source(path: str | Path, character_id: int) -> None:
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    source = {"path": str(Path(path).expanduser().resolve()), "character_id": int(character_id)}
    (settings.runtime_dir / "evemon_source.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_evemon_source() -> dict | None:
    path = settings.runtime_dir / "evemon_source.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
