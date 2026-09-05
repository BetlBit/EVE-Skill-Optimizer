import asyncio
from datetime import datetime, timezone

from app import main
from app.economics import (
    accelerator_duration_seconds,
    implant_set_cost,
    injector_sp_for_count,
    schedule_time_after_sp,
    IMPLANT_SET_NAMES,
    minimum_injectors_for_sp,
    pareto_frontier,
    plex_to_isk,
    recommended_ids,
    skill_injector_sp_gain,
    strategy_efficiency,
    strategy_result,
)
from app.main import OptimizeEconomicsRequest, ParseRequest
from app.market import JITA_4_4_LOCATION_ID, MarketClient, MarketOrder, MarketSnapshot
from app.models import ActiveBooster, AttributeName, AttributeSet, CharacterSnapshot, SkillDefinition
from app.training_math import total_sp_for_level

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _snapshot(type_id, name, sell_price=None, buy_price=None):
    return MarketSnapshot(
        type_id=type_id,
        type_name=name,
        sell=MarketOrder(sell_price, 1, type_id * 10, NOW) if sell_price is not None else None,
        buy=MarketOrder(buy_price, 1, type_id * 10 + 1, NOW) if buy_price is not None else None,
        source="esi_jita_4_4",
        warnings=() if sell_price is not None else ("no active Jita 4-4 sell order",),
        fetched_at=NOW,
        cache_age_seconds=0,
    )


def _orders(price, *, location=JITA_4_4_LOCATION_ID, buy=False, order_id=1):
    return {"price": price, "location_id": location, "is_buy_order": buy, "order_id": order_id, "volume_remain": 7}


def test_lowest_jita_sell_order_chosen_correctly():
    async def fetcher(type_id, order_type, page):
        if order_type == "sell":
            return [_orders(30, order_id=3), _orders(10, order_id=1), _orders(20, order_id=2)], {"Date": "Sat, 05 Sep 2026 12:00:00 GMT"}
        return [], {}

    result = asyncio.run(MarketClient(order_fetcher=fetcher).get_jita_market_snapshot([1], type_names={1: "Item"}))[1]
    assert result.sell.unit_price == 10


def test_non_jita_order_ignored():
    async def fetcher(type_id, order_type, page):
        if order_type == "sell":
            return [_orders(1, location=123), _orders(9)], {}
        return [], {}

    result = asyncio.run(MarketClient(order_fetcher=fetcher).get_jita_market_snapshot([1], type_names={1: "Item"}))[1]
    assert result.sell.unit_price == 9


def test_highest_jita_buy_order_chosen_correctly():
    async def fetcher(type_id, order_type, page):
        if order_type == "buy":
            return [_orders(5, buy=True), _orders(7, buy=True), _orders(99, buy=True, location=123)], {}
        return [], {}

    result = asyncio.run(MarketClient(order_fetcher=fetcher).get_jita_market_snapshot([1], type_names={1: "Item"}))[1]
    assert result.buy.unit_price == 7


def test_missing_sell_order_handled_safely():
    async def fetcher(type_id, order_type, page):
        return [], {}

    result = asyncio.run(MarketClient(order_fetcher=fetcher).get_jita_market_snapshot([1], type_names={1: "Item"}))[1]
    assert result.sell is None
    assert "no active Jita 4-4 sell order" in result.warnings


def test_plex_conversion_correct():
    assert plex_to_isk(500, 5_000_000)["total_isk"] == 2_500_000_000


def test_implant_set_costs_for_plus_3_plus_4_plus_5():
    names = {
        "Ocular Filter - Basic": 1,
        "Memory Augmentation - Basic": 2,
        "Neural Boost - Basic": 3,
        "Cybernetic Subprocessor - Basic": 4,
        "Social Adaptation Chip - Basic": 5,
        "Ocular Filter - Standard": 6,
        "Memory Augmentation - Standard": 7,
        "Neural Boost - Standard": 8,
        "Cybernetic Subprocessor - Standard": 9,
        "Social Adaptation Chip - Standard": 10,
        "Ocular Filter - Improved": 11,
        "Memory Augmentation - Improved": 12,
        "Neural Boost - Improved": 13,
        "Cybernetic Subprocessor - Improved": 14,
        "Social Adaptation Chip - Improved": 15,
    }
    snapshots = {type_id: _snapshot(type_id, name, sell_price=type_id * 100) for name, type_id in names.items()}
    assert implant_set_cost("+3", snapshots, names)["implants_isk"] == 1500
    assert implant_set_cost("+4", snapshots, names)["implants_isk"] == 4000
    assert implant_set_cost("+5", snapshots, names)["implants_isk"] == 6500


def test_current_implants_incremental_cost_zero():
    assert implant_set_cost("current", {}, {})["implants_isk"] == 0


def test_market_cache_avoids_duplicate_same_request_lookups():
    calls = []

    async def fetcher(type_id, order_type, page):
        calls.append((type_id, order_type, page))
        return ([_orders(10, buy=order_type == "buy")] if order_type == "buy" else [_orders(9)]), {}

    client = MarketClient(order_fetcher=fetcher)
    asyncio.run(client.get_jita_market_snapshot([1, 1], type_names={1: "Item"}))
    asyncio.run(client.get_jita_market_snapshot([1], type_names={1: "Item"}))
    assert calls == [(1, "sell", 1), (1, "buy", 1)]


def test_injector_sp_gain_each_verified_bracket():
    assert skill_injector_sp_gain(4_999_999, "large") == 500_000
    assert skill_injector_sp_gain(5_000_000, "large") == 400_000
    assert skill_injector_sp_gain(50_000_000, "large") == 300_000
    assert skill_injector_sp_gain(80_000_000, "large") == 150_000
    assert skill_injector_sp_gain(80_000_000, "small") == 30_000


def test_multiple_injectors_crossing_bracket_boundary():
    plan = minimum_injectors_for_sp(total_sp_before=4_900_000, target_sp=700_000, injector_type="large")
    assert plan.count == 2
    assert plan.exact_sp_gained == 900_000


def test_minimum_injector_count_and_excess_sp():
    plan = minimum_injectors_for_sp(total_sp_before=81_000_000, target_sp=200_000, injector_type="large")
    assert plan.count == 2
    assert plan.excess_sp == 100_000


def test_unallocated_sp_counted_zero_cost_when_enabled():
    plan = minimum_injectors_for_sp(total_sp_before=81_000_000, target_sp=200_000, injector_type="large", unallocated_sp=75_000, use_unallocated_sp=True)
    assert plan.unallocated_sp_used == 75_000
    assert plan.count == 1


def test_no_changes_and_time_optimal_no_purchase_cost_zero():
    no_changes = strategy_result(strategy_id="no_changes", description="n", completion_seconds=10, no_changes_seconds=10)
    time_optimal = strategy_result(strategy_id="time_optimal_no_purchase", description="t", completion_seconds=8, no_changes_seconds=10)
    assert no_changes["cost"]["total_isk"] == 0
    assert time_optimal["cost"]["total_isk"] == 0


def test_isk_per_day_saved_formula_and_zero_saving():
    assert strategy_efficiency(total_isk=1_000_000, time_saved_seconds=86400)["isk_per_day_saved"] == 1_000_000
    assert strategy_efficiency(total_isk=1_000_000, time_saved_seconds=0)["isk_per_day_saved"] is None


def test_pareto_dominance_and_deterministic_frontier():
    strategies = [
        strategy_result(strategy_id="a", description="a", completion_seconds=10, no_changes_seconds=12, implants_isk=10),
        strategy_result(strategy_id="b", description="b", completion_seconds=11, no_changes_seconds=12, implants_isk=10),
        strategy_result(strategy_id="c", description="c", completion_seconds=9, no_changes_seconds=12, implants_isk=20),
    ]
    assert pareto_frontier(strategies) == ["a", "c"]
    assert pareto_frontier(list(reversed(strategies))) == ["a", "c"]


def test_fastest_cheapest_and_lowest_isk_day_recommendations():
    strategies = [
        strategy_result(strategy_id="free", description="f", completion_seconds=10, no_changes_seconds=12, implants_isk=0),
        strategy_result(strategy_id="cheap", description="c", completion_seconds=9, no_changes_seconds=12, implants_isk=30),
        strategy_result(strategy_id="fast", description="x", completion_seconds=5, no_changes_seconds=12, implants_isk=1000),
    ]
    recs = recommended_ids(strategies)
    assert recs["recommended_fastest"] == "fast"
    assert recs["recommended_cheapest"] == "free"
    assert recs["recommended_best_isk_per_day_saved"] == "cheap"


def test_unavailable_item_excludes_strategy_safely():
    strategy = strategy_result(strategy_id="bad", description="b", completion_seconds=9, no_changes_seconds=10, implants_isk=None)
    assert strategy["economically_available"] is False
    assert strategy["cost"]["total_isk"] is None


def test_market_failure_does_not_fabricate_price():
    async def fetcher(type_id, order_type, page):
        raise RuntimeError("boom")

    try:
        asyncio.run(MarketClient(order_fetcher=fetcher).get_jita_market_snapshot([1], type_names={1: "Item"}))
    except RuntimeError:
        pass


def test_optimize_economics_endpoint_with_mocked_market(monkeypatch):
    skills = {
        1: SkillDefinition(1, "IntMem", 1, AttributeName.intelligence, AttributeName.memory),
        3411: SkillDefinition(3411, "Cybernetics", 1, AttributeName.intelligence, AttributeName.memory),
    }
    main.sde.skills_by_id = skills
    main.sde.skills_by_name = {s.name.casefold(): s for s in skills.values()}
    names = {
        "PLEX": 100,
        "Large Skill Injector": 101,
        "Small Skill Injector": 102,
        "Ocular Filter - Basic": 201,
        "Memory Augmentation - Basic": 202,
        "Neural Boost - Basic": 203,
        "Cybernetic Subprocessor - Basic": 204,
        "Social Adaptation Chip - Basic": 205,
    }
    monkeypatch.setattr(main.sde, "type_id_by_name", lambda name: names[name])
    monkeypatch.setattr(main.sde, "cybernetics_level_for_attribute_implants", lambda bonus: 1)
    monkeypatch.setattr(main, "load_snapshot", lambda: CharacterSnapshot(
        character_id=1,
        total_sp=10_000_000,
        unallocated_sp=0,
        attributes=AttributeSet.uniform(20),
        skills={},
        implant_attribute_bonus=AttributeSet.zero(),
    ))
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    async def fetcher(type_id, order_type, page):
        return [_orders(type_id * 1_000, buy=order_type == "buy")], {}

    monkeypatch.setattr(main, "market_client", MarketClient(order_fetcher=fetcher))
    result = main.optimize_economics(OptimizeEconomicsRequest(
        plans=[ParseRequest(name="P", text="IntMem I")],
        use_imported_character=True,
        implant_scenarios=["current", "+3"],
        include_large_skill_injectors=True,
        include_small_skill_injectors=True,
    ))
    assert result["market"]["location_id"] == JITA_4_4_LOCATION_ID
    assert "no_changes" in [strategy["strategy_id"] for strategy in result["strategies"]]
    assert result["plex"]["plex_unit_price_isk"] == 100_000


def test_non_exact_strategy_is_excluded_from_frontier_and_recommendations():
    exact = strategy_result(strategy_id="exact", description="e", completion_seconds=10, no_changes_seconds=20, implants_isk=100, exact=True)
    tempting_but_estimated = strategy_result(strategy_id="estimate", description="x", completion_seconds=1, no_changes_seconds=20, implants_isk=1, exact=False)
    assert pareto_frontier([exact, tempting_but_estimated]) == ["exact"]
    recs = recommended_ids([exact, tempting_but_estimated])
    assert recs["recommended_fastest"] == "exact"
    assert recs["recommended_cheapest"] == "exact"


def test_unknown_booster_blocks_economics_instead_of_false_zero(monkeypatch):
    skills = {
        1: SkillDefinition(1, "IntMem", 1, AttributeName.intelligence, AttributeName.memory),
        3411: SkillDefinition(3411, "Cybernetics", 1, AttributeName.intelligence, AttributeName.memory),
    }
    main.sde.skills_by_id = skills
    main.sde.skills_by_name = {skill.name.casefold(): skill for skill in skills.values()}
    monkeypatch.setattr(main.sde, "cybernetics_level_for_attribute_implants", lambda bonus: 1)
    monkeypatch.setattr(main, "load_snapshot", lambda: CharacterSnapshot(
        character_id=1,
        total_sp=10_000_000,
        unallocated_sp=0,
        attributes=AttributeSet.uniform(20),
        skills={},
        implant_attribute_bonus=AttributeSet.zero(),
        booster_attribute_bonus=AttributeSet.uniform(8),
        active_boosters=(ActiveBooster(attribute_bonus=AttributeSet.uniform(8)),),
    ))
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    result = main.optimize_economics(OptimizeEconomicsRequest(
        plans=[ParseRequest(name="P", text="IntMem I")],
        use_imported_character=True,
        implant_scenarios=["current"],
        include_large_skill_injectors=False,
        include_small_skill_injectors=False,
    ))
    assert result["remaining_sp"] > 0
    assert result["blocked"] is True
    assert result["blocked_reason"] == "booster_expiry_unknown"
    assert result["strategies"] == []
    assert result["time"]["range"]["best_case_all_plans_seconds"] > 0
    assert result["time"]["range"]["worst_case_all_plans_seconds"] >= result["time"]["range"]["best_case_all_plans_seconds"]


def test_exact_economics_never_reports_zero_for_positive_remaining_sp(monkeypatch):
    skills = {
        1: SkillDefinition(1, "IntMem", 1, AttributeName.intelligence, AttributeName.memory),
        3411: SkillDefinition(3411, "Cybernetics", 1, AttributeName.intelligence, AttributeName.memory),
    }
    main.sde.skills_by_id = skills
    main.sde.skills_by_name = {skill.name.casefold(): skill for skill in skills.values()}
    names = {"PLEX": 100}
    monkeypatch.setattr(main.sde, "type_id_by_name", lambda name: names[name])
    monkeypatch.setattr(main, "load_snapshot", lambda: CharacterSnapshot(
        character_id=1, total_sp=10_000_000, unallocated_sp=0, attributes=AttributeSet.uniform(20),
        skills={}, implant_attribute_bonus=AttributeSet.zero(),
    ))
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    async def fetcher(type_id, order_type, page):
        return [_orders(100_000, buy=order_type == "buy")], {}
    monkeypatch.setattr(main, "market_client", MarketClient(order_fetcher=fetcher))
    result = main.optimize_economics(OptimizeEconomicsRequest(
        plans=[ParseRequest(name="P", text="IntMem I")],
        use_imported_character=True,
        implant_scenarios=["current"],
        include_large_skill_injectors=False,
        include_small_skill_injectors=False,
    ))
    assert result["remaining_sp"] > 0
    no_changes = next(strategy for strategy in result["strategies"] if strategy["strategy_id"] == "no_changes")
    assert no_changes["time"]["all_plans_completion_seconds"] > 0


def test_biology_duration_formula_and_by810_bonus():
    assert accelerator_duration_seconds(8, 0, 0.0) == 8 * 86400
    assert accelerator_duration_seconds(8, 5, 0.0) == 16 * 86400
    assert accelerator_duration_seconds(8, 5, 0.10) == 17.6 * 86400


def test_informational_buy_all_strategy_is_excluded_from_recommendations():
    practical = strategy_result(
        strategy_id="practical",
        description="p",
        completion_seconds=10,
        no_changes_seconds=20,
        implants_isk=100,
    )
    buy_all = strategy_result(
        strategy_id="buy_all",
        description="all",
        completion_seconds=0,
        no_changes_seconds=20,
        injectors_isk=1,
        recommendation_eligible=False,
        informational=True,
    )
    recs = recommended_ids([practical, buy_all])
    assert recs["recommended_fastest"] == "practical"
    assert recs["recommended_best_isk_per_day_saved"] == "practical"
    assert pareto_frontier([practical, buy_all]) == ["practical"]


def test_manual_plex_price_and_nes_accelerator_strategy(monkeypatch):
    skills = {
        1: SkillDefinition(1, "IntMem", 1, AttributeName.intelligence, AttributeName.memory),
        3405: SkillDefinition(3405, "Biology", 1, AttributeName.intelligence, AttributeName.memory),
        3411: SkillDefinition(3411, "Cybernetics", 1, AttributeName.intelligence, AttributeName.memory),
    }
    main.sde.skills_by_id = skills
    main.sde.skills_by_name = {skill.name.casefold(): skill for skill in skills.values()}
    monkeypatch.setattr(main.sde, "type_id_by_name", lambda name: {"PLEX": 100}[name])
    monkeypatch.setattr(main, "load_snapshot", lambda: CharacterSnapshot(
        character_id=1,
        total_sp=10_000_000,
        unallocated_sp=0,
        attributes=AttributeSet.uniform(20),
        skills={3405: main.CharacterSkill(skill_id=3405, trained_level=5, active_level=5, skillpoints=total_sp_for_level(1, 5))},
        implant_attribute_bonus=AttributeSet.zero(),
        implant_names=("Eifyr and Co. 'Alchemist' Biology BY-810",),
    ))
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    async def fetcher(type_id, order_type, page):
        return [], {}

    monkeypatch.setattr(main, "market_client", MarketClient(order_fetcher=fetcher))
    result = main.optimize_economics(OptimizeEconomicsRequest(
        plans=[ParseRequest(name="P", text="IntMem I")],
        use_imported_character=True,
        implant_scenarios=["current"],
        include_large_skill_injectors=False,
        include_small_skill_injectors=False,
        include_nes_accelerators=True,
        nes_accelerator_bonus=8,
        nes_accelerator_mode="count",
        nes_accelerator_count=1,
        plex_unit_price_isk_override=5_000_000,
    ))
    assert result["plex"]["source"] == "manual_override"
    assert result["plex"]["plex_unit_price_isk"] == 5_000_000
    plus8 = next(s for s in result["strategies"] if s["strategy_id"] == "nes_accelerator_8_count")
    assert plus8["plex_quantity"] == 80
    assert plus8["cost"]["plex_isk"] == 400_000_000
    assert plus8["accelerator"]["biology_level_start"] == 5
    assert plus8["accelerator"]["biology_implant_duration_bonus"] == 0.10



def test_no_changes_is_reference_only_and_not_recommended():
    baseline = strategy_result(
        strategy_id="no_changes",
        description="base",
        completion_seconds=1000,
        no_changes_seconds=1000,
        recommendation_eligible=False,
        informational=True,
    )
    useful = strategy_result(
        strategy_id="useful",
        description="u",
        completion_seconds=800,
        no_changes_seconds=1000,
        plex_isk=100,
    )
    recs = recommended_ids([baseline, useful])
    assert recs["recommended_fastest"] == "useful"
    assert recs["recommended_cheapest"] == "useful"
    assert recs["recommended_best_isk_per_day_saved"] == "useful"
    assert pareto_frontier([baseline, useful]) == ["useful"]


def test_cheapest_useful_requires_five_percent_time_saving():
    tiny = strategy_result(
        strategy_id="tiny",
        description="tiny",
        completion_seconds=970,
        no_changes_seconds=1000,
        plex_isk=1,
    )
    useful = strategy_result(
        strategy_id="useful",
        description="useful",
        completion_seconds=900,
        no_changes_seconds=1000,
        plex_isk=10,
    )
    assert recommended_ids([tiny, useful])["recommended_cheapest"] == "useful"


def test_owned_implant_set_has_zero_incremental_cost_but_keeps_market_unit_price():
    names = IMPLANT_SET_NAMES["+5"]
    type_ids = {name: index + 100 for index, name in enumerate(names)}
    snapshots = {type_ids[name]: _snapshot(type_ids[name], name, sell_price=100_000_000) for name in names}
    result = implant_set_cost("+5", snapshots, type_ids, owned_implant_names=names)
    assert result["available"] is True
    assert result["implants_isk"] == 0
    assert all(item.get("owned") for item in result["items"])
    assert all(item["unit_price_isk"] == 100_000_000 for item in result["items"])
    assert all(item["total_isk"] == 0 for item in result["items"])


def test_schedule_sp_overlay_and_injector_count_are_deterministic():
    plan = injector_sp_for_count(
        total_sp_before=90_000_000,
        count=2,
        injector_type="large",
        target_sp=500_000,
    )
    assert plan.exact_sp_gained == 300_000
    schedule = [
        {"remaining_sp": 100_000, "duration_seconds": 1000},
        {"remaining_sp": 300_000, "duration_seconds": 6000},
    ]
    overlay = schedule_time_after_sp(schedule, 250_000)
    assert overlay["sp_used"] == 250_000
    assert overlay["completion_seconds"] == 3000


def test_selected_nes_biology_v_and_manual_lsi_combo(monkeypatch):
    skills = {
        1: SkillDefinition(1, "IntMem", 1, AttributeName.intelligence, AttributeName.memory),
        3405: SkillDefinition(3405, "Biology", 1, AttributeName.intelligence, AttributeName.memory),
        3411: SkillDefinition(3411, "Cybernetics", 1, AttributeName.intelligence, AttributeName.memory),
    }
    main.sde.skills_by_id = skills
    main.sde.skills_by_name = {skill.name.casefold(): skill for skill in skills.values()}
    ids = {"PLEX": 100, "Large Skill Injector": 101}
    monkeypatch.setattr(main.sde, "type_id_by_name", lambda name: ids[name])
    monkeypatch.setattr(main, "load_snapshot", lambda: CharacterSnapshot(
        character_id=1,
        total_sp=90_000_000,
        unallocated_sp=0,
        attributes=AttributeSet.uniform(20),
        skills={3405: main.CharacterSkill(skill_id=3405, trained_level=5, active_level=5, skillpoints=total_sp_for_level(1, 5))},
        implant_attribute_bonus=AttributeSet.zero(),
    ))
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)

    async def fetcher(type_id, order_type, page):
        if order_type == "sell":
            price = 5_000_000 if type_id == 100 else 700_000_000
            return [_orders(price, order_id=type_id)], {}
        return [], {}

    monkeypatch.setattr(main, "market_client", MarketClient(order_fetcher=fetcher))
    result = main.optimize_economics(OptimizeEconomicsRequest(
        plans=[ParseRequest(name="P", text="IntMem V")],
        use_imported_character=True,
        implant_scenarios=["current"],
        include_large_skill_injectors=True,
        include_small_skill_injectors=False,
        include_nes_accelerators=True,
        nes_accelerator_bonus=12,
        nes_accelerator_mode="continuous",
        combo_large_injector_count=1,
        optimize_large_injector_count=False,
        plex_unit_price_isk_override=5_000_000,
    ))
    catalog12 = next(row for row in result["nes_accelerators"]["catalog"] if row["bonus"] == 12)
    assert catalog12["biology_duration_seconds"] == 24 * 86400
    assert result["nes_accelerators"]["selected_bonus"] == 12
    assert sum(1 for s in result["strategies"] if s["strategy_id"].startswith("nes_accelerator_12_continuous")) == 1
    combo = next(s for s in result["strategies"] if s["strategy_id"] == "nes_accelerator_12_large_injector_manual_1")
    assert combo["injector_combo"]["count"] == 1
    assert combo["injector_combo"]["sp_gained"] == 150_000
    assert combo["injector_combo"]["sp_remaining_after"] < result["remaining_sp"]
    assert combo["time"]["all_plans_completion_seconds"] < next(s for s in result["strategies"] if s["strategy_id"] == "nes_accelerator_12_continuous")["time"]["all_plans_completion_seconds"]
    assert result["large_skill_injectors"]["mode"] == "manual"
    assert result["large_skill_injectors"]["requested_count"] == 1
