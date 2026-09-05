from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import count

from .models import AttributeName, AttributeSet, CharacterSnapshot
from .remap import valid_base_remaps
from .scheduler import TaskGraph, TaskKey, simulate_order
from .training_math import TimedAttributeModifier, timeline_task_seconds

NORMAL_REMAP_COOLDOWN_DAYS = 365
EXACT_REMAP_TASK_LIMIT = 8
DEFAULT_REMAP_BEAM_WIDTH = 192
DEFAULT_MAX_CANDIDATE_REMAPS = 24


@dataclass(frozen=True)
class RemapAvailability:
    timed_available_now: bool
    timed_available_at: datetime | None
    bonus_remaps: int
    source: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RemapAction:
    index: int
    kind: str
    occurs_at: datetime
    base_attributes: AttributeSet


@dataclass(frozen=True)
class TimeOptimizationResult:
    order: tuple[TaskKey, ...]
    remap_actions: tuple[RemapAction, ...]
    algorithm: str
    optimality: str
    states_evaluated: int
    beam_width: int | None
    candidate_maps_count: int
    remap_actions_considered: int
    objective_value: float
    makespan_seconds: float
    plan_completion_seconds: dict[str, float]


def remap_availability(snapshot: CharacterSnapshot | None, now: datetime) -> RemapAvailability:
    now = _as_utc(now)
    if snapshot is None:
        return RemapAvailability(False, None, 0, "request", ("manual/request character has no remap metadata",))

    warnings: list[str] = []
    source = "evemon"
    bonus = max(0, int(snapshot.bonus_remaps or 0))
    if snapshot.accrued_remap_cooldown_date is not None:
        timed_at = _as_utc(snapshot.accrued_remap_cooldown_date)
        source = "evemon_accrued_remap_cooldown_date"
    elif snapshot.evemon_last_timed_respec is not None:
        timed_at = _as_utc(snapshot.evemon_last_timed_respec) + timedelta(days=NORMAL_REMAP_COOLDOWN_DAYS)
        source = "evemon_last_timed_respec_plus_365d"
    elif snapshot.last_remap_date is not None:
        timed_at = _as_utc(snapshot.last_remap_date) + timedelta(days=NORMAL_REMAP_COOLDOWN_DAYS)
        source = "evemon_last_remap_date_plus_365d"
        warnings.append("last_remap_date may include bonus remaps in some sources; timed remap availability is conservative")
    else:
        timed_at = None
        source = "missing_remap_dates"
        warnings.append("timed remap availability is unknown because no remap dates were imported")

    return RemapAvailability(
        timed_available_now=bool(timed_at and timed_at <= now),
        timed_available_at=timed_at,
        bonus_remaps=bonus,
        source=source,
        warnings=tuple(warnings),
    )


def candidate_remaps(
    graph: TaskGraph,
    current_base: AttributeSet,
    *,
    max_candidate_remaps: int = DEFAULT_MAX_CANDIDATE_REMAPS,
) -> tuple[AttributeSet, ...]:
    candidates: list[AttributeSet] = [current_base]
    all_maps = list(valid_base_remaps())
    remaining_tasks = list(graph.tasks.values())
    pairs = sorted({(task.primary, task.secondary) for task in remaining_tasks}, key=lambda p: (p[0].value, p[1].value))

    if remaining_tasks:
        candidates.append(_best_map_for_tasks(all_maps, remaining_tasks))
    for primary, secondary in pairs:
        pair_tasks = [task for task in remaining_tasks if task.primary == primary and task.secondary == secondary]
        candidates.append(_best_map_for_tasks(all_maps, pair_tasks))

    scored = sorted(
        all_maps,
        key=lambda attrs: (_static_training_score(remaining_tasks, attrs), _attrs_key(attrs)),
    )
    candidates.extend(scored[: max(0, max_candidate_remaps)])
    return _dedupe_maps(candidates)[: max(1, max_candidate_remaps)]


def optimize_training_time(
    graph: TaskGraph,
    *,
    objective: str,
    current_base: AttributeSet,
    start_time: datetime,
    omega: bool,
    modifiers: tuple[TimedAttributeModifier, ...],
    availability: RemapAvailability,
    beam_width: int = DEFAULT_REMAP_BEAM_WIDTH,
    max_candidate_remaps: int = DEFAULT_MAX_CANDIDATE_REMAPS,
    activation_task_keys: set[TaskKey] | None = None,
    current_implant_bonus: AttributeSet | None = None,
    scenario_implant_bonus: AttributeSet | None = None,
    pinned_prefix: tuple[TaskKey, ...] = (),
) -> TimeOptimizationResult:
    if objective not in {"weighted_completion_time", "makespan", "priority_lexicographic"}:
        raise ValueError(f"Unknown schedule objective: {objective}")
    start_time = _as_utc(start_time)
    candidate_maps = candidate_remaps(graph, current_base, max_candidate_remaps=max_candidate_remaps)
    exact = len(graph.tasks) <= EXACT_REMAP_TASK_LIMIT
    return _search(
        graph,
        objective=objective,
        start_time=start_time,
        omega=omega,
        modifiers=modifiers,
        availability=availability,
        candidate_maps=candidate_maps,
        beam_width=min(beam_width, DEFAULT_REMAP_BEAM_WIDTH),
        exact=exact,
        activation_task_keys=activation_task_keys or set(),
        current_implant_bonus=current_implant_bonus or AttributeSet.zero(),
        scenario_implant_bonus=scenario_implant_bonus or AttributeSet.zero(),
        pinned_prefix=pinned_prefix,
    )


def simulate_time_plan(
    graph: TaskGraph,
    order: tuple[TaskKey, ...],
    remap_actions: tuple[RemapAction, ...],
    *,
    start_time: datetime,
    omega: bool,
    modifiers: tuple[TimedAttributeModifier, ...],
    initial_base: AttributeSet,
    activation_task_keys: set[TaskKey] | None = None,
    current_implant_bonus: AttributeSet | None = None,
    scenario_implant_bonus: AttributeSet | None = None,
) -> tuple[dict[str, float], float, list[dict]]:
    action_by_index = {action.index: action for action in remap_actions}
    done: set[TaskKey] = set()
    current = _as_utc(start_time)
    elapsed = 0.0
    base = initial_base
    rows: list[dict] = []
    completion: dict[str, float] = {}
    activation_task_keys = activation_task_keys or set()
    current_implant_bonus = current_implant_bonus or AttributeSet.zero()
    scenario_implant_bonus = scenario_implant_bonus or AttributeSet.zero()

    for index, key in enumerate(order, 1):
        action = action_by_index.get(index)
        if action:
            base = action.base_attributes
        if not graph.deps[key] <= done:
            raise RuntimeError(f"Illegal schedule order at {key}")
        task = graph.tasks[key]
        implant = scenario_implant_bonus if _implants_active(done, activation_task_keys) else current_implant_bonus
        starts_at = current
        seconds = timeline_task_seconds([task], base.plus(implant), omega=omega, modifiers=modifiers, start_time=current)
        current = current + timedelta(seconds=seconds)
        elapsed += seconds
        done.add(key)
        completes = sorted(name for name, keys in graph.plan_required.items() if name not in completion and keys <= done)
        for name in completes:
            completion[name] = elapsed
        rows.append(
            {
                "index": index,
                "key": key,
                "task": task,
                "starts_at": starts_at,
                "finishes_at": current,
                "duration_seconds": seconds,
                "base_attributes": base,
                "implant_attribute_bonus": implant,
                "remap_action": action,
                "completes_plans": completes,
            }
        )
    for name in graph.plan_required:
        completion.setdefault(name, elapsed)
    return completion, elapsed, rows


@dataclass(frozen=True)
class _State:
    done: frozenset[TaskKey]
    order: tuple[TaskKey, ...]
    elapsed: float
    current_time: datetime
    base: AttributeSet
    timed_available_at: datetime | None
    bonus_remaps: int
    remaps: tuple[RemapAction, ...]
    plan_completion: tuple[tuple[str, float], ...]


def _search(
    graph: TaskGraph,
    *,
    objective: str,
    start_time: datetime,
    omega: bool,
    modifiers: tuple[TimedAttributeModifier, ...],
    availability: RemapAvailability,
    candidate_maps: tuple[AttributeSet, ...],
    beam_width: int,
    exact: bool,
    activation_task_keys: set[TaskKey],
    current_implant_bonus: AttributeSet,
    scenario_implant_bonus: AttributeSet,
    pinned_prefix: tuple[TaskKey, ...],
) -> TimeOptimizationResult:
    initial = _State(
        done=frozenset(),
        order=(),
        elapsed=0.0,
        current_time=start_time,
        base=candidate_maps[0],
        timed_available_at=availability.timed_available_at,
        bonus_remaps=availability.bonus_remaps,
        remaps=(),
        plan_completion=(),
    )
    for key in pinned_prefix:
        if key in graph.tasks and graph.deps[key] <= set(initial.done):
            initial = _advance_state(
                graph,
                initial,
                key,
                base=initial.base,
                remap_kind=None,
                objective=objective,
                omega=omega,
                modifiers=modifiers,
                activation_task_keys=activation_task_keys,
                current_implant_bonus=current_implant_bonus,
                scenario_implant_bonus=scenario_implant_bonus,
            )

    states = [initial]
    states_evaluated = 0
    remap_actions_considered = 0
    serial = count()

    while states and len(states[0].done) < len(graph.tasks):
        next_states: list[tuple[tuple, int, _State]] = []
        for state in states:
            ready = [key for key in graph.tasks if key not in state.done and graph.deps[key] <= set(state.done)]
            ready.sort(key=lambda key: (graph.tasks[key].sp_remaining, key[0], key[1]))
            remap_options = [(state.base, None)]
            remap_kind = _available_remap_kind(state, state.current_time)
            if remap_kind is not None:
                for base in candidate_maps:
                    if base != state.base:
                        remap_options.append((base, remap_kind))
                remap_actions_considered += max(0, len(remap_options) - 1) * len(ready)
            for base, kind in remap_options:
                for key in ready:
                    new_state = _advance_state(
                        graph,
                        state,
                        key,
                        base=base,
                        remap_kind=kind,
                        objective=objective,
                        omega=omega,
                        modifiers=modifiers,
                        activation_task_keys=activation_task_keys,
                        current_implant_bonus=current_implant_bonus,
                        scenario_implant_bonus=scenario_implant_bonus,
                    )
                    states_evaluated += 1
                    next_states.append((_partial_score(graph, new_state, objective), next(serial), new_state))
        next_states.sort(key=lambda item: (item[0], item[1]))
        if exact:
            states = [item[2] for item in next_states]
        else:
            states = _dominance_prune([item[2] for item in next_states[: beam_width * 4]], keep_per_key=3)[:beam_width]

    if not states:
        raise RuntimeError("Training time optimizer failed")

    scored = []
    for state in states:
        completion = dict(state.plan_completion)
        for name in graph.plan_required:
            completion.setdefault(name, state.elapsed)
        scored.append((_score_tuple(graph, completion, state.elapsed, objective, state.order, state.remaps), state, completion))
    scored.sort(key=lambda item: item[0])
    _, best, completion = scored[0]
    return TimeOptimizationResult(
        order=best.order,
        remap_actions=best.remaps,
        algorithm="exact_joint_remap" if exact else "beam_joint_remap",
        optimality="proven" if exact else "heuristic",
        states_evaluated=states_evaluated,
        beam_width=None if exact else beam_width,
        candidate_maps_count=len(candidate_maps),
        remap_actions_considered=remap_actions_considered,
        objective_value=_objective_value(graph, completion, best.elapsed, objective),
        makespan_seconds=best.elapsed,
        plan_completion_seconds=completion,
    )


def _advance_state(
    graph: TaskGraph,
    state: _State,
    key: TaskKey,
    *,
    base: AttributeSet,
    remap_kind: str | None,
    objective: str,
    omega: bool,
    modifiers: tuple[TimedAttributeModifier, ...],
    activation_task_keys: set[TaskKey],
    current_implant_bonus: AttributeSet,
    scenario_implant_bonus: AttributeSet,
) -> _State:
    timed_at = state.timed_available_at
    bonus = state.bonus_remaps
    remaps = state.remaps
    if remap_kind is not None:
        if remap_kind == "timed":
            timed_at = state.current_time + timedelta(days=NORMAL_REMAP_COOLDOWN_DAYS)
        else:
            bonus -= 1
        remaps = remaps + (RemapAction(len(state.order) + 1, remap_kind, state.current_time, base),)

    implant = scenario_implant_bonus if _implants_active(set(state.done), activation_task_keys) else current_implant_bonus
    task = graph.tasks[key]
    seconds = timeline_task_seconds([task], base.plus(implant), omega=omega, modifiers=modifiers, start_time=state.current_time)
    done = frozenset((*state.done, key))
    elapsed = state.elapsed + seconds
    current_time = state.current_time + timedelta(seconds=seconds)
    completion = dict(state.plan_completion)
    for name, keys in graph.plan_required.items():
        if name not in completion and keys <= set(done):
            completion[name] = elapsed
    return _State(done, state.order + (key,), elapsed, current_time, base, timed_at, bonus, remaps, tuple(sorted(completion.items())))


def _available_remap_kind(state: _State, now: datetime) -> str | None:
    if state.timed_available_at is not None and state.timed_available_at <= now:
        return "timed"
    if state.bonus_remaps > 0:
        return "bonus"
    return None


def _implants_active(done: set[TaskKey], activation_task_keys: set[TaskKey]) -> bool:
    return not activation_task_keys or activation_task_keys <= done


def _best_map_for_tasks(maps: list[AttributeSet], tasks) -> AttributeSet:
    return min(maps, key=lambda attrs: (_static_training_score(tasks, attrs), _attrs_key(attrs)))


def _static_training_score(tasks, attrs: AttributeSet) -> float:
    score = 0.0
    for task in tasks:
        rate = attrs.value(task.primary) + attrs.value(task.secondary) / 2.0
        score += task.sp_remaining / rate
    return score


def _dedupe_maps(candidates: list[AttributeSet]) -> tuple[AttributeSet, ...]:
    out: list[AttributeSet] = []
    seen: set[tuple[int, int, int, int, int]] = set()
    for attrs in candidates:
        key = _attrs_key(attrs)
        if key not in seen:
            seen.add(key)
            out.append(attrs)
    return tuple(out)


def _dominance_prune(states: list[_State], *, keep_per_key: int) -> list[_State]:
    buckets: dict[tuple, list[_State]] = {}
    for state in states:
        key = (
            state.done,
            _attrs_key(state.base),
            state.timed_available_at,
            state.bonus_remaps,
            tuple(action.kind for action in state.remaps),
        )
        buckets.setdefault(key, []).append(state)
    kept: list[_State] = []
    for bucket in buckets.values():
        bucket.sort(key=lambda state: (state.elapsed, state.order, len(state.remaps)))
        kept.extend(bucket[:keep_per_key])
    kept.sort(key=lambda state: (state.elapsed, state.order, len(state.remaps)))
    return kept


def _objective_value(graph: TaskGraph, completion: dict[str, float], makespan: float, objective: str) -> float:
    if objective == "makespan":
        return makespan
    if objective == "priority_lexicographic":
        total = 0.0
        scale = max(makespan, 1.0) + 1.0
        for idx, name in enumerate(sorted(graph.plan_priority, key=lambda n: (-graph.plan_priority[n], n))):
            total += completion[name] / (scale ** idx)
        return total
    return sum(graph.plan_priority[name] * completion[name] for name in graph.plan_priority)


def _partial_score(graph: TaskGraph, state: _State, objective: str) -> tuple:
    completion = dict(state.plan_completion)
    completed_score = sum(graph.plan_priority[name] * seconds for name, seconds in completion.items())
    remaining_sp = sum(task.sp_remaining for key, task in graph.tasks.items() if key not in state.done)
    remap_penalty = len(state.remaps) * 0.0001
    return (completed_score, remaining_sp + state.elapsed, remap_penalty, state.order, tuple(_attrs_key(action.base_attributes) for action in state.remaps))


def _score_tuple(graph: TaskGraph, completion: dict[str, float], makespan: float, objective: str, order: tuple[TaskKey, ...], remaps: tuple[RemapAction, ...]) -> tuple:
    if objective == "priority_lexicographic":
        primary = tuple(completion[name] for name in sorted(graph.plan_priority, key=lambda n: (-graph.plan_priority[n], n)))
    else:
        primary = (_objective_value(graph, completion, makespan, objective),)
    return (*primary, makespan, len(remaps), order, tuple(_attrs_key(action.base_attributes) for action in remaps))


def _attrs_key(attrs: AttributeSet) -> tuple[int, int, int, int, int]:
    return tuple(attrs.value(name) for name in AttributeName)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
