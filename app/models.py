from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable


class AttributeName(str, Enum):
    intelligence = "intelligence"
    memory = "memory"
    perception = "perception"
    willpower = "willpower"
    charisma = "charisma"


@dataclass(frozen=True)
class AttributeSet:
    intelligence: int
    memory: int
    perception: int
    willpower: int
    charisma: int

    def value(self, name: AttributeName) -> int:
        return int(getattr(self, name.value))

    @property
    def total(self) -> int:
        return self.intelligence + self.memory + self.perception + self.willpower + self.charisma

    def plus(self, other: "AttributeSet") -> "AttributeSet":
        return AttributeSet(**{k.value: self.value(k) + other.value(k) for k in AttributeName})

    @classmethod
    def uniform(cls, amount: int) -> "AttributeSet":
        return cls(amount, amount, amount, amount, amount)

    @classmethod
    def zero(cls) -> "AttributeSet":
        return cls.uniform(0)


@dataclass(frozen=True)
class ActiveBooster:
    name: str | None = None
    attribute_bonus: AttributeSet = field(default_factory=AttributeSet.zero)
    expires_at: datetime | None = None
    source: str = "evemon"
    data_unavailable: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillDefinition:
    type_id: int
    name: str
    rank: float
    primary: AttributeName
    secondary: AttributeName
    prerequisites: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class CharacterSkill:
    skill_id: int
    trained_level: int
    active_level: int
    skillpoints: int


@dataclass(frozen=True)
class SkillTarget:
    skill_id: int
    level: int


@dataclass(frozen=True)
class PlanTarget:
    skill_name: str
    level: int


@dataclass
class SkillPlan:
    name: str
    targets: list[PlanTarget] = field(default_factory=list)
    weight: float = 1.0


@dataclass(frozen=True)
class SkillQueueEntry:
    skill_id: int
    level: int
    start_sp: int = 0
    end_sp: int = 0
    start_time: datetime | None = None
    finish_time: datetime | None = None


@dataclass(frozen=True)
class CharacterSnapshot:
    character_id: int
    total_sp: int
    unallocated_sp: int
    attributes: AttributeSet
    skills: dict[int, CharacterSkill]
    implants: tuple[int, ...] = ()
    implant_attribute_bonus: AttributeSet = field(default_factory=AttributeSet.zero)
    booster_attribute_bonus: AttributeSet = field(default_factory=AttributeSet.zero)
    active_boosters: tuple[ActiveBooster, ...] = ()
    bonus_remaps: int | None = None
    last_remap_date: datetime | None = None
    accrued_remap_cooldown_date: datetime | None = None
    character_name: str | None = None
    implant_names: tuple[str, ...] = ()
    evemon_last_timed_respec: datetime | None = None
    skill_queue: tuple[SkillQueueEntry, ...] = ()

    @property
    def base_attributes(self) -> AttributeSet:
        return self.attributes

    @property
    def effective_attributes(self) -> AttributeSet:
        return self.attributes.plus(self.implant_attribute_bonus).plus(self.booster_attribute_bonus)


@dataclass(frozen=True)
class TrainingTask:
    skill_id: int
    skill_name: str
    level: int
    sp_start: int
    sp_end: int
    sp_remaining: int
    primary: AttributeName
    secondary: AttributeName
    rank: float
    plan_names: tuple[str, ...] = ()


def attribute_set_from_dict(data: dict) -> AttributeSet:
    return AttributeSet(
        intelligence=int(data["intelligence"]),
        memory=int(data["memory"]),
        perception=int(data["perception"]),
        willpower=int(data["willpower"]),
        charisma=int(data["charisma"]),
    )


def sum_sp(tasks: Iterable[TrainingTask]) -> int:
    return sum(t.sp_remaining for t in tasks)
