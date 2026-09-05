from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app import main
from app.main import ParseRequest, ScheduleRequest
from app.models import ActiveBooster, AttributeName, AttributeSet, CharacterSkill, CharacterSnapshot, SkillDefinition, SkillQueueEntry
from app.scheduler import build_task_graph, optimize_schedule
from app.training_math import total_sp_for_level


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _skill(type_id, name, primary=AttributeName.intelligence, secondary=AttributeName.memory, prereq=(), rank=1):
    return SkillDefinition(type_id, name, rank, primary, secondary, tuple(prereq))


def _install_sde(count_extra: int = 0):
    skills = {
        1: _skill(1, "Shared"),
        2: _skill(2, "High", AttributeName.perception, AttributeName.willpower, ((1, 1),)),
        3: _skill(3, "Low", AttributeName.intelligence, AttributeName.memory, ((1, 1),)),
        4: _skill(4, "Chain"),
        5: _skill(5, "Deep", prereq=((4, 2),)),
        6: _skill(6, "Fast", rank=1),
        7: _skill(7, "Slow", rank=2),
        8: _skill(8, "PerTask", AttributeName.perception, AttributeName.willpower),
        9: _skill(9, "IntTask", AttributeName.intelligence, AttributeName.memory),
    }
    for idx in range(count_extra):
        sid = 100 + idx
        skills[sid] = _skill(sid, f"Extra {idx}", rank=1 + (idx % 3))
    main.sde.skills_by_id = skills
    main.sde.skills_by_name = {s.name.casefold(): s for s in skills.values()}


def _snapshot(**overrides) -> CharacterSnapshot:
    data = {
        "character_id": 1,
        "character_name": "Pilot",
        "total_sp": 0,
        "unallocated_sp": 0,
        "attributes": AttributeSet.uniform(20),
        "skills": {},
        "implant_attribute_bonus": AttributeSet.zero(),
        "booster_attribute_bonus": AttributeSet.zero(),
        "active_boosters": (),
        "skill_queue": (),
    }
    data.update(overrides)
    return CharacterSnapshot(**data)


def _schedule(plans, *, use_imported=False, current_skills=None, objective="weighted_completion_time", respect_current_queue=False):
    return main.schedule(
        ScheduleRequest(
            plans=plans,
            current_skills=current_skills or [],
            use_imported_character=use_imported,
            omega=True,
            objective=objective,
            respect_current_queue=respect_current_queue,
        )
    )


def test_two_independent_plans_higher_priority_finishes_first(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([
        ParseRequest(name="Low", text="Low I", priority=1),
        ParseRequest(name="High", text="High I", priority=10),
    ])

    completed = [row["completes_plans"] for row in result["recommended_schedule"] if row["completes_plans"]]
    assert completed[0] == ["High"]


def test_shared_prerequisite_trained_once_and_sp_accounting(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([
        ParseRequest(name="A", text="High I", priority=1),
        ParseRequest(name="B", text="Low I", priority=1),
    ])

    shared_rows = [row for row in result["recommended_schedule"] if row["skill_name"] == "Shared"]
    assert len(shared_rows) == 1
    assert result["summary"]["shared_tasks"] == 1
    assert result["summary"]["remaining_sp"] < sum(plan["remaining_sp"] for plan in result["plans"])


def test_same_skill_level_chain_is_legal(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([ParseRequest(name="Chain", text="Chain III")])
    assert [(row["skill_name"], row["target_level"]) for row in result["recommended_schedule"]] == [
        ("Chain", 1),
        ("Chain", 2),
        ("Chain", 3),
    ]


def test_prerequisite_chain_is_legal(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([ParseRequest(name="Deep", text="Deep I")])
    assert [(row["skill_name"], row["target_level"]) for row in result["recommended_schedule"]][:3] == [
        ("Chain", 1),
        ("Chain", 2),
        ("Deep", 1),
    ]
    deep = result["recommended_schedule"][2]
    assert deep["task_id"] == "5:1"
    assert deep["prerequisite_task_ids"] == ["4:2"]
    assert deep["prerequisites"] == [{"task_id": "4:2", "skill_id": 4, "skill_name": "Chain", "level": 2}]


def test_already_trained_prerequisite_removed(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule(
        [ParseRequest(name="High", text="High I")],
        current_skills=[{"skill_id": 1, "trained_level": 1, "active_level": 1, "skillpoints": total_sp_for_level(1, 1)}],
    )
    assert [row["skill_name"] for row in result["recommended_schedule"]] == ["High"]


def test_partial_current_sp_preserved_exactly():
    _install_sde()
    graph = build_task_graph(
        [main.parse_plan("Chain V", name="P")],
        {4: CharacterSkill(4, 4, 4, total_sp_for_level(1, 4) + 123)},
        main.sde.skills_by_id,
        main.sde.skills_by_name,
    )
    task = graph.tasks[(4, 5)]
    assert task.sp_start == total_sp_for_level(1, 4) + 123


def test_exact_optimizer_reports_proven_and_known_optimal_order(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([
        ParseRequest(name="Low", text="Low I", priority=1),
        ParseRequest(name="High", text="High I", priority=10),
    ])
    assert result["optimizer"]["algorithm"] == "exact"
    assert result["optimizer"]["optimality"] == "proven"
    assert [row["skill_name"] for row in result["recommended_schedule"][:2]] == ["Shared", "High"]


def test_beam_search_returns_legal_and_deterministic(monkeypatch):
    _install_sde(count_extra=13)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    plans = [ParseRequest(name="Big", text="\n".join(f"Extra {i} I" for i in range(13)))]
    first = _schedule(plans)
    second = _schedule(plans)
    assert first["optimizer"]["algorithm"] == "beam_search"
    assert first["optimizer"]["optimality"] == "heuristic"
    assert first["recommended_schedule"] == second["recommended_schedule"]


def test_plan_completion_timestamps_and_baselines(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([
        ParseRequest(name="Fast", text="Fast I"),
        ParseRequest(name="Slow", text="Slow I"),
    ])
    assert result["plans"][0]["completion_at"] is not None
    assert result["baselines"]["input_plan_order"]["all_plans_completion_seconds"] is not None
    assert result["baselines"]["shortest_available_task"]["all_plans_completion_seconds"] is not None


def test_optimizer_objective_not_worse_than_input_baseline(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([
        ParseRequest(name="Low", text="Low I", priority=1),
        ParseRequest(name="High", text="High I", priority=10),
    ])
    assert result["optimizer"]["objective_value"] <= result["baselines"]["input_plan_order"]["weighted_completion_objective"]


def test_unknown_booster_expiry_returns_exact_false_ranges(monkeypatch):
    _install_sde()
    snapshot = _snapshot(
        booster_attribute_bonus=AttributeSet.uniform(8),
        active_boosters=(ActiveBooster(attribute_bonus=AttributeSet.uniform(8)),),
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([ParseRequest(name="P", text="Chain V")], use_imported=True)
    assert result["exact"] is False
    assert result["optimizer"]["objective_value"] is None
    assert result["range"]["best_case_all_plans_seconds"] < result["range"]["worst_case_all_plans_seconds"]
    assert result["recommended_schedule"]
    first = result["recommended_schedule"][0]
    assert first["duration_seconds"] is None
    assert first["duration_best_case_seconds"] > 0
    assert first["duration_worst_case_seconds"] >= first["duration_best_case_seconds"]
    assert result["plans"][0]["completion_seconds"] is None
    assert result["plans"][0]["completion_best_case_seconds"] > 0


def test_respect_current_queue_false_may_reorder(monkeypatch):
    _install_sde()
    snapshot = _snapshot(
        skill_queue=(SkillQueueEntry(7, 1, 0, total_sp_for_level(2, 1), NOW, NOW + timedelta(hours=1)),)
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([
        ParseRequest(name="Fast", text="Fast I", priority=10),
        ParseRequest(name="Slow", text="Slow I", priority=1),
    ], use_imported=True)
    assert result["recommended_schedule"][0]["skill_name"] == "Fast"


def test_respect_current_queue_true_preserves_active_level(monkeypatch):
    _install_sde()
    snapshot = _snapshot(
        skill_queue=(SkillQueueEntry(7, 1, 0, total_sp_for_level(2, 1), NOW, NOW + timedelta(hours=1)),)
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([
        ParseRequest(name="Fast", text="Fast I", priority=10),
        ParseRequest(name="Slow", text="Slow I", priority=1),
    ], use_imported=True, respect_current_queue=True)
    assert result["recommended_schedule"][0]["skill_name"] == "Slow"


def test_manual_and_imported_modes_compatible(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    manual = _schedule([ParseRequest(name="P", text="Fast I")])
    monkeypatch.setattr(main, "load_snapshot", lambda: _snapshot())
    imported = _schedule([ParseRequest(name="P", text="Fast I")], use_imported=True)
    assert manual["character_source"] == "request"
    assert imported["character_source"] == "evemon"


def test_invalid_priority_and_unknown_skill_rejected(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    with pytest.raises(HTTPException):
        _schedule([ParseRequest(name="Bad", text="Fast I", priority=0)])
    with pytest.raises(HTTPException):
        _schedule([ParseRequest(name="Bad", text="Missing I")])


def test_duplicate_skill_targets_across_plans_dedupe(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([
        ParseRequest(name="A", text="Fast I"),
        ParseRequest(name="B", text="Fast I"),
    ])
    assert result["summary"]["unique_tasks"] == 1


def test_graph_validation_rejects_missing_prerequisite():
    _install_sde()
    broken = dict(main.sde.skills_by_id)
    broken[10] = _skill(10, "Broken", prereq=((999, 1),))
    by_name = {s.name.casefold(): s for s in broken.values()}
    with pytest.raises(KeyError):
        build_task_graph([main.parse_plan("Broken I", name="Bad")], {}, broken, by_name)


def test_graph_validation_rejects_cycle():
    skills = {
        1: _skill(1, "A", prereq=((2, 1),)),
        2: _skill(2, "B", prereq=((1, 1),)),
    }
    by_name = {s.name.casefold(): s for s in skills.values()}
    with pytest.raises(RuntimeError):
        build_task_graph([main.parse_plan("A I", name="Bad")], {}, skills, by_name)


def test_makespan_objective_supported(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([ParseRequest(name="A", text="Fast I"), ParseRequest(name="B", text="Slow I")], objective="makespan")
    assert result["objective"] == "makespan"
    assert result["optimizer"]["objective_value"] == result["baselines"]["shortest_available_task"]["all_plans_completion_seconds"]


def test_priority_lexicographic_objective_supported(monkeypatch):
    _install_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([
        ParseRequest(name="Low", text="Low I", priority=1),
        ParseRequest(name="High", text="High I", priority=10),
    ], objective="priority_lexicographic")
    assert result["objective"] == "priority_lexicographic"
    assert ["High"] in [row["completes_plans"] for row in result["recommended_schedule"]]


def test_temporary_booster_expiry_can_make_ordering_matter(monkeypatch):
    _install_sde()
    snapshot = _snapshot(
        booster_attribute_bonus=AttributeSet(intelligence=100, memory=100, perception=0, willpower=0, charisma=0),
        active_boosters=(
            ActiveBooster(
                attribute_bonus=AttributeSet(intelligence=100, memory=100, perception=0, willpower=0, charisma=0),
                expires_at=NOW + timedelta(seconds=400),
            ),
        ),
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = _schedule([
        ParseRequest(name="Per", text="PerTask I", priority=1),
        ParseRequest(name="Int", text="IntTask I", priority=1),
    ], use_imported=True)
    assert result["recommended_schedule"][0]["skill_name"] == "IntTask"


def test_ignore_unknown_booster_makes_schedule_exact(monkeypatch):
    _install_sde()
    snapshot = _snapshot(
        booster_attribute_bonus=AttributeSet.uniform(8),
        active_boosters=(ActiveBooster(attribute_bonus=AttributeSet.uniform(8)),),
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = main.schedule(ScheduleRequest(
        plans=[ParseRequest(name="P", text="Chain V")],
        use_imported_character=True,
        omega=True,
        ignore_active_booster=True,
    ))
    assert result["exact"] is True
    assert result["boosters"]["ignored"] is True
    assert result["boosters"]["expiry_source"] == "ignored_by_user"
    assert all(value == 0 for value in result["boosters"]["attribute_bonus"].values())
    assert result["summary"]["remaining_sp"] > 0
    assert sum(row["duration_seconds"] for row in result["recommended_schedule"]) > 0


def test_manual_remaining_time_and_strength_make_schedule_exact_without_evemon_booster_strength(monkeypatch):
    _install_sde()
    snapshot = _snapshot(booster_attribute_bonus=AttributeSet.zero(), active_boosters=())
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = main.schedule(ScheduleRequest(
        plans=[ParseRequest(name="P", text="Chain V")],
        use_imported_character=True,
        omega=True,
        booster_expires_at_override=NOW + timedelta(hours=671, minutes=43, seconds=20),
        booster_attribute_bonus_override=8,
    ))

    assert result["exact"] is True
    assert result["boosters"]["attribute_bonus"]["intelligence"] == 8
    assert result["boosters"]["manual_attribute_bonus_override"] == 8
    assert sum(row["duration_seconds"] for row in result["recommended_schedule"]) > 0
