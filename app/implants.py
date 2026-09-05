from __future__ import annotations

from dataclasses import dataclass

from .models import AttributeName, AttributeSet


@dataclass(frozen=True)
class ImplantResolution:
    attribute_bonus: AttributeSet
    unknown_implants: tuple[str, ...]


_IMPLANT_ATTRIBUTES = {
    "ocular filter": AttributeName.perception,
    "memory augmentation": AttributeName.memory,
    "neural boost": AttributeName.willpower,
    "cybernetic subprocessor": AttributeName.intelligence,
    "social adaptation chip": AttributeName.charisma,
}

_IMPLANT_GRADES = {
    "limited": 1,
    "basic": 3,
    "standard": 4,
    "improved": 5,
    "advanced": 5,
}


def _normalize_name(name: str) -> str:
    return " ".join(name.casefold().replace("-", " ").split())


def _resolve_one(name: str) -> tuple[AttributeName, int] | None:
    normalized = _normalize_name(name)
    for implant_name, attribute in _IMPLANT_ATTRIBUTES.items():
        if implant_name not in normalized:
            continue
        for grade, bonus in _IMPLANT_GRADES.items():
            if grade in normalized.split():
                return attribute, bonus
    return None


def resolve_attribute_implants(names: tuple[str, ...] | list[str]) -> ImplantResolution:
    bonuses = {attribute.value: 0 for attribute in AttributeName}
    unknown: list[str] = []

    for raw_name in names:
        name = str(raw_name).strip()
        if not name:
            continue
        resolved = _resolve_one(name)
        if resolved is None:
            unknown.append(name)
            continue
        attribute, bonus = resolved
        bonuses[attribute.value] = max(bonuses[attribute.value], bonus)

    return ImplantResolution(
        attribute_bonus=AttributeSet(**bonuses),
        unknown_implants=tuple(unknown),
    )
