from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from itertools import product

from .models import AttributeName, AttributeSet, TrainingTask
from .training_math import TimedAttributeModifier, timeline_task_seconds


@dataclass(frozen=True)
class RemapResult:
    base: AttributeSet
    effective: AttributeSet
    seconds: float


def valid_base_remaps():
    # Base total is 99; each attribute is constrained to [17, 27].
    names = list(AttributeName)
    for vals in product(range(17, 28), repeat=4):
        fifth = 99 - sum(vals)
        if 17 <= fifth <= 27:
            d = dict(zip([n.value for n in names[:4]], vals))
            d[names[4].value] = fifth
            yield AttributeSet(**d)


def optimize_single_remap(
    tasks: list[TrainingTask],
    *,
    implant_bonus: AttributeSet | None = None,
    booster_bonus: AttributeSet | None = None,
    timed_modifiers: tuple[TimedAttributeModifier, ...] = (),
    start_time: datetime | None = None,
    omega: bool = True,
) -> RemapResult:
    implant_bonus = implant_bonus or AttributeSet.zero()
    booster_bonus = booster_bonus or AttributeSet.zero()
    additive = implant_bonus.plus(booster_bonus)
    best: RemapResult | None = None
    for base in valid_base_remaps():
        effective = base.plus(additive)
        seconds = timeline_task_seconds(
            tasks,
            base.plus(implant_bonus),
            omega=omega,
            modifiers=timed_modifiers or (TimedAttributeModifier(booster_bonus),),
            start_time=start_time,
        )
        result = RemapResult(base=base, effective=effective, seconds=seconds)
        if best is None or result.seconds < best.seconds:
            best = result
    assert best is not None
    return best


def optimize_single_remap_with_unknown_booster(
    tasks: list[TrainingTask],
    *,
    implant_bonus: AttributeSet,
    booster_bonus: AttributeSet,
    omega: bool = True,
) -> tuple[RemapResult, RemapResult]:
    best_case = optimize_single_remap(tasks, implant_bonus=implant_bonus, booster_bonus=booster_bonus, omega=omega)
    worst_case = optimize_single_remap(tasks, implant_bonus=implant_bonus, omega=omega)
    return best_case, worst_case
