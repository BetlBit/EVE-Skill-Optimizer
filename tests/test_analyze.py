from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app import main
from app.main import AnalyzeRequest, ParseRequest
from app.models import ActiveBooster, AttributeName, AttributeSet, CharacterSkill, CharacterSnapshot, SkillDefinition, SkillQueueEntry
from app.training_math import timeline_task_seconds, total_sp_for_level


NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _install_test_sde():
    biology = SkillDefinition(
        type_id=3405,
        name="Biology",
        rank=1,
        primary=AttributeName.intelligence,
        secondary=AttributeName.memory,
    )
    gunnery = SkillDefinition(
        type_id=3300,
        name="Gunnery",
        rank=1,
        primary=AttributeName.perception,
        secondary=AttributeName.willpower,
    )
    main.sde.skills_by_id = {biology.type_id: biology, gunnery.type_id: gunnery}
    main.sde.skills_by_name = {biology.name.casefold(): biology, gunnery.name.casefold(): gunnery}


def _snapshot() -> CharacterSnapshot:
    return CharacterSnapshot(
        character_id=123456789,
        character_name="Test Pilot",
        total_sp=total_sp_for_level(1, 4),
        unallocated_sp=42,
        attributes=AttributeSet(intelligence=24, memory=24, perception=17, willpower=17, charisma=17),
        skills={
            3405: CharacterSkill(
                skill_id=3405,
                trained_level=4,
                active_level=4,
                skillpoints=total_sp_for_level(1, 4),
            )
        },
        implant_names=(
            "Cybernetic Subprocessor - Improved",
            "Memory Augmentation - Standard",
            "Mystery Implant",
        ),
        implant_attribute_bonus=AttributeSet(intelligence=5, memory=4, perception=0, willpower=0, charisma=0),
        booster_attribute_bonus=AttributeSet.uniform(8),
        active_boosters=(
            ActiveBooster(
                attribute_bonus=AttributeSet.uniform(8),
                expires_at=NOW + timedelta(days=30),
                source="evemon.attributes.booster",
                data_unavailable=("name",),
            ),
        ),
        bonus_remaps=2,
        last_remap_date=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
        evemon_last_timed_respec=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc),
        skill_queue=(
            SkillQueueEntry(skill_id=3405, level=5, start_sp=total_sp_for_level(1, 4), end_sp=total_sp_for_level(1, 5)),
        ),
    )


def _analyze(use_imported_character: bool, *, implant_bonus: int = 0, current_skills: list[dict] | None = None) -> dict:
    return main.analyze(
        AnalyzeRequest(
            plans=[ParseRequest(name="Test", text="Biology V")],
            current_skills=current_skills or [],
            use_imported_character=use_imported_character,
            omega=True,
            implant_bonus=implant_bonus,
        )
    )


def test_analyze_imported_character_uses_snapshot_skills(monkeypatch):
    _install_test_sde()
    monkeypatch.setattr(main, "load_snapshot", _snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["remaining_sp"] == total_sp_for_level(1, 5) - total_sp_for_level(1, 4)
    assert result["character"]["character_id"] == 123456789
    assert result["character"]["character_name"] == "Test Pilot"


def test_current_state_uses_snapshot_attributes(monkeypatch):
    _install_test_sde()
    snapshot = _snapshot()
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["current_state"]["base_attributes"] == snapshot.attributes.__dict__
    assert result["current_state"]["effective_attributes"] == {
        "intelligence": 37,
        "memory": 36,
        "perception": 25,
        "willpower": 25,
        "charisma": 25,
    }
    expected = timeline_task_seconds(
        main.PlanAnalyzer(main.sde).build_tasks(
            [main.parse_plan("Biology V", name="Test")],
            snapshot.skills,
        ),
        snapshot.attributes.plus(snapshot.implant_attribute_bonus),
        omega=True,
        modifiers=main._booster_modifiers(snapshot.active_boosters),
        start_time=NOW,
    )
    assert result["current_state"]["seconds"] == expected


def test_current_state_uses_effective_attributes_not_raw_snapshot_attributes(monkeypatch):
    _install_test_sde()
    snapshot = _snapshot()
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["current_state"]["attributes"] != snapshot.attributes.__dict__
    assert result["current_state"]["attributes"] == result["current_state"]["effective_attributes"]


def test_current_state_does_not_double_add_implants(monkeypatch):
    _install_test_sde()
    snapshot = _snapshot()
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True, implant_bonus=5)

    assert result["current_state"]["effective_attributes"]["intelligence"] == 37
    assert result["current_state"]["effective_attributes"]["memory"] == 36


def test_booster_is_not_double_counted(monkeypatch):
    _install_test_sde()
    snapshot = _snapshot()
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["current_state"]["booster_attribute_bonus"] == AttributeSet.uniform(8).__dict__
    assert result["current_state"]["effective_attributes"]["charisma"] == 25


def test_unknown_implant_is_returned_from_analyze(monkeypatch):
    _install_test_sde()
    monkeypatch.setattr(main, "load_snapshot", _snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["implants"]["unknown_implants"] == ["Mystery Implant"]


def test_optimal_remap_receives_correct_per_attribute_implant_bonuses(monkeypatch):
    _install_test_sde()
    monkeypatch.setattr(main, "load_snapshot", _snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True, implant_bonus=5)

    assert result["implants"]["attribute_bonus"]["intelligence"] == 5
    assert result["implants"]["attribute_bonus"]["memory"] == 4
    assert result["implants"]["attribute_bonus"]["perception"] == 0
    assert result["best_single_remap"]["effective_at_start"]["intelligence"] == result["best_single_remap"]["base"]["intelligence"] + 13
    assert result["best_single_remap"]["effective_at_start"]["memory"] == result["best_single_remap"]["base"]["memory"] + 12
    assert result["best_single_remap"]["effective_at_start"]["perception"] == result["best_single_remap"]["base"]["perception"] + 8


def test_unknown_booster_does_not_crash(monkeypatch):
    _install_test_sde()
    snapshot = CharacterSnapshot(
        **{
            **_snapshot().__dict__,
            "booster_attribute_bonus": AttributeSet.zero(),
            "active_boosters": (ActiveBooster(name="Unknown Booster", attribute_bonus=AttributeSet.zero()),),
        }
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["boosters"]["unknown_boosters"] == ["Unknown Booster"]


def test_expired_booster_gives_zero_current_bonus(monkeypatch):
    _install_test_sde()
    snapshot = CharacterSnapshot(
        **{
            **_snapshot().__dict__,
            "active_boosters": (
                ActiveBooster(attribute_bonus=AttributeSet.uniform(8), expires_at=NOW - timedelta(seconds=1)),
            ),
        }
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["boosters"]["attribute_bonus"] == AttributeSet.zero().__dict__
    assert result["current_state"]["effective_attributes"]["charisma"] == 17


def test_manual_mode_still_works(monkeypatch):
    _install_test_sde()
    monkeypatch.setattr(main, "load_snapshot", lambda: None)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(
        False,
        implant_bonus=2,
        current_skills=[
            {
                "skill_id": 3405,
                "trained_level": 4,
                "active_level": 4,
                "skillpoints": total_sp_for_level(1, 4),
            }
        ],
    )

    assert result["character_source"] == "request"
    assert result["remaining_sp"] == total_sp_for_level(1, 5) - total_sp_for_level(1, 4)
    assert result["implants"]["attribute_bonus"] == AttributeSet.uniform(2).__dict__
    assert "seconds" in result["best_single_remap"]


def test_time_saved_vs_current_is_calculated_correctly(monkeypatch):
    _install_test_sde()
    monkeypatch.setattr(main, "load_snapshot", _snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    expected = result["current_state"]["seconds"] - result["best_single_remap"]["seconds"]
    assert result["best_single_remap"]["time_saved_seconds_vs_current"] == expected
    assert result["best_single_remap"]["time_saved_days_vs_current"] == expected / 86400.0


def test_base_plus_plus5_implants_plus_plus8_booster_gives_expected_effective_attributes(monkeypatch):
    _install_test_sde()
    snapshot = CharacterSnapshot(
        **{
            **_snapshot().__dict__,
            "attributes": AttributeSet(intelligence=24, memory=24, perception=17, willpower=17, charisma=17),
            "implant_names": (
                "Cybernetic Subprocessor - Improved",
                "Memory Augmentation - Improved",
                "Ocular Filter - Improved",
                "Neural Boost - Improved",
                "Social Adaptation Chip - Improved",
            ),
            "implant_attribute_bonus": AttributeSet.uniform(5),
            "booster_attribute_bonus": AttributeSet.uniform(8),
            "active_boosters": (ActiveBooster(attribute_bonus=AttributeSet.uniform(8)),),
        }
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["current_state"]["effective_attributes"] == {
        "intelligence": 37,
        "memory": 37,
        "perception": 30,
        "willpower": 30,
        "charisma": 30,
    }


def test_direct_evemon_expiry_is_used(monkeypatch):
    _install_test_sde()
    monkeypatch.setattr(main, "load_snapshot", _snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["boosters"]["expires_at"] == (NOW + timedelta(days=30)).isoformat()
    assert result["boosters"]["expiry_source"] == "evemon_direct"
    assert result["current_state"]["exact"] is True


def test_expiry_unknown_returns_duration_range_not_fake_exact_time(monkeypatch):
    _install_test_sde()
    snapshot = CharacterSnapshot(
        **{
            **_snapshot().__dict__,
            "skill_queue": (),
            "active_boosters": (
                ActiveBooster(
                    attribute_bonus=AttributeSet.uniform(8),
                    source="evemon.attributes.booster",
                    data_unavailable=("name", "expires_at"),
                ),
            ),
        }
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["boosters"]["expires_at"] is None
    assert result["boosters"]["expiry_source"] is None
    assert result["current_state"]["exact"] is False
    assert result["current_state"]["seconds"] is None
    assert result["current_state"]["best_case_seconds"] < result["current_state"]["worst_case_seconds"]
    assert result["best_single_remap"]["exact"] is False
    assert "time_saved_days_vs_current" not in result["best_single_remap"]


def test_manual_override_works_and_is_marked(monkeypatch):
    _install_test_sde()
    snapshot = CharacterSnapshot(
        **{
            **_snapshot().__dict__,
            "active_boosters": (
                ActiveBooster(attribute_bonus=AttributeSet.uniform(8), data_unavailable=("name", "expires_at")),
            ),
        }
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = main.analyze(
        AnalyzeRequest(
            plans=[ParseRequest(name="Test", text="Biology V")],
            use_imported_character=True,
            omega=True,
            booster_expires_at_override=NOW + timedelta(hours=2),
        )
    )

    assert result["boosters"]["expires_at"] == (NOW + timedelta(hours=2)).isoformat()
    assert result["boosters"]["expiry_source"] == "manual_override"
    assert result["current_state"]["exact"] is True



def test_manual_booster_strength_override_replaces_evemon_strength(monkeypatch):
    _install_test_sde()
    snapshot = CharacterSnapshot(
        **{
            **_snapshot().__dict__,
            "booster_attribute_bonus": AttributeSet.uniform(8),
            "active_boosters": (ActiveBooster(attribute_bonus=AttributeSet.uniform(8)),),
        }
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = main.analyze(
        AnalyzeRequest(
            plans=[ParseRequest(name="Test", text="Biology V")],
            use_imported_character=True,
            omega=True,
            booster_expires_at_override=NOW + timedelta(hours=2),
            booster_attribute_bonus_override=12,
        )
    )

    assert result["boosters"]["imported_attribute_bonus"]["intelligence"] == 8
    assert result["boosters"]["effective_attribute_bonus"]["intelligence"] == 12
    assert result["boosters"]["attribute_bonus"]["intelligence"] == 12
    assert result["boosters"]["manual_attribute_bonus_override"] == 12
    assert result["current_state"]["booster_attribute_bonus"]["intelligence"] == 12
    assert result["current_state"]["exact"] is True


def test_manual_booster_strength_allows_manual_booster_when_evemon_strength_missing(monkeypatch):
    _install_test_sde()
    snapshot = CharacterSnapshot(
        **{
            **_snapshot().__dict__,
            "booster_attribute_bonus": AttributeSet.zero(),
            "active_boosters": (),
        }
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = main.analyze(
        AnalyzeRequest(
            plans=[ParseRequest(name="Test", text="Biology V")],
            use_imported_character=True,
            omega=True,
            booster_expires_at_override=NOW + timedelta(hours=671, minutes=43, seconds=20),
            booster_attribute_bonus_override=8,
        )
    )

    assert result["boosters"]["imported_attribute_bonus"] == AttributeSet.zero().__dict__
    assert result["boosters"]["attribute_bonus"]["intelligence"] == 8
    assert result["boosters"]["expires_at"] == (NOW + timedelta(hours=671, minutes=43, seconds=20)).isoformat()
    assert result["current_state"]["exact"] is True

def test_manual_override_requires_timezone(monkeypatch):
    _install_test_sde()
    monkeypatch.setattr(main, "load_snapshot", _snapshot)

    with pytest.raises(HTTPException):
        main.analyze(
            AnalyzeRequest(
                plans=[ParseRequest(name="Test", text="Biology V")],
                use_imported_character=True,
                booster_expires_at_override=datetime(2026, 9, 4, 13, 0),
            )
        )


def test_manual_override_requires_active_booster(monkeypatch):
    _install_test_sde()
    snapshot = CharacterSnapshot(
        **{
            **_snapshot().__dict__,
            "booster_attribute_bonus": AttributeSet.zero(),
            "active_boosters": (),
        }
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)

    with pytest.raises(HTTPException):
        main.analyze(
            AnalyzeRequest(
                plans=[ParseRequest(name="Test", text="Biology V")],
                use_imported_character=True,
                booster_expires_at_override=NOW + timedelta(hours=2),
            )
        )


def test_past_expiration_is_not_applied_as_current_bonus(monkeypatch):
    _install_test_sde()
    snapshot = CharacterSnapshot(
        **{
            **_snapshot().__dict__,
            "active_boosters": (
                ActiveBooster(attribute_bonus=AttributeSet.uniform(8), expires_at=NOW - timedelta(seconds=1)),
            ),
        }
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["boosters"]["attribute_bonus"] == AttributeSet.zero().__dict__
    assert result["current_state"]["booster_attribute_bonus"] == AttributeSet.zero().__dict__


def test_best_single_remap_uses_same_expiry_handling(monkeypatch):
    _install_test_sde()
    expires_at = NOW + timedelta(seconds=1)
    snapshot = CharacterSnapshot(
        **{
            **_snapshot().__dict__,
            "active_boosters": (ActiveBooster(attribute_bonus=AttributeSet.uniform(8), expires_at=expires_at),),
        }
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = _analyze(True)

    assert result["best_single_remap"]["exact"] is True
    assert result["boosters"]["expires_at"] == expires_at.isoformat()


def test_ignore_active_booster_is_explicit_and_exact(monkeypatch):
    _install_test_sde()
    snapshot = CharacterSnapshot(
        **{
            **_snapshot().__dict__,
            "skill_queue": (),
            "active_boosters": (ActiveBooster(attribute_bonus=AttributeSet.uniform(8)),),
        }
    )
    monkeypatch.setattr(main, "load_snapshot", lambda: snapshot)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = main.analyze(AnalyzeRequest(
        plans=[ParseRequest(name="Test", text="Biology V")],
        use_imported_character=True,
        omega=True,
        ignore_active_booster=True,
    ))
    assert result["current_state"]["exact"] is True
    assert result["boosters"]["ignored"] is True
    assert result["boosters"]["expiry_source"] == "ignored_by_user"
    assert result["boosters"]["imported_attribute_bonus"]["intelligence"] == 8
    assert result["boosters"]["attribute_bonus"]["intelligence"] == 0


def test_analyze_accepts_localized_eve_skill_plan(monkeypatch):
    _install_test_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    result = main.analyze(
        AnalyzeRequest(
            plans=[
                ParseRequest(
                    name="Localized",
                    text='<localized hint="Biology">Биология*</localized> 5',
                )
            ],
            current_skills=[],
            use_imported_character=False,
            omega=True,
            implant_bonus=0,
        )
    )

    assert result["remaining_sp"] == total_sp_for_level(1, 5)
    assert result["tasks"][-1]["skill"] == "Biology"


def test_analyze_unknown_skill_returns_http_400_instead_of_internal_error(monkeypatch):
    _install_test_sde()
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    with pytest.raises(HTTPException) as exc:
        main.analyze(
            AnalyzeRequest(
                plans=[ParseRequest(name="Bad", text="Definitely Unknown Skill 1")],
                current_skills=[],
                use_imported_character=False,
                omega=True,
                implant_bonus=0,
            )
        )

    assert exc.value.status_code == 400
    assert "Unknown EVE skill" in str(exc.value.detail)
