from __future__ import annotations

from dataclasses import dataclass

from .market import MarketSnapshot

IMPLANT_SET_NAMES = {
    "+3": (
        "Ocular Filter - Basic",
        "Memory Augmentation - Basic",
        "Neural Boost - Basic",
        "Cybernetic Subprocessor - Basic",
        "Social Adaptation Chip - Basic",
    ),
    "+4": (
        "Ocular Filter - Standard",
        "Memory Augmentation - Standard",
        "Neural Boost - Standard",
        "Cybernetic Subprocessor - Standard",
        "Social Adaptation Chip - Standard",
    ),
    "+5": (
        "Ocular Filter - Improved",
        "Memory Augmentation - Improved",
        "Neural Boost - Improved",
        "Cybernetic Subprocessor - Improved",
        "Social Adaptation Chip - Improved",
    ),
}

PLEX_NAME = "PLEX"
LARGE_SKILL_INJECTOR_NAME = "Large Skill Injector"
SMALL_SKILL_INJECTOR_NAME = "Small Skill Injector"


NES_ACCELERATORS = {
    2: {"name": "Basic 'Boost' Cerebral Accelerator", "base_days": 2, "plex": 5},
    4: {"name": "Standard 'Boost' Cerebral Accelerator", "base_days": 4, "plex": 20},
    6: {"name": "Advanced 'Boost' Cerebral Accelerator", "base_days": 6, "plex": 45},
    8: {"name": "Specialist 'Boost' Cerebral Accelerator", "base_days": 8, "plex": 80},
    10: {"name": "Expert 'Boost' Cerebral Accelerator", "base_days": 10, "plex": 125},
    12: {"name": "Genius 'Boost' Cerebral Accelerator", "base_days": 12, "plex": 180},
}


def accelerator_duration_seconds(base_days: float, biology_level: int, biology_implant_bonus: float = 0.0) -> float:
    biology_level = min(5, max(0, int(biology_level)))
    skill_multiplier = 1.0 + 0.20 * biology_level
    implant_multiplier = 1.0 + max(0.0, float(biology_implant_bonus))
    return float(base_days) * 86400.0 * skill_multiplier * implant_multiplier


@dataclass(frozen=True)
class InjectorPlan:
    injector_type: str
    count: int
    exact_sp_gained: int
    excess_sp: int
    unallocated_sp_used: int


def plex_to_isk(plex_quantity: int, plex_unit_price_isk: float) -> dict:
    return {
        "plex_unit_price_isk": plex_unit_price_isk,
        "plex_quantity": int(plex_quantity),
        "total_isk": float(plex_quantity) * float(plex_unit_price_isk),
    }


def implant_set_cost(
    scenario: str,
    snapshots: dict[int, MarketSnapshot],
    type_ids_by_name: dict[str, int],
    *,
    owned_implant_names: tuple[str, ...] | list[str] = (),
) -> dict:
    if scenario == "current":
        return {"implants_isk": 0.0, "items": [], "available": True, "warnings": ["current implants are already owned; incremental cost is 0 ISK"]}
    if scenario == "none":
        return {"implants_isk": 0.0, "items": [], "available": True, "warnings": []}

    items = []
    warnings = []
    total = 0.0
    available = True
    owned = {str(name).casefold() for name in owned_implant_names}
    for name in IMPLANT_SET_NAMES[scenario]:
        type_id = type_ids_by_name[name]
        snapshot = snapshots[type_id]
        if name.casefold() in owned:
            # The implant is already owned, so incremental purchase cost is 0 ISK,
            # but keep the real Jita unit price for the market-price table.
            item = _priced_item(name, snapshot, quantity=0)
            item.update({"total_isk": 0.0, "owned": True, "available": True, "warnings": []})
            items.append(item)
            continue
        price = snapshot.sell.unit_price if snapshot.sell else None
        if price is None:
            available = False
            warnings.append(f"{name} has no Jita 4-4 sell order")
        else:
            total += price
        items.append(_priced_item(name, snapshot, quantity=1))
    return {"implants_isk": total if available else None, "items": items, "available": available, "warnings": warnings}


def skill_injector_sp_gain(total_sp_before_injection: int, injector_type: str) -> int:
    large = _large_injector_gain(total_sp_before_injection)
    if injector_type == "large":
        return large
    if injector_type == "small":
        return large // 5
    raise ValueError(f"Unknown injector type: {injector_type}")


def minimum_injectors_for_sp(
    *,
    total_sp_before: int,
    target_sp: int,
    injector_type: str,
    unallocated_sp: int = 0,
    use_unallocated_sp: bool = False,
) -> InjectorPlan:
    free_sp = min(max(0, int(unallocated_sp)), max(0, int(target_sp))) if use_unallocated_sp else 0
    remaining = max(0, int(target_sp) - free_sp)
    total_sp = int(total_sp_before) + free_sp
    count = 0
    gained = 0
    while gained < remaining:
        gain = skill_injector_sp_gain(total_sp, injector_type)
        if gain <= 0:
            raise RuntimeError("Skill injector gain must be positive")
        count += 1
        gained += gain
        total_sp += gain
    return InjectorPlan(
        injector_type=injector_type,
        count=count,
        exact_sp_gained=gained,
        excess_sp=max(0, gained - remaining),
        unallocated_sp_used=free_sp,
    )


def injector_sp_for_count(
    *,
    total_sp_before: int,
    count: int,
    injector_type: str,
    target_sp: int | None = None,
    unallocated_sp: int = 0,
    use_unallocated_sp: bool = False,
) -> InjectorPlan:
    target = max(0, int(target_sp)) if target_sp is not None else None
    free_sp = min(max(0, int(unallocated_sp)), target) if use_unallocated_sp and target is not None else (max(0, int(unallocated_sp)) if use_unallocated_sp else 0)
    total_sp = int(total_sp_before) + free_sp
    gained = 0
    for _ in range(max(0, int(count))):
        gain = skill_injector_sp_gain(total_sp, injector_type)
        gained += gain
        total_sp += gain
    needed_after_free = max(0, target - free_sp) if target is not None else gained
    return InjectorPlan(
        injector_type=injector_type,
        count=max(0, int(count)),
        exact_sp_gained=gained,
        excess_sp=max(0, gained - needed_after_free),
        unallocated_sp_used=min(free_sp, target) if target is not None else free_sp,
    )


def schedule_time_after_sp(
    schedule_rows: list[dict],
    available_sp: int,
) -> dict:
    """Apply unallocated/injected SP from the start of an already-valid queue.

    Applying from the front preserves prerequisite order and gives a deterministic
    practical estimate for a user who wants to shorten this exact recommended queue.
    """
    remaining_sp = max(0, int(available_sp))
    saved_seconds = 0.0
    used_sp = 0
    for row in schedule_rows:
        if remaining_sp <= 0:
            break
        row_sp = max(0, int(row.get("remaining_sp") or 0))
        row_seconds = max(0.0, float(row.get("duration_seconds") or 0.0))
        if row_sp <= 0 or row_seconds <= 0:
            continue
        used = min(row_sp, remaining_sp)
        saved_seconds += row_seconds * (used / row_sp)
        used_sp += used
        remaining_sp -= used
    total_seconds = sum(max(0.0, float(row.get("duration_seconds") or 0.0)) for row in schedule_rows)
    return {
        "completion_seconds": max(0.0, total_seconds - saved_seconds),
        "time_saved_seconds": min(total_seconds, saved_seconds),
        "sp_used": used_sp,
        "sp_unused": remaining_sp,
    }


def strategy_efficiency(*, total_isk: float, time_saved_seconds: float, sp_injected: int | None = None) -> dict:
    if time_saved_seconds <= 0:
        isk_per_day = None
        isk_per_hour = None
    else:
        isk_per_day = total_isk / (time_saved_seconds / 86400.0)
        isk_per_hour = total_isk / (time_saved_seconds / 3600.0)
    return {
        "isk_per_day_saved": isk_per_day,
        "isk_per_hour_saved": isk_per_hour,
        "isk_per_sp_injected": (total_isk / sp_injected) if sp_injected else None,
    }


def strategy_result(
    *,
    strategy_id: str,
    description: str,
    completion_seconds: float,
    no_changes_seconds: float,
    implants_isk: float | None = 0.0,
    accelerators_isk: float | None = 0.0,
    injectors_isk: float | None = 0.0,
    plex_isk: float | None = 0.0,
    items: list[dict] | None = None,
    remap_plan: list[dict] | None = None,
    sp_injected: int | None = None,
    exact: bool = True,
    warnings: list[str] | None = None,
    recommendation_eligible: bool = True,
    informational: bool = False,
    plex_quantity: int = 0,
) -> dict:
    warnings = list(warnings or [])
    costs = [implants_isk, accelerators_isk, injectors_isk, plex_isk]
    available = all(value is not None for value in costs)
    total_isk = sum(float(value) for value in costs if value is not None) if available else None
    saved = no_changes_seconds - completion_seconds
    return {
        "strategy_id": strategy_id,
        "description": description,
        "time": {
            "all_plans_completion_seconds": completion_seconds,
            "all_plans_completion_days": completion_seconds / 86400.0,
            "time_saved_vs_no_changes_seconds": saved,
            "time_saved_vs_no_changes_days": saved / 86400.0,
        },
        "cost": {
            "implants_isk": implants_isk,
            "accelerators_isk": accelerators_isk,
            "injectors_isk": injectors_isk,
            "plex_isk": plex_isk,
            "total_isk": total_isk,
        },
        "efficiency": strategy_efficiency(total_isk=float(total_isk or 0.0), time_saved_seconds=saved, sp_injected=sp_injected) if available else {
            "isk_per_day_saved": None,
            "isk_per_hour_saved": None,
            "isk_per_sp_injected": None,
        },
        "items": items or [],
        "remap_plan": remap_plan or [],
        "exact": exact,
        "economically_available": available,
        "warnings": warnings,
        "recommendation_eligible": bool(recommendation_eligible),
        "informational": bool(informational),
        "plex_quantity": int(plex_quantity or 0),
    }


def pareto_frontier(strategies: list[dict]) -> list[str]:
    # Do not let heuristic/placeholder completion times drive a "best" economic
    # recommendation.  Non-exact strategies can still be displayed to the user,
    # but only exact, economically available rows participate in Pareto ranking.
    comparable = [
        strategy
        for strategy in strategies
        if strategy.get("economically_available")
        and strategy.get("exact")
        and strategy.get("recommendation_eligible", True)
        and strategy.get("time", {}).get("time_saved_vs_no_changes_seconds", 0) > 0
    ]
    frontier = []
    for candidate in comparable:
        dominated = False
        c_cost = candidate["cost"]["total_isk"]
        c_time = candidate["time"]["all_plans_completion_seconds"]
        for other in comparable:
            if other is candidate:
                continue
            o_cost = other["cost"]["total_isk"]
            o_time = other["time"]["all_plans_completion_seconds"]
            if o_cost <= c_cost and o_time <= c_time and (o_cost < c_cost or o_time < c_time):
                dominated = True
                break
        if not dominated:
            frontier.append(candidate)
    frontier.sort(key=lambda item: (item["cost"]["total_isk"], item["time"]["all_plans_completion_seconds"], item["strategy_id"]))
    return [item["strategy_id"] for item in frontier]


def recommended_ids(strategies: list[dict], *, useful_savings_ratio: float = 0.05) -> dict:
    available = [
        strategy
        for strategy in strategies
        if strategy.get("economically_available")
        and strategy.get("exact")
        and strategy.get("recommendation_eligible", True)
        and not strategy.get("informational", False)
    ]
    positive_saved = [strategy for strategy in available if strategy["time"]["time_saved_vs_no_changes_seconds"] > 0]
    fastest = min(
        positive_saved,
        key=lambda item: (item["time"]["all_plans_completion_seconds"], item["cost"]["total_isk"], item["strategy_id"]),
        default=None,
    )
    # Compare savings to the shared no-change baseline. Each strategy stores both
    # completion and savings, so baseline = completion + savings.
    useful = [
        strategy
        for strategy in positive_saved
        if strategy["time"]["time_saved_vs_no_changes_seconds"]
        >= useful_savings_ratio
        * (strategy["time"]["all_plans_completion_seconds"] + strategy["time"]["time_saved_vs_no_changes_seconds"])
    ]
    cheapest = min(
        useful,
        key=lambda item: (item["cost"]["total_isk"], item["time"]["all_plans_completion_seconds"], item["strategy_id"]),
        default=None,
    )
    nonzero = [strategy for strategy in positive_saved if strategy["cost"]["total_isk"] > 0]
    best_eff = min(
        (strategy for strategy in nonzero if strategy["efficiency"]["isk_per_day_saved"] is not None),
        key=lambda item: (item["efficiency"]["isk_per_day_saved"], item["cost"]["total_isk"], item["strategy_id"]),
        default=None,
    )
    return {
        "recommended_fastest": fastest["strategy_id"] if fastest else None,
        "recommended_cheapest": cheapest["strategy_id"] if cheapest else None,
        "recommended_best_isk_per_day_saved": best_eff["strategy_id"] if best_eff else None,
    }


def _large_injector_gain(total_sp: int) -> int:
    if total_sp < 5_000_000:
        return 500_000
    if total_sp < 50_000_000:
        return 400_000
    if total_sp < 80_000_000:
        return 300_000
    return 150_000


def _priced_item(name: str, snapshot: MarketSnapshot, *, quantity: int) -> dict:
    unit = snapshot.sell.unit_price if snapshot.sell else None
    return {
        "type_id": snapshot.type_id,
        "type_name": name,
        "quantity": quantity,
        "unit_price_isk": unit,
        "total_isk": unit * quantity if unit is not None else None,
        "source": snapshot.source,
        "fetched_at": snapshot.fetched_at.isoformat(),
        "available": unit is not None,
        "warnings": list(snapshot.warnings),
    }
