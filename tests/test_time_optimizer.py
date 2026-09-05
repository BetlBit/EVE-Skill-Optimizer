from datetime import datetime, timedelta, timezone

from app import main
from app.main import OptimizeTrainingRequest, ParseRequest
from app.models import ActiveBooster, AttributeName, AttributeSet, CharacterSnapshot, SkillDefinition
from app.remap import valid_base_remaps
from app.scheduler import build_task_graph
from app.time_optimizer import candidate_remaps, remap_availability
from app.training_math import total_sp_for_level

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _skill(type_id, name, primary=AttributeName.intelligence, secondary=AttributeName.memory, prereq=(), rank=1):
    return SkillDefinition(type_id, name, rank, primary, secondary, tuple(prereq))


def _install_sde(monkeypatch):
    skills = {
        1: _skill(1, "IntMem", AttributeName.intelligence, AttributeName.memory),
        2: _skill(2, "PerWil", AttributeName.perception, AttributeName.willpower),
        3411: _skill(3411, "Cybernetics", AttributeName.intelligence, AttributeName.memory),
    }
    main.sde.skills_by_id = skills
    main.sde.skills_by_name = {s.name.casefold(): s for s in skills.values()}
    monkeypatch.setattr(main.sde, "cybernetics_level_for_attribute_implants", lambda bonus: {3: 1, 4: 4, 5: 5}[bonus])


def _snapshot(**overrides):
    data = {
        "character_id": 1,
        "character_name": "Pilot",
        "total_sp": 0,
        "unallocated_sp": 0,
        "attributes": AttributeSet(intelligence=17, memory=17, perception=27, willpower=21, charisma=17),
        "skills": {},
        "implant_attribute_bonus": AttributeSet.zero(),
        "booster_attribute_bonus": AttributeSet.zero(),
        "bonus_remaps": 0,
    }
    data.update(overrides)
    return CharacterSnapshot(**data)


def _request(scenarios=("current",), use_imported=True):
    return OptimizeTrainingRequest(
        plans=[ParseRequest(name="P", text="IntMem I")],
        use_imported_character=use_imported,
        omega=True,
        implant_scenarios=list(scenarios),
    )


def test_valid_remap_space_respects_eve_attribute_bounds():
    maps = list(valid_base_remaps())
    assert maps
    assert all(attrs.total == 99 for attrs in maps)
    assert all(17 <= attrs.value(name) <= 27 for attrs in maps for name in AttributeName)


def test_remap_availability_uses_accrued_cooldown_date():
    snapshot = _snapshot(accrued_remap_cooldown_date=NOW - timedelta(days=1), bonus_remaps=2)
    availability = remap_availability(snapshot, NOW)
    assert availability.timed_available_now is True
    assert availability.bonus_remaps == 2
    assert availability.source == "evemon_accrued_remap_cooldown_date"


def test_remap_availability_derives_last_timed_respec_plus_year():
    snapshot = _snapshot(evemon_last_timed_respec=NOW - timedelta(days=100), bonus_remaps=1)
    availability = remap_availability(snapshot, NOW)
    assert availability.timed_available_now is False
    assert availability.timed_available_at == NOW + timedelta(days=265)


def test_candidate_remaps_include_current_and_are_deterministic(monkeypatch):
    _install_sde(monkeypatch)
    graph = build_task_graph([main.parse_plan("IntMem I", name="P")], {}, main.sde.skills_by_id, main.sde.skills_by_name)
    current = AttributeSet(intelligence=17, memory=17, perception=27, willpower=21, charisma=17)
    first = candidate_remaps(graph, current, max_candidate_remaps=8)
    second = candidate_remaps(graph, current, max_candidate_remaps=8)
    assert first == second
    assert first[0] == current
    assert all(attrs.total == 99 for attrs in first)


def test_optimize_training_endpoint_reports_no_changes_and_scenarios(monkeypatch):
    _install_sde(monkeypatch)
    monkeypatch.setattr(main, "load_snapshot", lambda: _snapshot())
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = main.optimize_training(_request(("current", "none", "+3")))
    assert result["character_source"] == "evemon"
    assert result["no_changes"]["optimizer"]["objective_value"] is not None
    assert [row["scenario"] for row in result["scenarios"]] == ["current", "none", "+3"]
    assert result["recommended"]["scenario"] in {"current", "none", "+3"}


def test_current_scenario_preserves_current_implants(monkeypatch):
    _install_sde(monkeypatch)
    snapshot = _snapshot(implant_attribute_bonus=AttributeSet.uniform(5))
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = main.optimize_training(_request(("current",)))
    assert result["scenarios"][0]["implant_attribute_bonus"]["intelligence"] == 5


def test_timed_remap_is_consumed_before_bonus_remap(monkeypatch):
    _install_sde(monkeypatch)
    snapshot = _snapshot(accrued_remap_cooldown_date=NOW - timedelta(seconds=1), bonus_remaps=3)
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = main.optimize_training(_request(("current",)))
    assert result["scenarios"][0]["remap_actions"]
    assert result["scenarios"][0]["remap_actions"][0]["kind"] == "timed"


def test_bonus_remap_used_when_timed_is_not_available(monkeypatch):
    _install_sde(monkeypatch)
    snapshot = _snapshot(accrued_remap_cooldown_date=NOW + timedelta(days=20), bonus_remaps=1)
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = main.optimize_training(_request(("current",)))
    assert result["scenarios"][0]["remap_actions"][0]["kind"] == "bonus"


def test_no_remap_action_when_no_remaps_available(monkeypatch):
    _install_sde(monkeypatch)
    snapshot = _snapshot(accrued_remap_cooldown_date=NOW + timedelta(days=20), bonus_remaps=0)
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = main.optimize_training(_request(("current",)))
    assert result["scenarios"][0]["remap_actions"] == []


def test_implant_scenario_adds_cybernetics_prep_but_not_user_plan(monkeypatch):
    _install_sde(monkeypatch)
    monkeypatch.setattr(main, "load_snapshot", lambda: _snapshot())
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = main.optimize_training(_request(("+5",)))
    names = [row["skill_name"] for row in result["scenarios"][0]["schedule"]]
    assert "Cybernetics" in names
    assert [plan["name"] for plan in result["scenarios"][0]["plans"]] == ["P"]
    assert result["scenarios"][0]["required_cybernetics_level"] == 5


def test_implant_scenario_skips_cybernetics_when_already_trained(monkeypatch):
    _install_sde(monkeypatch)
    snapshot = _snapshot(
        skills={3411: main.CharacterSkill(3411, 5, 5, total_sp_for_level(1, 5))}
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = main.optimize_training(_request(("+5",)))
    assert result["scenarios"][0]["eligible_immediately"] is True
    assert "Cybernetics" not in [row["skill_name"] for row in result["scenarios"][0]["schedule"]]


def test_unknown_booster_blocks_real_remap_recommendation(monkeypatch):
    _install_sde(monkeypatch)
    snapshot = _snapshot(
        booster_attribute_bonus=AttributeSet.uniform(8),
        active_boosters=(ActiveBooster(attribute_bonus=AttributeSet.uniform(8)),),
        accrued_remap_cooldown_date=NOW - timedelta(days=1),
        bonus_remaps=2,
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = main.optimize_training(_request(("current", "+3")))
    assert result["exact"] is False
    assert result["recommended"]["blocked"] is True
    assert result["recommended"]["blocked_reason"] == "booster_expiry_unknown"
    assert result["range"]["best_case_all_plans_seconds"] < result["range"]["worst_case_all_plans_seconds"]
    for scenario in result["scenarios"]:
        assert scenario["exact"] is False
        assert scenario["remap_actions"] == []
        assert scenario["recommendation_blocked"] is True
        assert scenario["range"]["best_case_all_plans_seconds"] > 0
        assert scenario["range"]["worst_case_all_plans_seconds"] >= scenario["range"]["best_case_all_plans_seconds"]


def test_manual_booster_time_and_strength_unblock_time_optimization(monkeypatch):
    _install_sde(monkeypatch)
    snapshot = _snapshot(
        booster_attribute_bonus=AttributeSet.zero(),
        active_boosters=(),
        accrued_remap_cooldown_date=NOW - timedelta(days=1),
        bonus_remaps=1,
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    request = _request(("current",))
    request.booster_expires_at_override = NOW + timedelta(hours=671, minutes=43, seconds=20)
    request.booster_attribute_bonus_override = 8
    result = main.optimize_training(request)

    assert result["exact"] is True
    assert result["recommended"]["blocked"] is False
    assert result["current_character"]["booster_attribute_bonus_at_start"]["intelligence"] == 8
    assert result["boosters"]["attribute_bonus"]["intelligence"] == 8
