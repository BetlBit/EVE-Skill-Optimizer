from datetime import datetime, timedelta, timezone

from app.boosters import infer_attribute_booster_expiry
from app.models import AttributeName, AttributeSet, CharacterSkill, SkillDefinition, SkillQueueEntry


NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _skills():
    return {
        1: SkillDefinition(1, "IntMem", 1, AttributeName.intelligence, AttributeName.memory),
        2: SkillDefinition(2, "PerWil", 1, AttributeName.perception, AttributeName.willpower),
    }


def _queue_item(skill_id: int, sp_start: int, sp_end: int, finish_after: float, *, start_after: float = 0, level: int = 1):
    return SkillQueueEntry(
        skill_id=skill_id,
        level=level,
        start_sp=sp_start,
        end_sp=sp_end,
        start_time=NOW + timedelta(seconds=start_after),
        finish_time=NOW + timedelta(seconds=finish_after),
    )


def _seconds_from_expected(value, expected) -> float:
    return abs((value - expected).total_seconds())


def test_queue_inference_from_one_skill_with_known_solution():
    result = infer_attribute_booster_expiry(
        now=NOW,
        current_skills={1: CharacterSkill(1, 0, 0, 0)},
        queue=(_queue_item(1, 0, 7200, 12600),),
        base_attributes=AttributeSet.uniform(20),
        implant_bonus=AttributeSet.zero(),
        booster_bonus=AttributeSet.uniform(10),
        skill_catalog=_skills(),
    )

    assert _seconds_from_expected(result.expires_at, NOW + timedelta(hours=1)) < 0.01
    assert result.source == "inferred_from_evemon_skill_queue"
    assert result.evidence["max_residual_seconds"] < 1


def test_queue_inference_validated_by_multiple_skill_rows():
    result = infer_attribute_booster_expiry(
        now=NOW,
        current_skills={1: CharacterSkill(1, 0, 0, 0)},
        queue=(
            _queue_item(1, 0, 7200, 12600),
            _queue_item(2, 0, 3600, 19800, start_after=12600),
        ),
        base_attributes=AttributeSet.uniform(20),
        implant_bonus=AttributeSet.zero(),
        booster_bonus=AttributeSet.uniform(10),
        skill_catalog=_skills(),
    )

    assert _seconds_from_expected(result.expires_at, NOW + timedelta(hours=1)) < 0.01
    assert result.confidence == "high"
    assert result.evidence["queue_items_used"] == 2


def test_current_active_skill_uses_current_snapshot_sp_not_stale_queue_start_sp():
    result = infer_attribute_booster_expiry(
        now=NOW,
        current_skills={1: CharacterSkill(1, 0, 0, 1000)},
        queue=(_queue_item(1, 0, 7200, 10600),),
        base_attributes=AttributeSet.uniform(20),
        implant_bonus=AttributeSet.zero(),
        booster_bonus=AttributeSet.uniform(10),
        skill_catalog=_skills(),
    )

    assert _seconds_from_expected(result.expires_at, NOW + timedelta(hours=1)) < 0.01
    assert result.exact


def test_inferred_expiration_reproduces_queue_finish_within_tolerance():
    result = infer_attribute_booster_expiry(
        now=NOW,
        current_skills={1: CharacterSkill(1, 0, 0, 0)},
        queue=(_queue_item(1, 0, 7200, 12630),),
        base_attributes=AttributeSet.uniform(20),
        implant_bonus=AttributeSet.zero(),
        booster_bonus=AttributeSet.uniform(10),
        skill_catalog=_skills(),
    )

    assert result.exact
    assert result.evidence["max_residual_seconds"] <= result.evidence["tolerance_seconds"]


def test_inconsistent_queue_causes_inference_rejection():
    result = infer_attribute_booster_expiry(
        now=NOW,
        current_skills={1: CharacterSkill(1, 0, 0, 0)},
        queue=(
            _queue_item(1, 0, 7200, 12600),
            _queue_item(2, 0, 3600, 22000, start_after=12600),
        ),
        base_attributes=AttributeSet.uniform(20),
        implant_bonus=AttributeSet.zero(),
        booster_bonus=AttributeSet.uniform(10),
        skill_catalog=_skills(),
    )

    assert not result.exact
    assert result.evidence["reason"] == "residual_too_high"


def test_insufficient_queue_data_returns_unknown():
    result = infer_attribute_booster_expiry(
        now=NOW,
        current_skills={},
        queue=(),
        base_attributes=AttributeSet.uniform(20),
        implant_bonus=AttributeSet.zero(),
        booster_bonus=AttributeSet.uniform(10),
        skill_catalog=_skills(),
    )

    assert not result.exact
    assert result.evidence["reason"] == "insufficient_queue_data"
