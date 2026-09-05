from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from .models import AttributeName, AttributeSet, SkillDefinition, TrainingTask


@dataclass(frozen=True)
class TimedAttributeModifier:
    attribute_bonus: AttributeSet
    starts_at: datetime | None = None
    expires_at: datetime | None = None


def total_sp_for_level(rank: float, level: int) -> int:
    """Total SP required for a skill at a target level (0..5).

    EVE's published formula is 250 * rank * sqrt(32)^(level-1), rounded
    to the nearest integer. Known rank-1 totals: 250, 1414, 8000, 45255, 256000.
    """
    if level == 0:
        return 0
    if level < 0 or level > 5:
        raise ValueError("level must be between 0 and 5")
    raw = 250.0 * float(rank) * (math.sqrt(32.0) ** (level - 1))
    return int(math.floor(raw + 0.5))


def sp_per_minute(
    attributes: AttributeSet,
    primary,
    secondary,
    *,
    omega: bool = True,
) -> float:
    rate = attributes.value(primary) + attributes.value(secondary) / 2.0
    return rate if omega else rate * 0.5


def training_seconds(
    sp: int,
    attributes: AttributeSet,
    primary,
    secondary,
    *,
    omega: bool = True,
) -> float:
    if sp <= 0:
        return 0.0
    rate = sp_per_minute(attributes, primary, secondary, omega=omega)
    if rate <= 0:
        raise ValueError("training rate must be positive")
    return (sp / rate) * 60.0


def task_seconds(task: TrainingTask, attributes: AttributeSet, *, omega: bool = True) -> float:
    return training_seconds(
        task.sp_remaining,
        attributes,
        task.primary,
        task.secondary,
        omega=omega,
    )


def total_task_seconds(tasks: list[TrainingTask], attributes: AttributeSet, *, omega: bool = True) -> float:
    return sum(task_seconds(task, attributes, omega=omega) for task in tasks)


def timeline_task_seconds(
    tasks: list[TrainingTask],
    base_attributes: AttributeSet,
    *,
    omega: bool = True,
    modifiers: tuple[TimedAttributeModifier, ...] = (),
    start_time: datetime | None = None,
) -> float:
    now = _aware(start_time or datetime.now(timezone.utc))
    total_seconds = 0.0

    for task in tasks:
        remaining_sp = float(task.sp_remaining)
        while remaining_sp > 0:
            active_bonus = _active_bonus(modifiers, now)
            attrs = base_attributes.plus(active_bonus)
            rate_per_second = sp_per_minute(attrs, task.primary, task.secondary, omega=omega) / 60.0
            if rate_per_second <= 0:
                raise ValueError("training rate must be positive")

            next_boundary = _next_modifier_boundary(modifiers, now)
            if next_boundary is None:
                seconds = remaining_sp / rate_per_second
                total_seconds += seconds
                now = now + _seconds_delta(seconds)
                break

            boundary_seconds = max(0.0, (next_boundary - now).total_seconds())
            if boundary_seconds == 0:
                now = next_boundary
                continue

            trained_sp = rate_per_second * boundary_seconds
            if trained_sp >= remaining_sp:
                seconds = remaining_sp / rate_per_second
                total_seconds += seconds
                now = now + _seconds_delta(seconds)
                break

            remaining_sp -= trained_sp
            total_seconds += boundary_seconds
            now = next_boundary

    return total_seconds


def _active_bonus(modifiers: tuple[TimedAttributeModifier, ...], now: datetime) -> AttributeSet:
    totals = {name.value: 0 for name in AttributeName}
    for modifier in modifiers:
        if _modifier_active(modifier, now):
            for name in AttributeName:
                totals[name.value] += modifier.attribute_bonus.value(name)
    return AttributeSet(**totals)


def _modifier_active(modifier: TimedAttributeModifier, now: datetime) -> bool:
    starts_at = _aware(modifier.starts_at) if modifier.starts_at else None
    expires_at = _aware(modifier.expires_at) if modifier.expires_at else None
    if starts_at and now < starts_at:
        return False
    if expires_at and now >= expires_at:
        return False
    return True


def _next_modifier_boundary(modifiers: tuple[TimedAttributeModifier, ...], now: datetime) -> datetime | None:
    candidates: list[datetime] = []
    for modifier in modifiers:
        starts_at = _aware(modifier.starts_at) if modifier.starts_at else None
        expires_at = _aware(modifier.expires_at) if modifier.expires_at else None
        if starts_at and starts_at > now:
            candidates.append(starts_at)
        if expires_at and expires_at > now and _modifier_active(modifier, now):
            candidates.append(expires_at)
    return min(candidates) if candidates else None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _seconds_delta(seconds: float):
    from datetime import timedelta

    return timedelta(seconds=seconds)


def skill_target_remaining_sp(skill: SkillDefinition, current_sp: int, target_level: int) -> int:
    return max(0, total_sp_for_level(skill.rank, target_level) - max(0, current_sp))
