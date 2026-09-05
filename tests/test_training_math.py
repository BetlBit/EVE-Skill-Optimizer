from datetime import datetime, timedelta, timezone

from app.models import AttributeName, AttributeSet, TrainingTask
from app.training_math import TimedAttributeModifier, sp_per_minute, timeline_task_seconds, total_sp_for_level, training_seconds


def test_rank1_sp_totals():
    assert [total_sp_for_level(1, n) for n in range(1, 6)] == [250, 1414, 8000, 45255, 256000]


def test_rank2_is_double():
    assert total_sp_for_level(2, 5) == 512000


def test_omega_training_rate():
    attrs = AttributeSet(intelligence=20, memory=20, perception=20, willpower=20, charisma=19)
    assert sp_per_minute(attrs, AttributeName.intelligence, AttributeName.memory, omega=True) == 30
    assert sp_per_minute(attrs, AttributeName.intelligence, AttributeName.memory, omega=False) == 15


def test_training_time():
    attrs = AttributeSet(intelligence=20, memory=20, perception=20, willpower=20, charisma=19)
    assert training_seconds(1800, attrs, AttributeName.intelligence, AttributeName.memory, omega=True) == 3600


def _task(sp: int) -> TrainingTask:
    return TrainingTask(
        skill_id=1,
        skill_name="X",
        level=1,
        sp_start=0,
        sp_end=sp,
        sp_remaining=sp,
        primary=AttributeName.intelligence,
        secondary=AttributeName.memory,
        rank=1,
    )


def test_plan_finishing_before_expiration_gets_booster_for_whole_plan():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    attrs = AttributeSet(intelligence=20, memory=20, perception=20, willpower=20, charisma=19)
    booster = TimedAttributeModifier(AttributeSet.uniform(10), expires_at=now + timedelta(hours=2))

    assert timeline_task_seconds([_task(2700)], attrs, modifiers=(booster,), start_time=now) == 3600


def test_plan_continuing_after_expiration_is_calculated_piecewise():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    attrs = AttributeSet(intelligence=20, memory=20, perception=20, willpower=20, charisma=19)
    booster = TimedAttributeModifier(AttributeSet.uniform(10), expires_at=now + timedelta(hours=1))

    assert timeline_task_seconds([_task(7200)], attrs, modifiers=(booster,), start_time=now) == 12600


def test_future_expiring_booster_is_used_only_until_expiration():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    attrs = AttributeSet(intelligence=20, memory=20, perception=20, willpower=20, charisma=19)
    booster = TimedAttributeModifier(AttributeSet.uniform(10), expires_at=now + timedelta(minutes=30))

    assert timeline_task_seconds([_task(7200)], attrs, modifiers=(booster,), start_time=now) == 13500


def test_expiration_exactly_at_task_boundary():
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    attrs = AttributeSet(intelligence=20, memory=20, perception=20, willpower=20, charisma=19)
    booster = TimedAttributeModifier(AttributeSet.uniform(10), expires_at=now + timedelta(hours=1))

    assert timeline_task_seconds([_task(2700), _task(1800)], attrs, modifiers=(booster,), start_time=now) == 7200
