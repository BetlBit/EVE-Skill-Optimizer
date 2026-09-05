from app.models import AttributeName, AttributeSet, TrainingTask
from app.remap import optimize_single_remap


def test_int_mem_plan_prefers_int_memory():
    task = TrainingTask(
        skill_id=1, skill_name="X", level=5,
        sp_start=0, sp_end=1_000_000, sp_remaining=1_000_000,
        primary=AttributeName.intelligence, secondary=AttributeName.memory,
        rank=1, plan_names=("P",),
    )
    result = optimize_single_remap([task], implant_bonus=AttributeSet.uniform(0), omega=True)
    assert result.base.intelligence == 27
    assert result.base.memory == 21
    assert result.base.total == 99
