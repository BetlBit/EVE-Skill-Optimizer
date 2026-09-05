from __future__ import annotations

import html
import math

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import asyncio
import logging

from .analyzer import PlanAnalyzer
from .boosters import BoosterExpiryInference, infer_attribute_booster_expiry
from .economics import (
    IMPLANT_SET_NAMES,
    NES_ACCELERATORS,
    LARGE_SKILL_INJECTOR_NAME,
    PLEX_NAME,
    SMALL_SKILL_INJECTOR_NAME,
    accelerator_duration_seconds,
    implant_set_cost,
    minimum_injectors_for_sp,
    injector_sp_for_count,
    schedule_time_after_sp,
    pareto_frontier,
    plex_to_isk,
    recommended_ids,
    strategy_result,
)
from .evemon import (
    EvemonImportError,
    import_evemon_character,
    list_evemon_characters,
    load_evemon_source,
    load_snapshot,
    save_evemon_source,
    save_snapshot,
    snapshot_to_dict,
)
from .implants import resolve_attribute_implants
from .logging_config import configure_logging
from .market import MarketClient, market_metadata
from datetime import datetime, timedelta, timezone

from .models import ActiveBooster, AttributeName, AttributeSet, CharacterSkill, CharacterSnapshot, PlanTarget, SkillPlan, SkillQueueEntry
from .plan_parser import parse_plan
from .remap import optimize_single_remap, optimize_single_remap_with_unknown_booster
from .sde import SdeRepository
from .scheduler import (
    baseline_input_plan_order,
    baseline_shortest_available_task,
    build_task_graph,
    evaluate_order,
    optimize_schedule,
    simulate_order,
)
from .sso import authorization_url, character_id_from_access_token, exchange_code
from .training_math import TimedAttributeModifier, timeline_task_seconds
from .time_optimizer import (
    RemapAvailability,
    candidate_remaps,
    optimize_training_time,
    remap_availability,
    simulate_time_plan,
)
from .paths import app_package_dir, ensure_runtime_dirs
from .version import VERSION

ensure_runtime_dirs()
configure_logging()
logger = logging.getLogger(__name__)
APP_DIR = app_package_dir()

app = FastAPI(title="EVE Skill Optimizer", version=VERSION)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
sde = SdeRepository()
market_client = MarketClient()
# Legacy optional path: kept for later, but EVEMon import is now the free MVP path.
oauth_states: dict[str, str] = {}


class ParseRequest(BaseModel):
    name: str = "Plan"
    text: str
    priority: float = 1.0


class AnalyzeRequest(BaseModel):
    plans: list[ParseRequest]
    current_skills: list[dict] = Field(default_factory=list)
    use_imported_character: bool = False
    omega: bool = True
    implant_bonus: int = 0
    booster_expires_at_override: datetime | None = None
    ignore_active_booster: bool = False
    booster_attribute_bonus_override: int | None = Field(default=None, ge=1, le=100)


class ScheduleRequest(BaseModel):
    plans: list[ParseRequest]
    current_skills: list[dict] = Field(default_factory=list)
    use_imported_character: bool = False
    omega: bool = True
    implant_bonus: int = 0
    booster_expires_at_override: datetime | None = None
    ignore_active_booster: bool = False
    booster_attribute_bonus_override: int | None = Field(default=None, ge=1, le=100)
    objective: str = "weighted_completion_time"
    respect_current_queue: bool = False


class OptimizeTrainingRequest(BaseModel):
    plans: list[ParseRequest]
    current_skills: list[dict] = Field(default_factory=list)
    use_imported_character: bool = False
    omega: bool = True
    objective: str = "weighted_completion_time"
    respect_current_queue: bool = False
    implant_scenarios: list[str] = Field(default_factory=lambda: ["current"])
    booster_expires_at_override: datetime | None = None
    ignore_active_booster: bool = False
    booster_attribute_bonus_override: int | None = Field(default=None, ge=1, le=100)
    beam_width: int = Field(default=192, ge=1, le=512)
    max_candidate_remaps: int = Field(default=24, ge=1, le=80)


class OptimizeEconomicsRequest(BaseModel):
    plans: list[ParseRequest]
    current_skills: list[dict] = Field(default_factory=list)
    use_imported_character: bool = False
    omega: bool = True
    objective: str = "weighted_completion_time"
    respect_current_queue: bool = False
    implant_scenarios: list[str] = Field(default_factory=lambda: ["current"])
    include_market_accelerators: bool = False
    include_plex_offers: bool = False
    include_nes_accelerators: bool = True
    nes_accelerator_mode: str = "continuous"
    nes_accelerator_bonus: int = Field(default=12, ge=2, le=12)
    nes_accelerator_count: int = Field(default=1, ge=1, le=1000)
    combo_large_injector_count: int = Field(default=0, ge=0, le=10000)
    optimize_large_injector_count: bool = True
    plex_unit_price_isk_override: float | None = Field(default=None, gt=0)
    include_large_skill_injectors: bool = True
    include_small_skill_injectors: bool = True
    use_unallocated_sp: bool = True
    economic_objective: str = "pareto"
    booster_expires_at_override: datetime | None = None
    ignore_active_booster: bool = False
    booster_attribute_bonus_override: int | None = Field(default=None, ge=1, le=100)
    beam_width: int = Field(default=192, ge=1, le=512)
    max_candidate_remaps: int = Field(default=24, ge=1, le=80)


class EvemonFileRequest(BaseModel):
    path: str


class EvemonImportRequest(BaseModel):
    path: str
    character_id: int | None = None


@app.get("/health")
def health():
    imported = load_snapshot()
    return {
        "ok": True,
        "version": VERSION,
        "sde_build": sde.build,
        "skills_loaded": len(sde.skills_by_id),
        "imported_character_id": imported.character_id if imported else None,
    }


@app.get("/")
def index():
    return FileResponse(APP_DIR / "templates" / "index.html")


@app.get("/api/status")
def status():
    imported = load_snapshot()
    source = load_evemon_source()
    if not sde.skills_by_id:
        try:
            sde.load()
        except Exception:
            pass
    return {
        "ok": True,
        "app": "EVE Skill Optimizer",
        "version": VERSION,
        "sde": {
            "loaded": bool(sde.skills_by_id),
            "build": sde.build,
            "skills_loaded": len(sde.skills_by_id),
        },
        "evemon": {
            "has_last_source": bool(source),
            "last_character_id": source.get("character_id") if source else None,
        },
        "character": snapshot_to_dict(imported) if imported else None,
    }


@app.post("/api/sde/update")
async def update_sde():
    try:
        build = await sde.update()
        sde.load()
        return {"build": build, "skills_loaded": len(sde.skills_by_id)}
    except Exception:
        logger.exception("SDE update failed")
        raise


@app.post("/api/plans/parse")
def parse(body: ParseRequest):
    try:
        plan = parse_plan(body.text, name=body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"name": plan.name, "targets": [t.__dict__ for t in plan.targets]}


@app.post("/api/evemon/characters")
def evemon_characters(body: EvemonFileRequest):
    try:
        chars = list_evemon_characters(body.path)
    except EvemonImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"characters": chars}


@app.post("/api/evemon/import")
def evemon_import(body: EvemonImportRequest):
    try:
        snapshot = import_evemon_character(body.path, body.character_id)
    except EvemonImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    save_snapshot(snapshot)
    save_evemon_source(body.path, snapshot.character_id)
    return {"ok": True, "character": snapshot_to_dict(snapshot)}


@app.post("/api/evemon/sync")
def evemon_sync():
    source = load_evemon_source()
    if not source:
        raise HTTPException(409, "No EVEMon source has been imported yet")
    try:
        snapshot = import_evemon_character(source["path"], int(source["character_id"]))
    except (EvemonImportError, KeyError, ValueError) as exc:
        raise HTTPException(400, f"EVEMon sync failed: {exc}") from exc
    save_snapshot(snapshot)
    return {"ok": True, "character": snapshot_to_dict(snapshot)}


@app.get("/api/character")
def character():
    snapshot = load_snapshot()
    if not snapshot:
        raise HTTPException(404, "No character imported yet")
    return snapshot_to_dict(snapshot)


@app.post("/api/analyze")
def analyze(body: AnalyzeRequest):
    if not sde.skills_by_id:
        try:
            sde.load()
        except Exception as exc:
            raise HTTPException(409, f"SDE not loaded: {exc}") from exc

    try:
        plans = [parse_plan(p.text, name=p.name) for p in body.plans]
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    snapshot: CharacterSnapshot | None = None
    if body.use_imported_character:
        snapshot = load_snapshot()
        if not snapshot:
            raise HTTPException(409, "No imported character. Import EVEMon data first.")
        current_skills = snapshot.skills
        if body.booster_expires_at_override is not None and body.booster_expires_at_override.tzinfo is None:
            raise HTTPException(400, "booster_expires_at_override must be timezone-aware RFC3339")
    else:
        current_skills = {
            int(s["skill_id"]): CharacterSkill(
                skill_id=int(s["skill_id"]),
                trained_level=int(s.get("trained_level", 0)),
                active_level=int(s.get("active_level", s.get("trained_level", 0))),
                skillpoints=int(s.get("skillpoints", 0)),
            )
            for s in body.current_skills
        }

    analyzer = PlanAnalyzer(sde)
    try:
        tasks = analyzer.build_tasks(plans, current_skills)
    except (ValueError, KeyError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    analysis_started_at = _utc_now()
    if snapshot is not None:
        implant_resolution = resolve_attribute_implants(snapshot.implant_names)
        implant = snapshot.implant_attribute_bonus
        if implant.total == 0 and snapshot.implant_names:
            implant = implant_resolution.attribute_bonus
        expiry, booster_modifiers, calculation_booster_bonus, booster_ignored = _resolve_booster_runtime(
            body=body,
            snapshot=snapshot,
            implant=implant,
            analysis_started_at=analysis_started_at,
        )
        booster_bonus_at_start = _active_modifier_bonus(booster_modifiers, analysis_started_at)
        effective_current_attributes = snapshot.base_attributes.plus(implant).plus(booster_bonus_at_start)
        current_exact = _duration_summary(
            tasks=tasks,
            base_with_implants=snapshot.base_attributes.plus(implant),
            booster_bonus=calculation_booster_bonus,
            booster_modifiers=booster_modifiers,
            expiry=expiry,
            omega=body.omega,
            analysis_started_at=analysis_started_at,
        )
        current_state = {
            "base_attributes": snapshot.base_attributes.__dict__,
            "implant_attribute_bonus": implant.__dict__,
            "booster_attribute_bonus": booster_bonus_at_start.__dict__,
            "effective_attributes_at_start": effective_current_attributes.__dict__,
            "effective_attributes": effective_current_attributes.__dict__,
            "attributes": effective_current_attributes.__dict__,
            **current_exact,
        }
        character_info = {
            "character_id": snapshot.character_id,
            "character_name": snapshot.character_name,
            "total_sp": snapshot.total_sp,
            "unallocated_sp": snapshot.unallocated_sp,
        }
        implants_info = {
            "names": list(snapshot.implant_names),
            "attribute_bonus": implant.__dict__,
            "unknown_implants": list(implant_resolution.unknown_implants),
        }
        boosters_info = _boosters_response(snapshot.active_boosters, analysis_started_at, expiry, ignored=booster_ignored, effective_bonus=calculation_booster_bonus, manual_bonus_override=body.booster_attribute_bonus_override)
        remap_info = {
            "bonus_remaps": snapshot.bonus_remaps,
            "last_remap_date": _iso(snapshot.last_remap_date),
            "evemon_last_timed_respec": _iso(snapshot.evemon_last_timed_respec),
        }
        skill_queue = [_skill_queue_entry_to_dict(entry) for entry in snapshot.skill_queue]
    else:
        implant = AttributeSet.uniform(body.implant_bonus)
        booster_modifiers = ()
        booster_bonus_at_start = AttributeSet.zero()
        current_exact = {"exact": True, "seconds": None, "days": None}
        current_state = None
        calculation_booster_bonus = AttributeSet.zero()
        booster_ignored = False
        character_info = None
        implants_info = {
            "names": [],
            "attribute_bonus": implant.__dict__,
            "unknown_implants": [],
        }
        boosters_info = {
            "active": [],
            "attribute_bonus": AttributeSet.zero().__dict__,
            "imported_attribute_bonus": AttributeSet.zero().__dict__,
            "ignored": False,
            "expires_at": None,
            "expiry_source": None,
            "expiry_confidence": None,
            "expiry_evidence": {},
            "unknown_boosters": [],
            "warnings": [],
        }
        remap_info = None
        skill_queue = []

    if snapshot is not None and current_state["exact"] is False:
        best_case, worst_case = optimize_single_remap_with_unknown_booster(
            tasks,
            implant_bonus=implant,
            booster_bonus=calculation_booster_bonus,
            omega=body.omega,
        )
        effective_at_start = best_case.base.plus(implant).plus(booster_bonus_at_start)
        best_single_remap = {
            "base": best_case.base.__dict__,
            "implant_attribute_bonus": implant.__dict__,
            "booster_attribute_bonus_at_start": booster_bonus_at_start.__dict__,
            "effective_at_start": effective_at_start.__dict__,
            "effective": effective_at_start.__dict__,
            "exact": False,
            "seconds": None,
            "days": None,
            "best_case_seconds": best_case.seconds,
            "worst_case_seconds": worst_case.seconds,
            "best_case_days": best_case.seconds / 86400.0,
            "worst_case_days": worst_case.seconds / 86400.0,
        }
    else:
        best = optimize_single_remap(
            tasks,
            implant_bonus=implant,
            timed_modifiers=booster_modifiers,
            start_time=analysis_started_at,
            omega=body.omega,
        )
        effective_at_start = best.base.plus(implant).plus(booster_bonus_at_start)
        best_single_remap = {
            "base": best.base.__dict__,
            "implant_attribute_bonus": implant.__dict__,
            "booster_attribute_bonus_at_start": booster_bonus_at_start.__dict__,
            "effective_at_start": effective_at_start.__dict__,
            "effective": effective_at_start.__dict__,
            "exact": True,
            "seconds": best.seconds,
            "days": best.seconds / 86400.0,
        }
        if snapshot is not None and current_state.get("seconds") is not None:
            saved_seconds = current_state["seconds"] - best.seconds
            best_single_remap["time_saved_seconds_vs_current"] = saved_seconds
            best_single_remap["time_saved_days_vs_current"] = saved_seconds / 86400.0

    return {
        "character_source": "evemon" if body.use_imported_character else "request",
        "analysis_started_at": _iso(analysis_started_at),
        "character": character_info,
        "remaining_sp": sum(t.sp_remaining for t in tasks),
        "task_count": len(tasks),
        "current_state": current_state,
        "implants": implants_info,
        "boosters": boosters_info,
        "remap": remap_info,
        "skill_queue": skill_queue,
        "best_single_remap": best_single_remap,
        "tasks": [
            {
                "skill_id": t.skill_id,
                "skill": t.skill_name,
                "level": t.level,
                "sp_remaining": t.sp_remaining,
                "primary": t.primary.value,
                "secondary": t.secondary.value,
                "plans": t.plan_names,
            }
            for t in tasks
        ],
    }


@app.post("/api/schedule")
def schedule(body: ScheduleRequest):
    if not sde.skills_by_id:
        try:
            sde.load()
        except Exception as exc:
            raise HTTPException(409, f"SDE not loaded: {exc}") from exc
    if len(body.plans) > 20:
        raise HTTPException(400, "Too many plans; maximum is 20")
    if body.objective not in {"weighted_completion_time", "makespan", "priority_lexicographic"}:
        raise HTTPException(400, f"Unknown schedule objective: {body.objective}")
    if any(plan.priority <= 0 for plan in body.plans):
        raise HTTPException(400, "plan priority must be positive")

    try:
        plans = [parse_plan(p.text, name=p.name) for p in body.plans]
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    for plan, request_plan in zip(plans, body.plans):
        plan.weight = float(request_plan.priority)

    snapshot: CharacterSnapshot | None = None
    if body.use_imported_character:
        snapshot = load_snapshot()
        if not snapshot:
            raise HTTPException(409, "No imported character. Import EVEMon data first.")
        if body.booster_expires_at_override is not None and body.booster_expires_at_override.tzinfo is None:
            raise HTTPException(400, "booster_expires_at_override must be timezone-aware RFC3339")
        current_skills = snapshot.skills
        implant = snapshot.implant_attribute_bonus
        if implant.total == 0 and snapshot.implant_names:
            implant = resolve_attribute_implants(snapshot.implant_names).attribute_bonus
        base_attributes = snapshot.base_attributes.plus(implant)
        booster_bonus = snapshot.booster_attribute_bonus
        active_boosters = snapshot.active_boosters
        character_source = "evemon"
    else:
        current_skills = _current_skills_from_request(body.current_skills)
        implant = AttributeSet.uniform(body.implant_bonus)
        base_attributes = AttributeSet(intelligence=20, memory=20, perception=20, willpower=20, charisma=19).plus(implant)
        booster_bonus = AttributeSet.zero()
        active_boosters = ()
        character_source = "request"

    analysis_started_at = _utc_now()
    try:
        graph = build_task_graph(plans, current_skills, sde.skills_by_id, sde.skills_by_name)
    except (ValueError, KeyError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc

    if snapshot is not None:
        expiry, resolved_modifiers, booster_bonus, booster_ignored = _resolve_booster_runtime(
            body=body,
            snapshot=snapshot,
            implant=implant,
            analysis_started_at=analysis_started_at,
        )
    else:
        expiry = BoosterExpiryInference(None, None, None, {})
        resolved_modifiers = ()
        booster_ignored = False
    exact = booster_ignored or not (booster_bonus.total > 0 and not expiry.exact)
    modifiers = resolved_modifiers if exact else ()
    pinned = _current_queue_prefix(snapshot, graph, analysis_started_at) if body.respect_current_queue and snapshot else ()
    optimized = optimize_schedule(
        graph,
        objective=body.objective,
        base_attributes=base_attributes,
        modifiers=modifiers,
        start_time=analysis_started_at,
        omega=body.omega,
        pinned_prefix=pinned,
    )
    input_baseline = evaluate_order(
        graph,
        baseline_input_plan_order(graph),
        objective=body.objective,
        base_attributes=base_attributes,
        modifiers=modifiers,
        start_time=analysis_started_at,
        omega=body.omega,
    )
    shortest_baseline = evaluate_order(
        graph,
        baseline_shortest_available_task(graph),
        objective=body.objective,
        base_attributes=base_attributes,
        modifiers=modifiers,
        start_time=analysis_started_at,
        omega=body.omega,
    )
    completion, makespan, rows = simulate_order(
        graph,
        optimized.order,
        base_attributes=base_attributes,
        modifiers=modifiers,
        start_time=analysis_started_at,
        omega=body.omega,
    )

    response = {
        "character_source": character_source,
        "analysis_started_at": _iso(analysis_started_at),
        "objective": body.objective,
        "summary": _schedule_summary(graph),
        "optimizer": {
            "algorithm": optimized.algorithm,
            "optimality": optimized.optimality,
            "states_evaluated": optimized.states_evaluated,
            "beam_width": optimized.beam_width,
            "objective_value": optimized.objective_value if exact else None,
        },
        "plans": _schedule_plans_response(graph, completion, rows, analysis_started_at, exact),
        "recommended_schedule": _schedule_rows_response(graph, rows, exact),
        "baselines": {
            "input_plan_order": _baseline_response(input_baseline, graph, analysis_started_at, exact),
            "shortest_available_task": _baseline_response(shortest_baseline, graph, analysis_started_at, exact),
        },
        "comparison": _schedule_comparison(input_baseline, optimized, graph, exact),
        "boosters": _boosters_response(active_boosters, analysis_started_at, expiry, ignored=booster_ignored, effective_bonus=booster_bonus, manual_bonus_override=body.booster_attribute_bonus_override),
        "exact": exact,
    }
    if not exact:
        best_mod = (TimedAttributeModifier(booster_bonus),)
        worst_mod: tuple[TimedAttributeModifier, ...] = ()
        best_eval = evaluate_order(graph, optimized.order, objective=body.objective, base_attributes=base_attributes, modifiers=best_mod, start_time=analysis_started_at, omega=body.omega)
        worst_eval = evaluate_order(graph, optimized.order, objective=body.objective, base_attributes=base_attributes, modifiers=worst_mod, start_time=analysis_started_at, omega=body.omega)
        best_completion, best_makespan, best_rows = simulate_order(
            graph,
            optimized.order,
            base_attributes=base_attributes,
            modifiers=best_mod,
            start_time=analysis_started_at,
            omega=body.omega,
        )
        worst_completion, worst_makespan, worst_rows = simulate_order(
            graph,
            optimized.order,
            base_attributes=base_attributes,
            modifiers=worst_mod,
            start_time=analysis_started_at,
            omega=body.omega,
        )
        response["plans"] = _schedule_plans_range_response(
            graph,
            best_completion,
            worst_completion,
            best_rows,
            analysis_started_at,
        )
        response["recommended_schedule"] = _schedule_rows_range_response(graph, best_rows, worst_rows)
        response["range"] = {
            "best_case_objective_value": best_eval.objective_value,
            "worst_case_objective_value": worst_eval.objective_value,
            "best_case_all_plans_seconds": best_makespan,
            "worst_case_all_plans_seconds": worst_makespan,
        }
    return response


@app.post("/api/optimize-training")
def optimize_training(body: OptimizeTrainingRequest):
    if not sde.skills_by_id:
        try:
            sde.load()
        except Exception as exc:
            raise HTTPException(409, f"SDE not loaded: {exc}") from exc
    if len(body.plans) > 20:
        raise HTTPException(400, "Too many plans; maximum is 20")
    if body.objective not in {"weighted_completion_time", "makespan", "priority_lexicographic"}:
        raise HTTPException(400, f"Unknown schedule objective: {body.objective}")
    if any(plan.priority <= 0 for plan in body.plans):
        raise HTTPException(400, "plan priority must be positive")

    requested_scenarios = _normalize_implant_scenarios(body.implant_scenarios)
    try:
        plans = [parse_plan(p.text, name=p.name) for p in body.plans]
    except (ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    for plan, request_plan in zip(plans, body.plans):
        plan.weight = float(request_plan.priority)

    snapshot: CharacterSnapshot | None = None
    if body.use_imported_character:
        snapshot = load_snapshot()
        if not snapshot:
            raise HTTPException(409, "No imported character. Import EVEMon data first.")
        if body.booster_expires_at_override is not None and body.booster_expires_at_override.tzinfo is None:
            raise HTTPException(400, "booster_expires_at_override must be timezone-aware RFC3339")
        current_skills = snapshot.skills
        current_base = snapshot.base_attributes
        current_implants = snapshot.implant_attribute_bonus
        if current_implants.total == 0 and snapshot.implant_names:
            current_implants = resolve_attribute_implants(snapshot.implant_names).attribute_bonus
        active_boosters = snapshot.active_boosters
        booster_bonus = snapshot.booster_attribute_bonus
        character_source = "evemon"
    else:
        current_skills = _current_skills_from_request(body.current_skills)
        current_base = AttributeSet(intelligence=20, memory=20, perception=20, willpower=20, charisma=19)
        current_implants = AttributeSet.zero()
        active_boosters = ()
        booster_bonus = AttributeSet.zero()
        character_source = "request"

    analysis_started_at = _utc_now()
    try:
        base_graph = build_task_graph(plans, current_skills, sde.skills_by_id, sde.skills_by_name)
    except (ValueError, KeyError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc

    if snapshot is not None:
        expiry, resolved_modifiers, booster_bonus, booster_ignored = _resolve_booster_runtime(
            body=body,
            snapshot=snapshot,
            implant=current_implants,
            analysis_started_at=analysis_started_at,
        )
    else:
        expiry = BoosterExpiryInference(None, None, None, {})
        resolved_modifiers = ()
        booster_ignored = False
    exact = booster_ignored or not (booster_bonus.total > 0 and not expiry.exact)
    modifiers = resolved_modifiers if exact else ()
    remaps = remap_availability(snapshot, analysis_started_at)
    pinned = _current_queue_prefix(snapshot, base_graph, analysis_started_at) if body.respect_current_queue and snapshot else ()

    no_changes = optimize_schedule(
        base_graph,
        objective=body.objective,
        base_attributes=current_base.plus(current_implants),
        modifiers=modifiers,
        start_time=analysis_started_at,
        omega=body.omega,
        beam_width=body.beam_width,
        pinned_prefix=pinned,
    )
    no_changes_completion, no_changes_makespan, no_changes_rows = simulate_order(
        base_graph,
        no_changes.order,
        base_attributes=current_base.plus(current_implants),
        modifiers=modifiers,
        start_time=analysis_started_at,
        omega=body.omega,
    )

    scenario_rows = []
    for scenario in requested_scenarios:
        try:
            if exact:
                scenario_result = _optimize_implant_scenario(
                    scenario,
                    plans=plans,
                    current_skills=current_skills,
                    current_base=current_base,
                    current_implants=current_implants,
                    remaps=remaps,
                    modifiers=modifiers,
                    start_time=analysis_started_at,
                    objective=body.objective,
                    omega=body.omega,
                    beam_width=body.beam_width,
                    max_candidate_remaps=body.max_candidate_remaps,
                    pinned_prefix=pinned,
                    exact_booster=True,
                )
            else:
                best_case = _optimize_implant_scenario(
                    scenario,
                    plans=plans,
                    current_skills=current_skills,
                    current_base=current_base,
                    current_implants=current_implants,
                    remaps=remaps,
                    modifiers=(TimedAttributeModifier(booster_bonus),),
                    start_time=analysis_started_at,
                    objective=body.objective,
                    omega=body.omega,
                    beam_width=body.beam_width,
                    max_candidate_remaps=body.max_candidate_remaps,
                    pinned_prefix=pinned,
                    exact_booster=True,
                )
                worst_case = _optimize_implant_scenario(
                    scenario,
                    plans=plans,
                    current_skills=current_skills,
                    current_base=current_base,
                    current_implants=current_implants,
                    remaps=remaps,
                    modifiers=(),
                    start_time=analysis_started_at,
                    objective=body.objective,
                    omega=body.omega,
                    beam_width=body.beam_width,
                    max_candidate_remaps=body.max_candidate_remaps,
                    pinned_prefix=pinned,
                    exact_booster=True,
                )
                scenario_result = _combine_time_scenario_range(best_case, worst_case)
        except (ValueError, KeyError, RuntimeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        scenario_rows.append(scenario_result)

    recommended = min((row for row in scenario_rows if row["exact"]), key=lambda row: row["optimizer"]["objective_value"], default=None)
    display_booster_bonus_at_start = (
        AttributeSet.zero()
        if booster_ignored
        else _active_modifier_bonus(_effective_booster_modifiers(booster_bonus, expiry.expires_at), analysis_started_at)
    )

    response = {
        "character_source": character_source,
        "analysis_started_at": _iso(analysis_started_at),
        "objective": body.objective,
        "exact": exact,
        "remap_availability": _remap_availability_response(remaps),
        "current_character": {
            "character_id": snapshot.character_id if snapshot else None,
            "character_name": snapshot.character_name if snapshot else None,
            "base_attributes": current_base.__dict__,
            "implant_attribute_bonus": current_implants.__dict__,
            "booster_attribute_bonus_at_start": display_booster_bonus_at_start.__dict__,
            "bonus_remaps": snapshot.bonus_remaps if snapshot else None,
        },
        "summary": _schedule_summary(base_graph),
        "boosters": _boosters_response(active_boosters, analysis_started_at, expiry, ignored=booster_ignored, effective_bonus=booster_bonus, manual_bonus_override=body.booster_attribute_bonus_override),
        "no_changes": {
            "description": "current base attributes, current implants, current booster handling, no remaps",
            "optimizer": _optimizer_response(no_changes, exact, 0, 0),
            "plans": _schedule_plans_response(base_graph, no_changes_completion, no_changes_rows, analysis_started_at, exact),
            "schedule": _schedule_rows_response(base_graph, no_changes_rows, exact),
        },
        "scenarios": scenario_rows,
        "recommended": {
            "scenario": recommended["scenario"] if recommended else None,
            "objective_value": recommended["optimizer"]["objective_value"] if recommended and recommended["exact"] else None,
            "all_plans_completion_seconds": recommended["all_plans_completion_seconds"] if recommended and recommended["exact"] else None,
            "blocked": not exact,
            "blocked_reason": "booster_expiry_unknown" if not exact else None,
        },
        "algorithm_notes": {
            "remap_boundaries": "remaps are considered only before the first task and between tasks",
            "remap_consumption": "timed remap is consumed before bonus remaps when both are available",
            "implant_switching": "fixed implant scenario only; implants activate once after Cybernetics prerequisites if needed",
        },
    }
    if not exact:
        best_mod = (TimedAttributeModifier(booster_bonus),)
        worst_mod: tuple[TimedAttributeModifier, ...] = ()
        best_eval = evaluate_order(base_graph, no_changes.order, objective=body.objective, base_attributes=current_base.plus(current_implants), modifiers=best_mod, start_time=analysis_started_at, omega=body.omega)
        worst_eval = evaluate_order(base_graph, no_changes.order, objective=body.objective, base_attributes=current_base.plus(current_implants), modifiers=worst_mod, start_time=analysis_started_at, omega=body.omega)
        best_completion, best_makespan, best_rows = simulate_order(
            base_graph,
            no_changes.order,
            base_attributes=current_base.plus(current_implants),
            modifiers=best_mod,
            start_time=analysis_started_at,
            omega=body.omega,
        )
        worst_completion, worst_makespan, worst_rows = simulate_order(
            base_graph,
            no_changes.order,
            base_attributes=current_base.plus(current_implants),
            modifiers=worst_mod,
            start_time=analysis_started_at,
            omega=body.omega,
        )
        response["no_changes"]["plans"] = _schedule_plans_range_response(
            base_graph,
            best_completion,
            worst_completion,
            best_rows,
            analysis_started_at,
        )
        response["no_changes"]["schedule"] = _schedule_rows_range_response(base_graph, best_rows, worst_rows)
        response["no_changes"]["range"] = {
            "best_case_all_plans_seconds": best_makespan,
            "worst_case_all_plans_seconds": worst_makespan,
        }
        response["range"] = {
            "best_case_objective_value": best_eval.objective_value,
            "worst_case_objective_value": worst_eval.objective_value,
            "best_case_all_plans_seconds": best_makespan,
            "worst_case_all_plans_seconds": worst_makespan,
        }
    return response


@app.post("/api/optimize-economics")
def optimize_economics(body: OptimizeEconomicsRequest):
    if body.economic_objective not in {"fastest", "cheapest", "lowest_isk_per_day_saved", "pareto"}:
        raise HTTPException(400, f"Unknown economic objective: {body.economic_objective}")
    if body.nes_accelerator_mode not in {"continuous", "count"}:
        raise HTTPException(400, f"Unknown NES accelerator mode: {body.nes_accelerator_mode}")
    if body.nes_accelerator_bonus not in NES_ACCELERATORS:
        raise HTTPException(400, f"Unknown NES accelerator bonus: {body.nes_accelerator_bonus}")
    time_result = optimize_training(
        OptimizeTrainingRequest(
            plans=body.plans,
            current_skills=body.current_skills,
            use_imported_character=body.use_imported_character,
            omega=body.omega,
            objective=body.objective,
            respect_current_queue=body.respect_current_queue,
            implant_scenarios=body.implant_scenarios,
            booster_expires_at_override=body.booster_expires_at_override,
            booster_attribute_bonus_override=body.booster_attribute_bonus_override,
            ignore_active_booster=body.ignore_active_booster,
            beam_width=body.beam_width,
            max_candidate_remaps=body.max_candidate_remaps,
        )
    )
    snapshot = load_snapshot() if body.use_imported_character else None
    total_sp = snapshot.total_sp if snapshot else _request_total_sp(body.current_skills)
    unallocated_sp = snapshot.unallocated_sp if snapshot else 0
    remaining_sp = time_result["summary"]["remaining_sp"]

    if not time_result["exact"]:
        return {
            "character_source": time_result["character_source"],
            "analysis_started_at": time_result["analysis_started_at"],
            "objective": body.objective,
            "economic_objective": body.economic_objective,
            "current_character": time_result["current_character"],
            "remaining_sp": remaining_sp,
            "blocked": True,
            "blocked_reason": "booster_expiry_unknown",
            "market": None,
            "plex": None,
            "nes_accelerators": _nes_catalog_for_character(snapshot, body),
            "large_skill_injectors": {
                "enabled": body.include_large_skill_injectors,
                "mode": "auto" if body.optimize_large_injector_count else "manual",
                "requested_count": int(body.combo_large_injector_count),
            },
            "time": {
                "no_changes": time_result["no_changes"],
                "time_optimal": None,
                "range": time_result.get("range"),
            },
            "strategies": [],
            "pareto_frontier": [],
            "recommended_fastest": None,
            "recommended_cheapest": None,
            "recommended_best_isk_per_day_saved": None,
            "boosters": time_result.get("boosters"),
            "warnings": [
                "Booster expiration is unknown. Set a manual expiration time or ignore the booster before comparing ISK/time strategies."
            ],
        }

    no_changes_all_seconds = _schedule_all_seconds(time_result["no_changes"]["schedule"])
    if remaining_sp > 0 and no_changes_all_seconds <= 0:
        raise HTTPException(
            500,
            "Training time calculation returned zero while remaining SP is positive; economics calculation was stopped to avoid a false result.",
        )

    type_ids_by_name = _economic_type_ids(body.implant_scenarios, body.include_large_skill_injectors, body.include_small_skill_injectors)
    snapshots = _run_async(
        market_client.get_jita_market_snapshot(
            list(type_ids_by_name.values()),
            type_names={type_id: name for name, type_id in type_ids_by_name.items()},
        )
    )
    market = market_metadata(list(snapshots.values()))
    plex_snapshot = snapshots.get(type_ids_by_name[PLEX_NAME])
    market_plex_unit = plex_snapshot.sell.unit_price if plex_snapshot and plex_snapshot.sell else None
    plex_unit = float(body.plex_unit_price_isk_override) if body.plex_unit_price_isk_override is not None else market_plex_unit
    plex_source = "manual_override" if body.plex_unit_price_isk_override is not None else "esi_jita_4_4"

    strategies: list[dict] = []
    strategies.append(
        strategy_result(
            strategy_id="no_changes",
            description="Current attributes, owned implants and active booster, no purchases, no remap.",
            completion_seconds=no_changes_all_seconds,
            no_changes_seconds=no_changes_all_seconds,
            items=[],
            remap_plan=[],
            exact=time_result["exact"],
            recommendation_eligible=False,
            informational=True,
        )
    )

    current_scenario = next((s for s in time_result["scenarios"] if s["scenario"] == "current"), None)
    if current_scenario:
        strategies.append(
            strategy_result(
                strategy_id="time_optimal_no_purchase",
                description="Free/legal remaps and ordering only; no purchases.",
                completion_seconds=current_scenario["all_plans_completion_seconds"] or no_changes_all_seconds,
                no_changes_seconds=no_changes_all_seconds,
                items=[],
                remap_plan=current_scenario["remap_actions"],
                exact=current_scenario["exact"],
            )
        )

    for scenario in body.implant_scenarios:
        if scenario not in IMPLANT_SET_NAMES:
            continue
        scenario_time = next((s for s in time_result["scenarios"] if s["scenario"] == scenario), None)
        if not scenario_time:
            continue
        cost = implant_set_cost(scenario, snapshots, type_ids_by_name, owned_implant_names=snapshot.implant_names if snapshot else ())
        strategies.append(
            strategy_result(
                strategy_id=f"implants_{scenario[1]}",
                description=f"Buy the standard +{scenario[1]} attribute implant set at Jita 4-4 sell prices.",
                completion_seconds=scenario_time["all_plans_completion_seconds"] or no_changes_all_seconds,
                no_changes_seconds=no_changes_all_seconds,
                implants_isk=cost["implants_isk"],
                items=cost["items"],
                remap_plan=scenario_time["remap_actions"],
                exact=scenario_time["exact"],
                warnings=cost["warnings"],
            )
        )

    highest_priority_sp = _highest_priority_plan_sp(time_result)
    if body.include_large_skill_injectors:
        _add_injector_strategies(
            strategies,
            "large",
            type_ids_by_name[LARGE_SKILL_INJECTOR_NAME],
            snapshots[type_ids_by_name[LARGE_SKILL_INJECTOR_NAME]],
            total_sp=total_sp,
            unallocated_sp=unallocated_sp,
            use_unallocated_sp=body.use_unallocated_sp,
            remaining_sp=remaining_sp,
            milestone_sp=highest_priority_sp,
            no_changes_seconds=no_changes_all_seconds,
        )
    if body.include_small_skill_injectors:
        _add_injector_strategies(
            strategies,
            "small",
            type_ids_by_name[SMALL_SKILL_INJECTOR_NAME],
            snapshots[type_ids_by_name[SMALL_SKILL_INJECTOR_NAME]],
            total_sp=total_sp,
            unallocated_sp=unallocated_sp,
            use_unallocated_sp=body.use_unallocated_sp,
            remaining_sp=remaining_sp,
            milestone_sp=highest_priority_sp,
            no_changes_seconds=no_changes_all_seconds,
        )

    nes_strategy = None
    nes_schedule: list[dict] = []
    if body.include_nes_accelerators:
        nes_strategy, nes_schedule = _add_nes_accelerator_strategies(
            strategies,
            body=body,
            time_result=time_result,
            snapshot=snapshot,
            snapshots=snapshots,
            type_ids_by_name=type_ids_by_name,
            plex_unit_price_isk=plex_unit,
            no_changes_seconds=no_changes_all_seconds,
        )
        if body.include_large_skill_injectors:
            large_snapshot = snapshots.get(type_ids_by_name.get(LARGE_SKILL_INJECTOR_NAME))
            _add_nes_large_injector_combinations(
                strategies,
                body=body,
                nes_strategy=nes_strategy,
                nes_schedule=nes_schedule,
                snapshot=snapshot,
                total_sp=total_sp,
                unallocated_sp=unallocated_sp,
                remaining_sp=remaining_sp,
                no_changes_seconds=no_changes_all_seconds,
                large_snapshot=large_snapshot,
            )

    if body.include_market_accelerators:
        strategies.append(
            strategy_result(
                strategy_id="market_accelerators_unavailable",
                description="Future tradeable accelerator model placeholder; no exact accelerator item was requested.",
                completion_seconds=no_changes_all_seconds,
                no_changes_seconds=no_changes_all_seconds,
                accelerators_isk=None,
                exact=False,
                warnings=["accelerator pricing requires an exact item/offer source; no price fabricated"],
            )
        )
    if body.include_plex_offers:
        warnings = []
        if plex_unit is None:
            warnings.append("PLEX Jita 4-4 price unavailable, so PLEX offers cannot be converted")
        strategies.append(
            strategy_result(
                strategy_id="plex_offers_unavailable",
                description="NES/pack offer provider placeholder; no reliable live offer catalog is configured.",
                completion_seconds=no_changes_all_seconds,
                no_changes_seconds=no_changes_all_seconds,
                plex_isk=None,
                exact=False,
                warnings=warnings + ["no live reliable NES offer source configured; old pack prices are not hardcoded"],
            )
        )

    recs = recommended_ids(strategies)
    frontier = pareto_frontier(strategies)
    return {
        "character_source": time_result["character_source"],
        "analysis_started_at": time_result["analysis_started_at"],
        "objective": body.objective,
        "economic_objective": body.economic_objective,
        "current_character": time_result["current_character"],
        "remaining_sp": remaining_sp,
        "market": market,
        "plex": {
            "type_id": type_ids_by_name[PLEX_NAME],
            "plex_unit_price_isk": plex_unit,
            "market_plex_unit_price_isk": market_plex_unit,
            "source": plex_source,
            "conversion_example": plex_to_isk(1, plex_unit) if plex_unit is not None else None,
        },
        "nes_accelerators": _nes_catalog_for_character(snapshot, body),
        "large_skill_injectors": {
            "enabled": body.include_large_skill_injectors,
            "mode": "auto" if body.optimize_large_injector_count else "manual",
            "requested_count": int(body.combo_large_injector_count),
        },
        "time": {
            "no_changes": time_result["no_changes"],
            "time_optimal": current_scenario,
        },
        "strategies": strategies,
        "pareto_frontier": frontier,
        **recs,
        "warnings": _economic_warnings(body, snapshots),
    }


def _normalize_implant_scenarios(items: list[str]) -> list[str]:
    allowed = {"current", "none", "+3", "+4", "+5"}
    scenarios = []
    for item in items:
        normalized = str(item).strip().lower()
        if normalized in {"3", "basic"}:
            normalized = "+3"
        elif normalized in {"4", "standard"}:
            normalized = "+4"
        elif normalized in {"5", "improved"}:
            normalized = "+5"
        if normalized not in allowed:
            raise HTTPException(400, f"Unknown implant scenario: {item}")
        if normalized not in scenarios:
            scenarios.append(normalized)
    if not scenarios:
        raise HTTPException(400, "At least one implant scenario is required")
    return scenarios


def _economic_type_ids(implant_scenarios: list[str], include_large: bool, include_small: bool) -> dict[str, int]:
    names = {PLEX_NAME}
    for scenario in implant_scenarios:
        if scenario in IMPLANT_SET_NAMES:
            names.update(IMPLANT_SET_NAMES[scenario])
    if include_large:
        names.add(LARGE_SKILL_INJECTOR_NAME)
    if include_small:
        names.add(SMALL_SKILL_INJECTOR_NAME)
    return {name: sde.type_id_by_name(name) for name in sorted(names)}


def _add_injector_strategies(
    strategies: list[dict],
    injector_type: str,
    type_id: int,
    snapshot,
    *,
    total_sp: int,
    unallocated_sp: int,
    use_unallocated_sp: bool,
    remaining_sp: int,
    milestone_sp: int,
    no_changes_seconds: float,
) -> None:
    unit_price = snapshot.sell.unit_price if snapshot.sell else None
    item_warnings = list(snapshot.warnings)
    targets = (
        ("all_remaining", remaining_sp, 0.0, True, "Enough injectors to cover all remaining plan SP."),
        (
            "highest_priority_milestone",
            milestone_sp,
            no_changes_seconds,
            False,
            "Injector count for the highest-priority goal. All-plan completion time is conservative until milestone-specific rescheduling is modeled.",
        ),
        (
            "hybrid_half_remaining",
            max(1, remaining_sp // 2),
            no_changes_seconds * 0.5,
            False,
            "Half-SP hybrid cost estimate. Completion time is an estimate and is excluded from automatic best-value recommendations.",
        ),
    )
    for suffix, target_sp, completion, exact_time, description in targets:
        plan = minimum_injectors_for_sp(
            total_sp_before=total_sp,
            target_sp=target_sp,
            injector_type=injector_type,
            unallocated_sp=unallocated_sp,
            use_unallocated_sp=use_unallocated_sp,
        )
        injectors_isk = unit_price * plan.count if unit_price is not None else None
        items = [
            {
                "type_id": type_id,
                "type_name": snapshot.type_name,
                "quantity": plan.count,
                "unit_price_isk": unit_price,
                "total_isk": injectors_isk,
                "source": snapshot.source,
                "fetched_at": snapshot.fetched_at.isoformat(),
                "available": unit_price is not None,
                "warnings": item_warnings,
            }
        ]
        strategies.append(
            strategy_result(
                strategy_id=f"{injector_type}_injector_{suffix}",
                description=description,
                completion_seconds=completion,
                no_changes_seconds=no_changes_seconds,
                injectors_isk=injectors_isk,
                items=items,
                sp_injected=plan.exact_sp_gained,
                exact=exact_time,
                recommendation_eligible=(suffix != "all_remaining"),
                informational=(suffix == "all_remaining"),
                warnings=item_warnings + [
                    f"injectors={plan.count}",
                    f"sp_gained={plan.exact_sp_gained}",
                    f"excess_sp={plan.excess_sp}",
                    f"unallocated_sp_used={plan.unallocated_sp_used}",
                    *( [] if exact_time else ["completion_time_is_estimate"] ),
                ],
            )
        )


def _biology_implant_duration_bonus(snapshot: CharacterSnapshot | None) -> float:
    if snapshot is None:
        return 0.0
    names = " | ".join(snapshot.implant_names).lower()
    if "by-810" in names:
        return 0.10
    if "by-805" in names:
        return 0.05
    return 0.0


def _biology_level_at(schedule_rows: list[dict], current_level: int, moment: datetime) -> int:
    level = max(0, min(5, int(current_level)))
    moment = _as_utc(moment)
    for row in schedule_rows:
        if int(row.get("skill_id") or 0) != 3405:
            continue
        finishes = row.get("finishes_at")
        if not finishes:
            continue
        finished_at = _as_utc(datetime.fromisoformat(finishes))
        if finished_at <= moment:
            level = max(level, int(row.get("target_level") or 0))
    return min(5, level)


def _nes_activation_windows(
    *,
    start_at: datetime,
    count: int,
    config: dict,
    current_biology_level: int,
    biology_implant_bonus: float,
    reference_schedule: list[dict],
) -> list[tuple[datetime, datetime, int]]:
    windows: list[tuple[datetime, datetime, int]] = []
    cursor = _as_utc(start_at)
    for _ in range(max(0, int(count))):
        level = _biology_level_at(reference_schedule, current_biology_level, cursor)
        seconds = accelerator_duration_seconds(config["base_days"], level, biology_implant_bonus)
        end = cursor + timedelta(seconds=seconds)
        windows.append((cursor, end, level))
        cursor = end
    return windows


def _add_nes_accelerator_strategies(
    strategies: list[dict],
    *,
    body: OptimizeEconomicsRequest,
    time_result: dict,
    snapshot: CharacterSnapshot | None,
    snapshots: dict[int, object],
    type_ids_by_name: dict[str, int],
    plex_unit_price_isk: float | None,
    no_changes_seconds: float,
) -> tuple[dict | None, list[dict]]:
    """Add exactly one user-selected NES accelerator scenario.

    Cerebral accelerators share one booster slot, so the UI/API models one
    selected type at a time. Repeated uses are sequential, never parallel.
    """
    plans = [parse_plan(p.text, name=p.name) for p in body.plans]
    for plan, request_plan in zip(plans, body.plans):
        plan.weight = float(request_plan.priority)

    if snapshot is not None:
        current_skills = snapshot.skills
        current_base = snapshot.base_attributes
        current_implants = snapshot.implant_attribute_bonus
        if current_implants.total == 0 and snapshot.implant_names:
            current_implants = resolve_attribute_implants(snapshot.implant_names).attribute_bonus
    else:
        current_skills = _current_skills_from_request(body.current_skills)
        current_base = AttributeSet(intelligence=20, memory=20, perception=20, willpower=20, charisma=19)
        current_implants = AttributeSet.zero()

    started_at = _as_utc(datetime.fromisoformat(time_result["analysis_started_at"]))
    graph = build_task_graph(plans, current_skills, sde.skills_by_id, sde.skills_by_name)
    remaps = remap_availability(snapshot, started_at)
    pinned = _current_queue_prefix(snapshot, graph, started_at) if body.respect_current_queue and snapshot else ()

    if snapshot is not None:
        expiry, current_modifiers, _bonus, ignored = _resolve_booster_runtime(
            body=body,
            snapshot=snapshot,
            implant=current_implants,
            analysis_started_at=started_at,
        )
        if current_modifiers and expiry.expires_at and not ignored:
            nes_start = max(started_at, _as_utc(expiry.expires_at))
        else:
            nes_start = started_at
    else:
        current_modifiers = ()
        nes_start = started_at

    scenario = _normalize_implant_scenarios(body.implant_scenarios)[0]
    implant_cost = implant_set_cost(
        scenario,
        snapshots,
        type_ids_by_name,
        owned_implant_names=snapshot.implant_names if snapshot else (),
    )
    biology = current_skills.get(3405)
    current_biology_level = int(biology.trained_level if biology else 0)
    biology_implant_bonus = _biology_implant_duration_bonus(snapshot)

    bonus = int(body.nes_accelerator_bonus)
    config = NES_ACCELERATORS[bonus]
    uniform = AttributeSet.uniform(bonus)
    exact_time = True
    activation_levels: list[int] = []

    if body.nes_accelerator_mode == "continuous":
        # Continuous maintenance is one uninterrupted stream of the same booster.
        # The next unit is consumed only after the previous unit expires.
        nes_modifier = TimedAttributeModifier(attribute_bonus=uniform, starts_at=nes_start)
        scenario_result = _optimize_implant_scenario(
            scenario,
            plans=plans,
            current_skills=current_skills,
            current_base=current_base,
            current_implants=current_implants,
            remaps=remaps,
            modifiers=tuple(current_modifiers) + (nes_modifier,),
            start_time=started_at,
            objective=body.objective,
            omega=body.omega,
            beam_width=body.beam_width,
            max_candidate_remaps=body.max_candidate_remaps,
            pinned_prefix=pinned,
            exact_booster=True,
        )
        completion = float(scenario_result["all_plans_completion_seconds"] or no_changes_seconds)
        plan_end = started_at + timedelta(seconds=completion)
        count = 0
        cursor = nes_start
        while cursor < plan_end:
            level = _biology_level_at(scenario_result["schedule"], current_biology_level, cursor)
            activation_levels.append(level)
            cursor += timedelta(seconds=accelerator_duration_seconds(config["base_days"], level, biology_implant_bonus))
            count += 1
    else:
        count = int(body.nes_accelerator_count)
        reference_schedule = next(
            (row.get("schedule") for row in time_result.get("scenarios", []) if row.get("scenario") == scenario),
            [],
        ) or []
        previous_signature = None
        scenario_result = None
        windows = []
        for _ in range(6):
            windows = _nes_activation_windows(
                start_at=nes_start,
                count=count,
                config=config,
                current_biology_level=current_biology_level,
                biology_implant_bonus=biology_implant_bonus,
                reference_schedule=reference_schedule,
            )
            modifiers = tuple(current_modifiers) + tuple(
                TimedAttributeModifier(attribute_bonus=uniform, starts_at=start, expires_at=end)
                for start, end, _level in windows
            )
            scenario_result = _optimize_implant_scenario(
                scenario,
                plans=plans,
                current_skills=current_skills,
                current_base=current_base,
                current_implants=current_implants,
                remaps=remaps,
                modifiers=modifiers,
                start_time=started_at,
                objective=body.objective,
                omega=body.omega,
                beam_width=body.beam_width,
                max_candidate_remaps=body.max_candidate_remaps,
                pinned_prefix=pinned,
                exact_booster=True,
            )
            signature = tuple((int(level), int(end.timestamp())) for _start, end, level in windows)
            if signature == previous_signature:
                break
            previous_signature = signature
            reference_schedule = scenario_result["schedule"]
        else:
            exact_time = False
        activation_levels = [level for _start, _end, level in windows]
        completion = float(scenario_result["all_plans_completion_seconds"] or no_changes_seconds)

    if count <= 0 or scenario_result is None:
        return None, []
    plex_quantity = int(config["plex"]) * count
    plex_isk = float(plex_quantity) * plex_unit_price_isk if plex_unit_price_isk is not None else None
    warnings = list(implant_cost["warnings"])
    if not exact_time:
        warnings.append("nes_activation_timing_not_converged")

    result = strategy_result(
        strategy_id=f"nes_accelerator_{bonus}_{body.nes_accelerator_mode}",
        description=(
            f"NES +{bonus} cerebral accelerator; "
            + ("maintained continuously until all goals finish." if body.nes_accelerator_mode == "continuous" else f"use {count} accelerator(s) sequentially.")
        ),
        completion_seconds=completion,
        no_changes_seconds=no_changes_seconds,
        implants_isk=implant_cost["implants_isk"],
        plex_isk=plex_isk,
        items=list(implant_cost["items"]),
        remap_plan=scenario_result.get("remap_actions") or [],
        exact=bool(scenario_result.get("exact")) and exact_time,
        warnings=warnings,
        plex_quantity=plex_quantity,
    )
    result["accelerator"] = {
        "name": config["name"],
        "attribute_bonus": bonus,
        "base_days": config["base_days"],
        "effective_duration_seconds_now": accelerator_duration_seconds(config["base_days"], current_biology_level, biology_implant_bonus),
        "plex_each": config["plex"],
        "count": count,
        "plex_quantity": plex_quantity,
        "mode": body.nes_accelerator_mode,
        "biology_level_start": current_biology_level,
        "biology_levels_at_activation": activation_levels,
        "biology_implant_duration_bonus": biology_implant_bonus,
        "starts_at": _iso(nes_start),
        "sequential_only": True,
    }
    strategies.append(result)
    return result, list(scenario_result.get("schedule") or [])


def _nes_catalog_for_character(snapshot: CharacterSnapshot | None, body: OptimizeEconomicsRequest) -> dict:
    if snapshot is not None:
        biology = snapshot.skills.get(3405)
        biology_level = int(biology.trained_level if biology else 0)
    else:
        skills = _current_skills_from_request(body.current_skills)
        biology = skills.get(3405)
        biology_level = int(biology.trained_level if biology else 0)
    implant_bonus = _biology_implant_duration_bonus(snapshot)
    catalog = []
    for bonus, config in sorted(NES_ACCELERATORS.items()):
        biology_only = accelerator_duration_seconds(config["base_days"], biology_level, 0.0)
        effective = accelerator_duration_seconds(config["base_days"], biology_level, implant_bonus)
        catalog.append({
            "bonus": bonus,
            **config,
            "biology_duration_seconds": biology_only,
            "biology_days": biology_only / 86400.0,
            "effective_duration_seconds": effective,
            "effective_days": effective / 86400.0,
            "biology_level": biology_level,
            "biology_implant_duration_bonus": implant_bonus,
            "selected": bonus == body.nes_accelerator_bonus,
        })
    return {
        "selected_bonus": body.nes_accelerator_bonus,
        "mode": body.nes_accelerator_mode,
        "requested_count": body.nes_accelerator_count,
        "biology_level": biology_level,
        "biology_implant_duration_bonus": implant_bonus,
        "catalog": catalog,
    }


def _add_nes_large_injector_combinations(
    strategies: list[dict],
    *,
    body: OptimizeEconomicsRequest,
    nes_strategy: dict | None,
    nes_schedule: list[dict],
    snapshot,
    total_sp: int,
    unallocated_sp: int,
    remaining_sp: int,
    no_changes_seconds: float,
    large_snapshot,
) -> None:
    if not nes_strategy or not nes_schedule or not body.include_large_skill_injectors:
        return
    unit_price = large_snapshot.sell.unit_price if large_snapshot and large_snapshot.sell else None
    if unit_price is None:
        return

    base_cost = float(nes_strategy["cost"]["total_isk"] or 0.0)
    base_implants = nes_strategy["cost"]["implants_isk"]
    base_plex = nes_strategy["cost"]["plex_isk"]
    free_sp = min(max(0, int(unallocated_sp)), max(0, int(remaining_sp))) if body.use_unallocated_sp else 0

    def build(count: int, strategy_id: str, description: str) -> dict:
        plan = injector_sp_for_count(
            total_sp_before=total_sp,
            count=count,
            injector_type="large",
            target_sp=remaining_sp,
            unallocated_sp=unallocated_sp,
            use_unallocated_sp=body.use_unallocated_sp,
        )
        usable = min(remaining_sp, plan.unallocated_sp_used + plan.exact_sp_gained)
        overlay = schedule_time_after_sp(nes_schedule, usable)
        injector_cost = float(unit_price) * count
        fully_bought = usable >= remaining_sp and remaining_sp > 0
        result = strategy_result(
            strategy_id=strategy_id,
            description=description,
            completion_seconds=overlay["completion_seconds"],
            no_changes_seconds=no_changes_seconds,
            implants_isk=base_implants,
            plex_isk=base_plex,
            injectors_isk=injector_cost,
            sp_injected=plan.exact_sp_gained,
            exact=True,
            recommendation_eligible=not fully_bought,
            informational=fully_bought,
            plex_quantity=nes_strategy.get("plex_quantity", 0),
            warnings=[],
        )
        result["accelerator"] = dict(nes_strategy.get("accelerator") or {})
        result["injector_combo"] = {
            "injector_type": "large",
            "count": count,
            "sp_gained": plan.exact_sp_gained,
            "unallocated_sp_used": plan.unallocated_sp_used,
            "sp_applied_to_plan": overlay["sp_used"],
            "sp_remaining_after": max(0, remaining_sp - overlay["sp_used"]),
            "excess_sp": max(plan.excess_sp, overlay["sp_unused"]),
            "unit_price_isk": unit_price,
            "injectors_isk": injector_cost,
            "calculation": "applied_from_start_of_recommended_queue",
        }
        return result

    manual_count = max(0, int(body.combo_large_injector_count))
    if manual_count > 0:
        strategies.append(build(
            manual_count,
            f"nes_accelerator_{body.nes_accelerator_bonus}_large_injector_manual_{manual_count}",
            f"Selected NES accelerator plus {manual_count} Large Skill Injector(s).",
        ))

    if not body.optimize_large_injector_count:
        return
    all_plan = minimum_injectors_for_sp(
        total_sp_before=total_sp,
        target_sp=remaining_sp,
        injector_type="large",
        unallocated_sp=unallocated_sp,
        use_unallocated_sp=body.use_unallocated_sp,
    )
    max_count = min(max(0, all_plan.count - 1), 1000)
    candidates = [nes_strategy]
    built: list[dict] = []
    for count in range(1, max_count + 1):
        candidate = build(
            count,
            f"nes_accelerator_{body.nes_accelerator_bonus}_large_injector_candidate_{count}",
            f"NES +{body.nes_accelerator_bonus} plus {count} Large Skill Injector(s).",
        )
        if candidate.get("recommendation_eligible"):
            candidates.append(candidate)
            built.append(candidate)
    def value(row: dict) -> tuple:
        eff = row.get("efficiency", {}).get("isk_per_day_saved")
        return (float(eff) if eff is not None else math.inf, float(row["cost"]["total_isk"] or math.inf), row["time"]["all_plans_completion_seconds"])
    best = min(candidates, key=value) if candidates else nes_strategy
    if best is nes_strategy:
        nes_strategy["optimal_large_injector_count"] = 0
        return
    best_count = int(best["injector_combo"]["count"])
    optimal = build(
        best_count,
        f"nes_accelerator_{body.nes_accelerator_bonus}_large_injector_optimal",
        f"NES +{body.nes_accelerator_bonus} plus automatically selected Large Skill Injectors.",
    )
    optimal["injector_combo"]["optimized_for"] = "lowest_isk_per_day_saved"
    strategies.append(optimal)


def _request_total_sp(items: list[dict]) -> int:
    return sum(int(item.get("skillpoints", 0)) for item in items)


def _schedule_all_seconds(rows: list[dict]) -> float:
    return sum(float(row.get("duration_seconds") or 0.0) for row in rows)


def _highest_priority_plan_sp(time_result: dict) -> int:
    plans = time_result["no_changes"]["plans"]
    if not plans:
        return time_result["summary"]["remaining_sp"]
    chosen = max(plans, key=lambda plan: (plan["priority"], plan["remaining_sp"], plan["name"]))
    return int(chosen["remaining_sp"])


def _economic_warnings(body: OptimizeEconomicsRequest, snapshots: dict[int, object]) -> list[str]:
    warnings = sorted({
        warning
        for snapshot in snapshots.values()
        for warning in getattr(snapshot, "warnings", ())
        if "sell order" not in str(warning).lower()
    })
    if body.include_market_accelerators:
        warnings.append("future accelerator model exists only as a provider/config placeholder until an exact item or offer source is supplied")
    if body.include_plex_offers:
        warnings.append("NES/pack offer prices are not sourced from ESI and are not hardcoded")
    return warnings


def _run_async(awaitable):
    try:
        return asyncio.run(awaitable)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(awaitable)
        finally:
            loop.close()


def _optimize_implant_scenario(
    scenario: str,
    *,
    plans: list[SkillPlan],
    current_skills: dict[int, CharacterSkill],
    current_base: AttributeSet,
    current_implants: AttributeSet,
    remaps: RemapAvailability,
    modifiers: tuple[TimedAttributeModifier, ...],
    start_time: datetime,
    objective: str,
    omega: bool,
    beam_width: int,
    max_candidate_remaps: int,
    pinned_prefix: tuple,
    exact_booster: bool,
) -> dict:
    scenario_bonus, required_cybernetics = _scenario_implants_and_cybernetics(scenario)
    if scenario == "current":
        scenario_bonus = current_implants
    scenario_plans = list(plans)
    current_cyber = current_skills.get(3411)
    current_cyber_level = current_cyber.trained_level if current_cyber else 0
    activation_keys: set[tuple[int, int]] = set()
    if required_cybernetics > current_cyber_level:
        cybernetics = sde.skills_by_id.get(3411)
        if cybernetics is None:
            raise RuntimeError("Cybernetics skill is missing from loaded SDE")
        scenario_plans.append(SkillPlan("__implant_cybernetics_prep__", targets=[PlanTarget("Cybernetics", required_cybernetics)]))

    graph = build_task_graph(scenario_plans, current_skills, sde.skills_by_id, sde.skills_by_name)
    prep_name = "__implant_cybernetics_prep__"
    if prep_name in graph.plan_required:
        activation_keys = set(graph.plan_required[prep_name])
        graph.plan_required.pop(prep_name, None)
        graph.plan_priority.pop(prep_name, None)

    active_immediately = required_cybernetics <= current_cyber_level
    result = optimize_training_time(
        graph,
        objective=objective,
        current_base=current_base,
        start_time=start_time,
        omega=omega,
        modifiers=modifiers,
        availability=remaps,
        beam_width=beam_width,
        max_candidate_remaps=max_candidate_remaps,
        activation_task_keys=activation_keys,
        current_implant_bonus=scenario_bonus if active_immediately else current_implants,
        scenario_implant_bonus=scenario_bonus,
        pinned_prefix=pinned_prefix,
    )
    no_remap_fallback = None
    if not activation_keys:
        no_remap = optimize_schedule(
            graph,
            objective=objective,
            base_attributes=current_base.plus(scenario_bonus),
            modifiers=modifiers,
            start_time=start_time,
            omega=omega,
            beam_width=beam_width,
            pinned_prefix=pinned_prefix,
        )
        if no_remap.objective_value <= result.objective_value:
            no_remap_fallback = no_remap

    order = no_remap_fallback.order if no_remap_fallback else result.order
    remap_actions = () if no_remap_fallback else result.remap_actions
    completion, makespan, rows = simulate_time_plan(
        graph,
        order,
        remap_actions,
        start_time=start_time,
        omega=omega,
        modifiers=modifiers,
        initial_base=current_base,
        activation_task_keys=activation_keys,
        current_implant_bonus=scenario_bonus if active_immediately else current_implants,
        scenario_implant_bonus=scenario_bonus,
    )
    cyber_training_seconds = sum(row["duration_seconds"] for row in rows if row["key"] in activation_keys)
    implant_activates_at = None
    if active_immediately:
        implant_activates_at = start_time
    elif activation_keys:
        for row in rows:
            if activation_keys <= {r["key"] for r in rows[: row["index"]]}:
                implant_activates_at = row["finishes_at"]
                break
        if implant_activates_at is None:
            completed = set()
            for row in rows:
                completed.add(row["key"])
                if activation_keys <= completed:
                    implant_activates_at = row["finishes_at"]
                    break

    scenario_response = {
        "scenario": scenario,
        "exact": exact_booster,
        "implant_attribute_bonus": scenario_bonus.__dict__,
        "required_cybernetics_level": required_cybernetics,
        "eligible_immediately": active_immediately,
        "cybernetics_training_seconds": cyber_training_seconds,
        "implant_activates_at": _iso(implant_activates_at),
        "optimizer": {
            "algorithm": no_remap_fallback.algorithm if no_remap_fallback else result.algorithm,
            "optimality": no_remap_fallback.optimality if no_remap_fallback else result.optimality,
            "states_evaluated": no_remap_fallback.states_evaluated if no_remap_fallback else result.states_evaluated,
            "beam_width": no_remap_fallback.beam_width if no_remap_fallback else result.beam_width,
            "candidate_maps_count": result.candidate_maps_count,
            "remap_actions_considered": result.remap_actions_considered,
            "objective_value": (no_remap_fallback.objective_value if no_remap_fallback else result.objective_value) if exact_booster else None,
        },
        "all_plans_completion_seconds": makespan if exact_booster else None,
        "all_plans_completion_days": makespan / 86400.0 if exact_booster else None,
        "plans": _schedule_plans_response(graph, completion, rows, start_time, exact_booster),
        "schedule": _time_rows_response(graph, rows, exact_booster),
        "remap_actions": [_remap_action_response(action) for action in remap_actions],
    }
    if not exact_booster:
        scenario_response["range"] = {
            "best_case_objective_value": result.objective_value,
            "worst_case_objective_value": result.objective_value,
            "best_case_all_plans_seconds": makespan,
            "worst_case_all_plans_seconds": makespan,
            "warning": "booster expiry is unknown; scenario order is heuristic without a fake exact timestamp",
        }
    return scenario_response


def _combine_time_scenario_range(best_case: dict, worst_case: dict) -> dict:
    """Represent an unknown-booster scenario without inventing an exact schedule.

    The two inputs are independently optimized exact calculations for the
    optimistic case (attribute booster remains active) and the conservative case
    (booster contributes nothing).  They may legitimately have different skill
    orders/remaps, so the public range result deliberately does not expose one of
    those plans as a supposedly exact recommendation.
    """
    best_seconds = best_case["all_plans_completion_seconds"]
    worst_seconds = worst_case["all_plans_completion_seconds"]
    if best_seconds is not None and worst_seconds is not None and best_seconds > worst_seconds:
        best_case, worst_case = worst_case, best_case
        best_seconds, worst_seconds = worst_seconds, best_seconds

    return {
        "scenario": best_case["scenario"],
        "exact": False,
        "implant_attribute_bonus": best_case["implant_attribute_bonus"],
        "required_cybernetics_level": best_case["required_cybernetics_level"],
        "eligible_immediately": best_case["eligible_immediately"],
        "cybernetics_training_seconds": None,
        "implant_activates_at": None,
        "optimizer": {
            "algorithm": "range",
            "optimality": "not_proven_due_unknown_booster_expiry",
            "states_evaluated": (best_case["optimizer"].get("states_evaluated") or 0)
            + (worst_case["optimizer"].get("states_evaluated") or 0),
            "beam_width": max(best_case["optimizer"].get("beam_width") or 0, worst_case["optimizer"].get("beam_width") or 0),
            "candidate_maps_count": max(best_case["optimizer"].get("candidate_maps_count") or 0, worst_case["optimizer"].get("candidate_maps_count") or 0),
            "remap_actions_considered": max(best_case["optimizer"].get("remap_actions_considered") or 0, worst_case["optimizer"].get("remap_actions_considered") or 0),
            "objective_value": None,
        },
        "all_plans_completion_seconds": None,
        "all_plans_completion_days": None,
        "plans": [],
        "schedule": [],
        "remap_actions": [],
        "recommendation_blocked": True,
        "recommendation_blocked_reason": "booster_expiry_unknown",
        "range": {
            "best_case_objective_value": best_case["optimizer"].get("objective_value"),
            "worst_case_objective_value": worst_case["optimizer"].get("objective_value"),
            "best_case_all_plans_seconds": best_seconds,
            "worst_case_all_plans_seconds": worst_seconds,
            "best_case_implant_activates_at": best_case.get("implant_activates_at"),
            "worst_case_implant_activates_at": worst_case.get("implant_activates_at"),
            "warning": "booster expiry is unknown; remap/implant timing is intentionally not presented as exact",
        },
        "technical_best_case": {
            "optimizer": best_case["optimizer"],
            "all_plans_completion_seconds": best_seconds,
        },
        "technical_worst_case": {
            "optimizer": worst_case["optimizer"],
            "all_plans_completion_seconds": worst_seconds,
        },
    }


def _scenario_implants_and_cybernetics(scenario: str) -> tuple[AttributeSet, int]:
    if scenario == "none":
        return AttributeSet.zero(), 0
    if scenario == "current":
        return AttributeSet.zero(), 0
    bonus = int(scenario[1:])
    return AttributeSet.uniform(bonus), sde.cybernetics_level_for_attribute_implants(bonus)


def _time_rows_response(graph, rows, exact: bool) -> list[dict]:
    out = []
    for row in rows:
        task = row["task"]
        action = row["remap_action"]
        out.append(
            {
                "index": row["index"],
                "skill_id": task.skill_id,
                "skill_name": task.skill_name,
                "target_level": task.level,
                "skill_name_ru": sde.skill_localized_name(task.skill_id, "ru"),
                "eve_clipboard_line": _eve_clipboard_line(task.skill_id, task.skill_name, task.level),
                "task_id": _task_id(row["key"]),
                "prerequisite_task_ids": [_task_id(dep) for dep in sorted(graph.deps[row["key"]])],
                "prerequisites": _prerequisite_details(graph, row["key"]),
                "remaining_sp": task.sp_remaining,
                "starts_at": _iso(row["starts_at"]) if exact else None,
                "finishes_at": _iso(row["finishes_at"]) if exact else None,
                "duration_seconds": row["duration_seconds"] if exact else None,
                "primary_attribute": task.primary.value,
                "secondary_attribute": task.secondary.value,
                "base_attributes": row["base_attributes"].__dict__,
                "implant_attribute_bonus": row["implant_attribute_bonus"].__dict__,
                "remap_action": _remap_action_response(action) if action else None,
                "completes_plans": row["completes_plans"],
            }
        )
    return out


def _remap_availability_response(remaps: RemapAvailability) -> dict:
    return {
        "timed_available_now": remaps.timed_available_now,
        "timed_available_at": _iso(remaps.timed_available_at),
        "bonus_remaps": remaps.bonus_remaps,
        "source": remaps.source,
        "warnings": list(remaps.warnings),
    }


def _remap_action_response(action) -> dict:
    return {
        "index": action.index,
        "kind": action.kind,
        "occurs_at": _iso(action.occurs_at),
        "base_attributes": action.base_attributes.__dict__,
    }


def _optimizer_response(result, exact: bool, candidate_maps_count: int, remap_actions_considered: int) -> dict:
    return {
        "algorithm": result.algorithm,
        "optimality": result.optimality,
        "states_evaluated": result.states_evaluated,
        "beam_width": result.beam_width,
        "candidate_maps_count": candidate_maps_count,
        "remap_actions_considered": remap_actions_considered,
        "objective_value": result.objective_value if exact else None,
    }


def _iso(value):
    return value.isoformat() if value else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _effective_booster_bonus(body, snapshot: CharacterSnapshot) -> AttributeSet:
    override = getattr(body, "booster_attribute_bonus_override", None)
    if override is not None:
        return AttributeSet.uniform(int(override))
    return snapshot.booster_attribute_bonus


def _resolve_booster_expiry(
    *,
    body: AnalyzeRequest,
    snapshot: CharacterSnapshot,
    implant: AttributeSet,
    booster_bonus: AttributeSet,
    analysis_started_at: datetime,
) -> BoosterExpiryInference:
    if booster_bonus.total <= 0:
        if body.booster_expires_at_override is not None:
            raise HTTPException(
                400,
                "booster_expires_at_override was supplied, but booster strength is unknown; "
                "import the booster strength or set booster_attribute_bonus_override",
            )
        return BoosterExpiryInference(None, None, None, {})
    if body.booster_expires_at_override is not None:
        override = _as_utc(body.booster_expires_at_override)
        if override <= analysis_started_at:
            raise HTTPException(400, "booster_expires_at_override must be in the future")
        return BoosterExpiryInference(
            override,
            "manual_override",
            "high",
            {"method": "user_supplied_timezone_aware_rfc3339"},
        )
    direct = next((booster.expires_at for booster in snapshot.active_boosters if booster.expires_at is not None), None)
    if direct is not None:
        return BoosterExpiryInference(
            _as_utc(direct),
            "evemon_direct",
            "high",
            {"method": "evemon_active_booster_expires_at"},
        )
    return infer_attribute_booster_expiry(
        now=analysis_started_at,
        current_skills=snapshot.skills,
        queue=snapshot.skill_queue,
        base_attributes=snapshot.base_attributes,
        implant_bonus=implant,
        booster_bonus=booster_bonus,
        skill_catalog=sde.skills_by_id,
        omega=body.omega,
    )


def _effective_booster_modifiers(booster_bonus: AttributeSet, expires_at: datetime | None) -> tuple[TimedAttributeModifier, ...]:
    if booster_bonus.total <= 0:
        return ()
    return (TimedAttributeModifier(attribute_bonus=booster_bonus, expires_at=expires_at),)


def _resolve_booster_runtime(
    *,
    body,
    snapshot: CharacterSnapshot,
    implant: AttributeSet,
    analysis_started_at: datetime,
) -> tuple[BoosterExpiryInference, tuple[TimedAttributeModifier, ...], AttributeSet, bool]:
    """Resolve the booster state used by calculations.

    EVEMon may expose booster strength but not expiration. The user can override
    either piece independently: a manual remaining-time/date override supplies
    the expiration, while booster_attribute_bonus_override supplies one uniform
    attribute bonus applied to all five attributes.
    """
    if getattr(body, "ignore_active_booster", False):
        if getattr(body, "booster_expires_at_override", None) is not None:
            raise HTTPException(400, "Choose either booster_expires_at_override or ignore_active_booster, not both")
        return (
            BoosterExpiryInference(
                None,
                "ignored_by_user",
                "user",
                {"ignored_by_user": True},
                (),
            ),
            (),
            AttributeSet.zero(),
            True,
        )

    booster_bonus = _effective_booster_bonus(body, snapshot)
    expiry = _resolve_booster_expiry(
        body=body,
        snapshot=snapshot,
        implant=implant,
        booster_bonus=booster_bonus,
        analysis_started_at=analysis_started_at,
    )
    modifiers = _effective_booster_modifiers(booster_bonus, expiry.expires_at)
    return expiry, modifiers, booster_bonus, False


def _duration_summary(
    *,
    tasks,
    base_with_implants: AttributeSet,
    booster_bonus: AttributeSet,
    booster_modifiers: tuple[TimedAttributeModifier, ...],
    expiry: BoosterExpiryInference,
    omega: bool,
    analysis_started_at: datetime,
) -> dict:
    if booster_bonus.total > 0 and not expiry.exact:
        best_case = timeline_task_seconds(
            tasks,
            base_with_implants,
            omega=omega,
            modifiers=(TimedAttributeModifier(booster_bonus),),
            start_time=analysis_started_at,
        )
        worst_case = timeline_task_seconds(
            tasks,
            base_with_implants,
            omega=omega,
            modifiers=(),
            start_time=analysis_started_at,
        )
        return {
            "exact": False,
            "seconds": None,
            "days": None,
            "best_case_seconds": best_case,
            "worst_case_seconds": worst_case,
            "best_case_days": best_case / 86400.0,
            "worst_case_days": worst_case / 86400.0,
        }
    seconds = timeline_task_seconds(
        tasks,
        base_with_implants,
        omega=omega,
        modifiers=booster_modifiers,
        start_time=analysis_started_at,
    )
    return {
        "exact": True,
        "seconds": seconds,
        "days": seconds / 86400.0,
    }


def _booster_modifiers(boosters: tuple[ActiveBooster, ...], *, expires_at: datetime | None = None) -> tuple[TimedAttributeModifier, ...]:
    return tuple(
        TimedAttributeModifier(attribute_bonus=booster.attribute_bonus, expires_at=expires_at or booster.expires_at)
        for booster in boosters
        if booster.attribute_bonus.total > 0
    )


def _active_modifier_bonus(modifiers: tuple[TimedAttributeModifier, ...], now: datetime) -> AttributeSet:
    totals = {name.value: 0 for name in AttributeName}
    now = _as_utc(now)
    for modifier in modifiers:
        starts_at = _as_utc(modifier.starts_at) if modifier.starts_at else None
        expires_at = _as_utc(modifier.expires_at) if modifier.expires_at else None
        if starts_at and starts_at > now:
            continue
        if expires_at and expires_at <= now:
            continue
        for name in AttributeName:
            totals[name.value] += modifier.attribute_bonus.value(name)
    return AttributeSet(**totals)


def _boosters_response(
    boosters: tuple[ActiveBooster, ...],
    now: datetime,
    expiry: BoosterExpiryInference,
    *,
    ignored: bool = False,
    effective_bonus: AttributeSet | None = None,
    manual_bonus_override: int | None = None,
) -> dict:
    active = []
    unknown = []
    now = _as_utc(now)
    imported_bonus = _active_modifier_bonus(_booster_modifiers(boosters, expires_at=expiry.expires_at), now)
    resolved_bonus = effective_bonus if effective_bonus is not None else imported_bonus
    if expiry.expires_at is not None and _as_utc(expiry.expires_at) <= now:
        resolved_bonus = AttributeSet.zero()
    active_bonus = AttributeSet.zero() if ignored else resolved_bonus

    for booster in boosters:
        expires_at = expiry.expires_at or booster.expires_at
        expired = bool(expires_at and _as_utc(expires_at) <= now)
        item_bonus = resolved_bonus if manual_bonus_override is not None else booster.attribute_bonus
        item = {
            "name": booster.name,
            "attribute_bonus": AttributeSet.zero().__dict__ if expired else item_bonus.__dict__,
            "expires_at": _iso(expires_at),
            "source": "manual_bonus_override" if manual_bonus_override is not None else booster.source,
            "data_unavailable": list(booster.data_unavailable),
        }
        if item_bonus.total > 0 and not expired:
            active.append(item)
        elif booster.name:
            unknown.append(booster.name)

    # If EVEMon did not expose an active booster record but the user supplied the
    # strength manually, still expose one synthetic active item for UI/debugging.
    if not boosters and resolved_bonus.total > 0:
        active.append({
            "name": None,
            "attribute_bonus": resolved_bonus.__dict__,
            "expires_at": _iso(expiry.expires_at),
            "source": "manual_bonus_override" if manual_bonus_override is not None else "manual",
            "data_unavailable": ["name"],
        })

    return {
        "active": active,
        "attribute_bonus": active_bonus.__dict__,
        "imported_attribute_bonus": imported_bonus.__dict__,
        "effective_attribute_bonus": resolved_bonus.__dict__,
        "attribute_bonus_source": "manual_override" if manual_bonus_override is not None else "evemon",
        "manual_attribute_bonus_override": manual_bonus_override,
        "ignored": ignored,
        "expires_at": _iso(expiry.expires_at),
        "expiry_source": expiry.source,
        "expiry_confidence": expiry.confidence,
        "expiry_evidence": expiry.evidence,
        "unknown_boosters": unknown,
        "warnings": list(expiry.warnings),
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _skill_queue_entry_to_dict(entry: SkillQueueEntry) -> dict:
    return {
        "skill_id": entry.skill_id,
        "level": entry.level,
        "start_sp": entry.start_sp,
        "end_sp": entry.end_sp,
        "start_time": _iso(entry.start_time),
        "finish_time": _iso(entry.finish_time),
    }


def _current_skills_from_request(items: list[dict]) -> dict[int, CharacterSkill]:
    return {
        int(s["skill_id"]): CharacterSkill(
            skill_id=int(s["skill_id"]),
            trained_level=int(s.get("trained_level", 0)),
            active_level=int(s.get("active_level", s.get("trained_level", 0))),
            skillpoints=int(s.get("skillpoints", 0)),
        )
        for s in items
    }


def _schedule_summary(graph) -> dict:
    shared = graph.shared_tasks
    return {
        "plans_count": len(graph.plan_required),
        "unique_tasks": len(graph.tasks),
        "remaining_sp": sum(task.sp_remaining for task in graph.tasks.values()),
        "shared_tasks": len(shared),
        "shared_unique_sp": sum(graph.tasks[key].sp_remaining for key in shared),
    }


def _schedule_plans_response(graph, completion, rows, analysis_started_at: datetime, exact: bool) -> list[dict]:
    index_by_plan = {}
    for row in rows:
        for name in row["completes_plans"]:
            index_by_plan[name] = row["index"]
    out = []
    for name, keys in graph.plan_required.items():
        seconds = completion.get(name)
        out.append(
            {
                "name": name,
                "priority": graph.plan_priority[name],
                "remaining_sp": sum(graph.tasks[key].sp_remaining for key in keys),
                "completion_seconds": seconds if exact else None,
                "completion_days": seconds / 86400.0 if exact and seconds is not None else None,
                "completion_at": _iso(analysis_started_at + _seconds_delta(seconds)) if exact and seconds is not None else None,
                "completed_after_task_index": index_by_plan.get(name, 0 if not keys else None),
            }
        )
    return out


def _eve_clipboard_line(skill_id: int, canonical_name: str, level: int) -> str:
    localized_name = sde.skill_localized_name(skill_id, "ru")
    hint = html.escape(str(canonical_name), quote=True)
    visible = html.escape(str(localized_name), quote=False)
    return f'<localized hint="{hint}">{visible}*</localized> {int(level)}'


def _schedule_rows_response(graph, rows, exact: bool) -> list[dict]:
    out = []
    done = set()
    for row in rows:
        task = row["task"]
        key = row["key"]
        newly_unlocked = sorted(
            name
            for name, required in graph.plan_required.items()
            if name not in row["completes_plans"] and key in required
        )
        out.append(
            {
                "index": row["index"],
                "skill_id": task.skill_id,
                "skill_name": task.skill_name,
                "target_level": task.level,
                "skill_name_ru": sde.skill_localized_name(task.skill_id, "ru"),
                "eve_clipboard_line": _eve_clipboard_line(task.skill_id, task.skill_name, task.level),
                "task_id": _task_id(row["key"]),
                "prerequisite_task_ids": [_task_id(dep) for dep in sorted(graph.deps[row["key"]])],
                "prerequisites": _prerequisite_details(graph, row["key"]),
                "remaining_sp": task.sp_remaining,
                "starts_at": _iso(row["starts_at"]) if exact else None,
                "finishes_at": _iso(row["finishes_at"]) if exact else None,
                "duration_seconds": row["duration_seconds"] if exact else None,
                "primary_attribute": task.primary.value,
                "secondary_attribute": task.secondary.value,
                "unlocks_plans": newly_unlocked,
                "completes_plans": row["completes_plans"],
            }
        )
        done.add(key)
    return out


def _schedule_plans_range_response(
    graph,
    best_completion,
    worst_completion,
    rows,
    analysis_started_at: datetime,
) -> list[dict]:
    out = _schedule_plans_response(graph, best_completion, rows, analysis_started_at, False)
    by_name = {item["name"]: item for item in out}
    for name in graph.plan_required:
        item = by_name[name]
        best = best_completion.get(name)
        worst = worst_completion.get(name)
        item.update(
            {
                "completion_best_case_seconds": best,
                "completion_worst_case_seconds": worst,
                "completion_best_case_days": best / 86400.0 if best is not None else None,
                "completion_worst_case_days": worst / 86400.0 if worst is not None else None,
                "completion_best_case_at": _iso(analysis_started_at + _seconds_delta(best)) if best is not None else None,
                "completion_worst_case_at": _iso(analysis_started_at + _seconds_delta(worst)) if worst is not None else None,
            }
        )
    return out


def _schedule_rows_range_response(graph, best_rows, worst_rows) -> list[dict]:
    out = _schedule_rows_response(graph, best_rows, False)
    worst_by_key = {row["key"]: row for row in worst_rows}
    for item, best_row in zip(out, best_rows):
        worst_row = worst_by_key.get(best_row["key"])
        if worst_row is None:
            continue
        item.update(
            {
                "duration_best_case_seconds": best_row["duration_seconds"],
                "duration_worst_case_seconds": worst_row["duration_seconds"],
                "starts_at_best_case": _iso(best_row["starts_at"]),
                "starts_at_worst_case": _iso(worst_row["starts_at"]),
                "finishes_at_best_case": _iso(best_row["finishes_at"]),
                "finishes_at_worst_case": _iso(worst_row["finishes_at"]),
            }
        )
    return out


def _baseline_response(result, graph, analysis_started_at: datetime, exact: bool) -> dict:
    return {
        "weighted_completion_objective": result.objective_value if exact else None,
        "all_plans_completion_seconds": result.makespan_seconds if exact else None,
        "all_plans_completion_days": result.makespan_seconds / 86400.0 if exact else None,
        "plan_completion_seconds": result.plan_completion_seconds if exact else {name: None for name in graph.plan_required},
        "plan_completion_days": {name: seconds / 86400.0 for name, seconds in result.plan_completion_seconds.items()} if exact else {name: None for name in graph.plan_required},
        "plan_completion_at": {
            name: _iso(analysis_started_at + _seconds_delta(seconds))
            for name, seconds in result.plan_completion_seconds.items()
        } if exact else {name: None for name in graph.plan_required},
    }


def _schedule_comparison(input_baseline, optimized, graph, exact: bool) -> dict:
    if not exact:
        return {
            "objective_improvement_vs_input_plan_order": None,
            "days_saved_to_first_plan_completion": None,
            "days_saved_to_all_plans_completion": None,
        }
    input_first = min(input_baseline.plan_completion_seconds.values(), default=0.0)
    opt_first = min(optimized.plan_completion_seconds.values(), default=0.0)
    return {
        "objective_improvement_vs_input_plan_order": input_baseline.objective_value - optimized.objective_value,
        "days_saved_to_first_plan_completion": (input_first - opt_first) / 86400.0,
        "days_saved_to_all_plans_completion": (input_baseline.makespan_seconds - optimized.makespan_seconds) / 86400.0,
    }


def _current_queue_prefix(snapshot: CharacterSnapshot, graph, now: datetime):
    now = _as_utc(now)
    for entry in snapshot.skill_queue:
        start = _as_utc(entry.start_time) if entry.start_time else None
        finish = _as_utc(entry.finish_time) if entry.finish_time else None
        if finish and finish > now and (start is None or start <= now):
            key = (entry.skill_id, entry.level)
            return (key,) if key in graph.tasks else ()
    return ()


def _seconds_delta(seconds: float):
    from datetime import timedelta

    return timedelta(seconds=seconds)


def _task_id(key) -> str:
    return f"{key[0]}:{key[1]}"


def _prerequisite_details(graph, key) -> list[dict]:
    details = []
    for dep in sorted(graph.deps.get(key, ())):
        task = graph.tasks.get(dep)
        if task is None:
            continue
        details.append(
            {
                "task_id": _task_id(dep),
                "skill_id": task.skill_id,
                "skill_name": task.skill_name,
                "level": task.level,
            }
        )
    return details


@app.get("/api/auth/login", include_in_schema=False)
async def auth_login():
    url, state, verifier = await authorization_url()
    oauth_states[state] = verifier
    return RedirectResponse(url)


@app.get("/api/auth/callback", include_in_schema=False)
async def auth_callback(code: str, state: str):
    verifier = oauth_states.pop(state, None)
    if not verifier:
        raise HTTPException(400, "Invalid or expired OAuth state")
    tokens = await exchange_code(code, verifier)
    character_id = character_id_from_access_token(tokens["access_token"])
    return {"ok": True, "character_id": character_id, "access_token_received": True}
