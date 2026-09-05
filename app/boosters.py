from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from .models import AttributeName, AttributeSet, CharacterSkill, SkillDefinition, SkillQueueEntry, TrainingTask
from .training_math import TimedAttributeModifier, timeline_task_seconds

DEFAULT_INFERENCE_TOLERANCE_SECONDS = 180.0


@dataclass(frozen=True)
class BoosterExpiryInference:
    expires_at: datetime | None
    source: str | None
    confidence: str | None
    evidence: dict
    warnings: tuple[str, ...] = ()

    @property
    def exact(self) -> bool:
        return self.expires_at is not None


def infer_attribute_booster_expiry(
    *,
    now: datetime,
    current_skills: Mapping[int, CharacterSkill],
    queue: tuple[SkillQueueEntry, ...],
    base_attributes: AttributeSet,
    implant_bonus: AttributeSet,
    booster_bonus: AttributeSet,
    skill_catalog: Mapping[int, SkillDefinition],
    omega: bool = True,
    tolerance_seconds: float = DEFAULT_INFERENCE_TOLERANCE_SECONDS,
) -> BoosterExpiryInference:
    now = _as_utc(now)
    if booster_bonus.total <= 0:
        return BoosterExpiryInference(None, None, None, {"reason": "no_active_booster"})

    prepared = _queue_tasks_from_now(now, current_skills, queue, skill_catalog)
    if len(prepared) < 1:
        return BoosterExpiryInference(
            None,
            None,
            None,
            {"reason": "insufficient_queue_data", "queue_items_used": 0},
            ("Active booster bonus is known, but EVEMon queue data is insufficient to infer expiration.",),
        )

    base_with_implants = base_attributes.plus(implant_bonus)
    forever_mod = (TimedAttributeModifier(booster_bonus),)
    no_booster_mod: tuple[TimedAttributeModifier, ...] = ()
    observed = [(finish - now).total_seconds() for _, finish in prepared]
    tasks = [task for task, _ in prepared]
    forever = _cumulative_durations(tasks, base_with_implants, forever_mod, now, omega)
    no_booster = _cumulative_durations(tasks, base_with_implants, no_booster_mod, now, omega)

    usable_indices = [
        i
        for i, obs in enumerate(observed)
        if forever[i] + tolerance_seconds < obs < no_booster[i] - tolerance_seconds
    ]
    if not usable_indices:
        return BoosterExpiryInference(
            None,
            None,
            None,
            {
                "reason": "no_unique_solution",
                "queue_items_used": len(prepared),
                "observed_within_permanent_or_no_booster_bounds": True,
            },
            ("Active booster expiration could not be uniquely inferred from EVEMon queue timestamps.",),
        )

    target_idx = usable_indices[-1]
    candidate_elapsed = _solve_expiry_elapsed(
        tasks[: target_idx + 1],
        observed[target_idx],
        base_with_implants,
        booster_bonus,
        now,
        omega,
    )
    if candidate_elapsed is None or candidate_elapsed < 0:
        return BoosterExpiryInference(
            None,
            None,
            None,
            {"reason": "no_unique_solution", "queue_items_used": len(prepared)},
            ("Active booster expiration could not be solved from EVEMon queue timestamps.",),
        )

    expires_at = now + _seconds_delta(candidate_elapsed)
    if expires_at <= now:
        return BoosterExpiryInference(
            None,
            None,
            None,
            {"reason": "inferred_expiry_before_now", "queue_items_used": len(prepared)},
            ("Inferred booster expiration is not in the future.",),
        )

    candidate_mod = (TimedAttributeModifier(booster_bonus, expires_at=expires_at),)
    predicted = _cumulative_durations(tasks, base_with_implants, candidate_mod, now, omega)
    residuals = [abs(pred - obs) for pred, obs in zip(predicted, observed)]
    max_residual = max(residuals) if residuals else 0.0
    used = len(prepared)
    if max_residual > tolerance_seconds:
        return BoosterExpiryInference(
            None,
            None,
            None,
            {
                "reason": "residual_too_high",
                "queue_items_used": used,
                "max_residual_seconds": max_residual,
                "tolerance_seconds": tolerance_seconds,
                "candidate_expires_at": expires_at.isoformat(),
            },
            ("EVEMon queue timestamps are inconsistent with a single booster expiration.",),
        )

    confidence = "high" if used >= 2 and max_residual <= 60.0 else "medium"
    return BoosterExpiryInference(
        expires_at,
        "inferred_from_evemon_skill_queue",
        confidence,
        {
            "queue_items_used": used,
            "max_residual_seconds": max_residual,
            "tolerance_seconds": tolerance_seconds,
            "method": "piecewise_training_rate_fit",
        },
    )


def _queue_tasks_from_now(
    now: datetime,
    current_skills: Mapping[int, CharacterSkill],
    queue: tuple[SkillQueueEntry, ...],
    skill_catalog: Mapping[int, SkillDefinition],
) -> list[tuple[TrainingTask, datetime]]:
    out: list[tuple[TrainingTask, datetime]] = []
    for entry in queue:
        if entry.finish_time is None:
            continue
        finish = _as_utc(entry.finish_time)
        if finish <= now:
            continue
        skill = skill_catalog.get(entry.skill_id)
        if skill is None:
            continue
        start = _as_utc(entry.start_time) if entry.start_time else None
        start_sp = entry.start_sp
        if start is not None and start <= now < finish:
            current = current_skills.get(entry.skill_id)
            if current is not None:
                start_sp = max(start_sp, min(current.skillpoints, entry.end_sp))
        elif start is not None and now < start:
            start_sp = entry.start_sp
        elif start is None and not out:
            current = current_skills.get(entry.skill_id)
            if current is not None:
                start_sp = max(start_sp, min(current.skillpoints, entry.end_sp))
        remaining = max(0, entry.end_sp - start_sp)
        if remaining == 0:
            continue
        out.append(
            (
                TrainingTask(
                    skill_id=entry.skill_id,
                    skill_name=skill.name,
                    level=entry.level,
                    sp_start=start_sp,
                    sp_end=entry.end_sp,
                    sp_remaining=remaining,
                    primary=skill.primary,
                    secondary=skill.secondary,
                    rank=skill.rank,
                    plan_names=("EVEMon queue",),
                ),
                finish,
            )
        )
    return out


def _cumulative_durations(
    tasks: list[TrainingTask],
    base_attributes: AttributeSet,
    modifiers: tuple[TimedAttributeModifier, ...],
    now: datetime,
    omega: bool,
) -> list[float]:
    out: list[float] = []
    elapsed = 0.0
    current = now
    for task in tasks:
        seconds = timeline_task_seconds([task], base_attributes, omega=omega, modifiers=modifiers, start_time=current)
        elapsed += seconds
        current = current + _seconds_delta(seconds)
        out.append(elapsed)
    return out


def _solve_expiry_elapsed(
    tasks: list[TrainingTask],
    target_elapsed: float,
    base_attributes: AttributeSet,
    booster_bonus: AttributeSet,
    now: datetime,
    omega: bool,
) -> float | None:
    low = 0.0
    high = target_elapsed
    for _ in range(80):
        mid = (low + high) / 2.0
        expires_at = now + _seconds_delta(mid)
        duration = timeline_task_seconds(
            tasks,
            base_attributes,
            omega=omega,
            modifiers=(TimedAttributeModifier(booster_bonus, expires_at=expires_at),),
            start_time=now,
        )
        if abs(duration - target_elapsed) < 0.001:
            return mid
        if duration > target_elapsed:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _seconds_delta(seconds: float):
    from datetime import timedelta

    return timedelta(seconds=seconds)
