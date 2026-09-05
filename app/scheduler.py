from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import count

from .models import AttributeSet, CharacterSkill, SkillDefinition, SkillPlan, TrainingTask
from .training_math import TimedAttributeModifier, timeline_task_seconds, total_sp_for_level

TaskKey = tuple[int, int]

EXACT_TASK_LIMIT = 9
DEFAULT_BEAM_WIDTH = 256
MAX_UNIQUE_TASKS = 120


@dataclass(frozen=True)
class SchedulePlan:
    name: str
    priority: float
    targets: dict[int, int]


@dataclass(frozen=True)
class TaskGraph:
    tasks: dict[TaskKey, TrainingTask]
    deps: dict[TaskKey, set[TaskKey]]
    plan_required: dict[str, set[TaskKey]]
    plan_priority: dict[str, float]

    @property
    def shared_tasks(self) -> set[TaskKey]:
        return {key for key, task in self.tasks.items() if len(task.plan_names) > 1}


@dataclass(frozen=True)
class OptimizedSchedule:
    order: tuple[TaskKey, ...]
    algorithm: str
    optimality: str
    states_evaluated: int
    beam_width: int | None
    objective_value: float
    makespan_seconds: float
    plan_completion_seconds: dict[str, float]


def build_task_graph(
    plans: list[SkillPlan],
    current_skills: dict[int, CharacterSkill],
    skill_catalog: dict[int, SkillDefinition],
    skills_by_name: dict[str, SkillDefinition],
) -> TaskGraph:
    schedule_plans = [_normalize_plan(plan, skills_by_name) for plan in plans]
    required: dict[TaskKey, set[str]] = {}
    deps: dict[TaskKey, set[TaskKey]] = {}
    plan_required: dict[str, set[TaskKey]] = {plan.name: set() for plan in schedule_plans}
    visiting: set[tuple[int, int, str]] = set()

    def require(skill_id: int, level: int, plan_name: str) -> None:
        if level < 1 or level > 5:
            raise ValueError(f"Invalid level {level} for skill {skill_id}")
        if skill_id not in skill_catalog:
            raise KeyError(f"Missing SDE skill definition for prerequisite {skill_id}")
        marker = (skill_id, level, plan_name)
        if marker in visiting:
            raise RuntimeError(f"Cycle in skill prerequisites at {skill_id} level {level}")
        visiting.add(marker)
        skill = skill_catalog[skill_id]

        for req_id, req_level in skill.prerequisites:
            if req_id not in skill_catalog:
                raise KeyError(f"Missing SDE skill definition for prerequisite {req_id}")
            require(req_id, req_level, plan_name)

        current = current_skills.get(skill_id)
        trained_level = current.trained_level if current else 0
        for needed_level in range(max(1, trained_level + 1), level + 1):
            key = (skill_id, needed_level)
            required.setdefault(key, set()).add(plan_name)
            plan_required[plan_name].add(key)
            deps.setdefault(key, set())
            if needed_level > 1 and (skill_id, needed_level - 1) in required:
                deps[key].add((skill_id, needed_level - 1))
            if needed_level == max(1, trained_level + 1):
                for req_id, req_level in skill.prerequisites:
                    req_current = current_skills.get(req_id)
                    if req_current is None or req_current.trained_level < req_level:
                        deps[key].add((req_id, req_level))
        visiting.remove(marker)

    for plan in schedule_plans:
        for skill_id, level in plan.targets.items():
            require(skill_id, level, plan.name)

    tasks: dict[TaskKey, TrainingTask] = {}
    for key, plan_names in required.items():
        skill_id, level = key
        skill = skill_catalog[skill_id]
        current = current_skills.get(skill_id)
        trained_level = current.trained_level if current else 0
        current_sp = current.skillpoints if current else 0
        start_total = total_sp_for_level(skill.rank, level - 1)
        end_total = total_sp_for_level(skill.rank, level)
        effective_start = max(start_total, current_sp if level == trained_level + 1 else start_total)
        remaining = max(0, end_total - effective_start)
        if remaining == 0:
            continue
        tasks[key] = TrainingTask(
            skill_id=skill_id,
            skill_name=skill.name,
            level=level,
            sp_start=effective_start,
            sp_end=end_total,
            sp_remaining=remaining,
            primary=skill.primary,
            secondary=skill.secondary,
            rank=skill.rank,
            plan_names=tuple(sorted(plan_names)),
        )

    task_keys = set(tasks)
    deps = {key: {dep for dep in deps.get(key, set()) if dep in task_keys} for key in task_keys}
    plan_required = {name: {key for key in keys if key in task_keys} for name, keys in plan_required.items()}
    if len(tasks) > MAX_UNIQUE_TASKS:
        raise ValueError(f"Too many unique tasks: {len(tasks)} > {MAX_UNIQUE_TASKS}")
    _validate_graph(deps)
    return TaskGraph(
        tasks=tasks,
        deps=deps,
        plan_required=plan_required,
        plan_priority={plan.name: plan.priority for plan in schedule_plans},
    )


def optimize_schedule(
    graph: TaskGraph,
    *,
    objective: str,
    base_attributes: AttributeSet,
    modifiers: tuple[TimedAttributeModifier, ...],
    start_time: datetime,
    omega: bool,
    beam_width: int = DEFAULT_BEAM_WIDTH,
    pinned_prefix: tuple[TaskKey, ...] = (),
) -> OptimizedSchedule:
    if objective not in {"weighted_completion_time", "makespan", "priority_lexicographic"}:
        raise ValueError(f"Unknown schedule objective: {objective}")
    if len(graph.tasks) <= EXACT_TASK_LIMIT:
        return _exact_search(graph, objective, base_attributes, modifiers, start_time, omega, pinned_prefix)
    return _beam_search(graph, objective, base_attributes, modifiers, start_time, omega, min(beam_width, DEFAULT_BEAM_WIDTH), pinned_prefix)


def baseline_input_plan_order(graph: TaskGraph) -> tuple[TaskKey, ...]:
    order: list[TaskKey] = []
    done: set[TaskKey] = set()
    for plan_name in graph.plan_required:
        pending = set(graph.plan_required[plan_name]) - done
        while pending:
            ready = [key for key in pending if graph.deps[key] <= done]
            if not ready:
                ready = [key for key in graph.tasks if key not in done and graph.deps[key] <= done]
            ready.sort(key=_stable_key)
            chosen = ready[0]
            order.append(chosen)
            done.add(chosen)
            pending.discard(chosen)
    for key in _topological_order(graph, done):
        order.append(key)
    return tuple(order)


def baseline_shortest_available_task(graph: TaskGraph) -> tuple[TaskKey, ...]:
    order: list[TaskKey] = []
    done: set[TaskKey] = set()
    while len(done) < len(graph.tasks):
        ready = [key for key in graph.tasks if key not in done and graph.deps[key] <= done]
        if not ready:
            raise RuntimeError("No legal next task")
        ready.sort(key=lambda key: (graph.tasks[key].sp_remaining, *_stable_key(key)))
        chosen = ready[0]
        order.append(chosen)
        done.add(chosen)
    return tuple(order)


def evaluate_order(
    graph: TaskGraph,
    order: tuple[TaskKey, ...],
    *,
    objective: str,
    base_attributes: AttributeSet,
    modifiers: tuple[TimedAttributeModifier, ...],
    start_time: datetime,
    omega: bool,
) -> OptimizedSchedule:
    completion, makespan, _ = simulate_order(graph, order, base_attributes=base_attributes, modifiers=modifiers, start_time=start_time, omega=omega)
    return OptimizedSchedule(
        order=order,
        algorithm="baseline",
        optimality="baseline",
        states_evaluated=0,
        beam_width=None,
        objective_value=_objective_value(graph, completion, makespan, objective),
        makespan_seconds=makespan,
        plan_completion_seconds=completion,
    )


def simulate_order(
    graph: TaskGraph,
    order: tuple[TaskKey, ...],
    *,
    base_attributes: AttributeSet,
    modifiers: tuple[TimedAttributeModifier, ...],
    start_time: datetime,
    omega: bool,
    finalize_uncompleted: bool = True,
) -> tuple[dict[str, float], float, list[dict]]:
    done: set[TaskKey] = set()
    current = start_time
    elapsed = 0.0
    plan_completion: dict[str, float] = {}
    rows: list[dict] = []
    for idx, key in enumerate(order, 1):
        if not graph.deps[key] <= done:
            raise RuntimeError(f"Illegal schedule order at {key}")
        task = graph.tasks[key]
        seconds = timeline_task_seconds([task], base_attributes, omega=omega, modifiers=modifiers, start_time=current)
        starts_at = current
        current = current + timedelta(seconds=seconds)
        elapsed += seconds
        done.add(key)
        completes = sorted(name for name, keys in graph.plan_required.items() if name not in plan_completion and keys <= done)
        for name in completes:
            plan_completion[name] = elapsed
        rows.append(
            {
                "index": idx,
                "key": key,
                "task": task,
                "starts_at": starts_at,
                "finishes_at": current,
                "duration_seconds": seconds,
                "completes_plans": completes,
            }
        )
    if finalize_uncompleted:
        for name in graph.plan_required:
            plan_completion.setdefault(name, elapsed)
    return plan_completion, elapsed, rows


def _exact_search(graph, objective, base_attributes, modifiers, start_time, omega, pinned_prefix):
    best: tuple[tuple, tuple[TaskKey, ...], dict[str, float], float] | None = None
    states = 0

    def dfs(done: set[TaskKey], order: list[TaskKey]) -> None:
        nonlocal best, states
        states += 1
        if len(done) == len(graph.tasks):
            completion, makespan, _ = simulate_order(graph, tuple(order), base_attributes=base_attributes, modifiers=modifiers, start_time=start_time, omega=omega)
            score = _score_tuple(graph, completion, makespan, objective, tuple(order))
            if best is None or score < best[0]:
                best = (score, tuple(order), completion, makespan)
            return
        ready = [key for key in graph.tasks if key not in done and graph.deps[key] <= done]
        ready.sort(key=_stable_key)
        for key in ready:
            done.add(key)
            order.append(key)
            dfs(done, order)
            order.pop()
            done.remove(key)

    pinned_done: set[TaskKey] = set()
    for key in pinned_prefix:
        if key not in graph.tasks or not graph.deps[key] <= pinned_done:
            continue
        pinned_done.add(key)
    dfs(set(pinned_done), list(pinned_done))
    assert best is not None
    _, order, completion, makespan = best
    return OptimizedSchedule(order, "exact", "proven", states, None, _objective_value(graph, completion, makespan, objective), makespan, completion)


def _beam_search(graph, objective, base_attributes, modifiers, start_time, omega, beam_width, pinned_prefix):
    serial = count()
    states = [(set(), [], 0)]
    states_evaluated = 0
    for key in pinned_prefix:
        if key in graph.tasks and graph.deps[key] <= states[0][0]:
            states[0][0].add(key)
            states[0][1].append(key)
    while states and len(states[0][0]) < len(graph.tasks):
        next_states = []
        for done, order, _ in states:
            ready = [key for key in graph.tasks if key not in done and graph.deps[key] <= done]
            ready.sort(key=lambda key: _heuristic_key(graph, key, done))
            for key in ready:
                new_done = set(done)
                new_done.add(key)
                new_order = order + [key]
                completion, makespan, _ = simulate_order(
                    graph,
                    tuple(new_order),
                    base_attributes=base_attributes,
                    modifiers=modifiers,
                    start_time=start_time,
                    omega=omega,
                    finalize_uncompleted=False,
                )
                h = _partial_score(graph, new_done, completion, makespan, objective, tuple(new_order))
                next_states.append((new_done, new_order, h, next(serial)))
                states_evaluated += 1
        next_states.sort(key=lambda item: (item[2], item[3]))
        states = [(done, order, marker) for done, order, _, marker in next_states[:beam_width]]
    if not states:
        raise RuntimeError("Beam search failed")
    candidates = []
    for done, order, _ in states:
        completion, makespan, _ = simulate_order(graph, tuple(order), base_attributes=base_attributes, modifiers=modifiers, start_time=start_time, omega=omega)
        candidates.append((_score_tuple(graph, completion, makespan, objective, tuple(order)), tuple(order), completion, makespan))
    candidates.sort(key=lambda item: item[0])
    _, order, completion, makespan = candidates[0]
    return OptimizedSchedule(order, "beam_search", "heuristic", states_evaluated, beam_width, _objective_value(graph, completion, makespan, objective), makespan, completion)


def _normalize_plan(plan: SkillPlan, skills_by_name: dict[str, SkillDefinition]) -> SchedulePlan:
    if plan.weight <= 0:
        raise ValueError("plan priority must be positive")
    targets: dict[int, int] = {}
    for target in plan.targets:
        if target.level < 1 or target.level > 5:
            raise ValueError(f"Invalid target level {target.level}")
        skill = skills_by_name.get(target.skill_name.casefold())
        if skill is None:
            raise KeyError(f"Unknown EVE skill: {target.skill_name}")
        targets[skill.type_id] = max(targets.get(skill.type_id, 0), target.level)
    return SchedulePlan(plan.name, float(plan.weight), targets)


def _validate_graph(deps: dict[TaskKey, set[TaskKey]]) -> None:
    pending = {key: set(val) for key, val in deps.items()}
    done: set[TaskKey] = set()
    while pending:
        ready = [key for key, val in pending.items() if val <= done]
        if not ready:
            raise RuntimeError("Cycle in scheduler task graph")
        for key in ready:
            done.add(key)
            del pending[key]


def _topological_order(graph: TaskGraph, already_done: set[TaskKey] | None = None) -> tuple[TaskKey, ...]:
    done = set(already_done or set())
    order = []
    while len(done) < len(graph.tasks):
        ready = [key for key in graph.tasks if key not in done and graph.deps[key] <= done]
        ready.sort(key=_stable_key)
        chosen = ready[0]
        order.append(chosen)
        done.add(chosen)
    return tuple(order)


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


def _score_tuple(graph: TaskGraph, completion: dict[str, float], makespan: float, objective: str, order: tuple[TaskKey, ...]) -> tuple:
    if objective == "priority_lexicographic":
        primary = tuple(completion[name] for name in sorted(graph.plan_priority, key=lambda n: (-graph.plan_priority[n], n)))
    else:
        primary = (_objective_value(graph, completion, makespan, objective),)
    return (*primary, makespan, _switch_count(graph, order), tuple(_stable_key(key) for key in order))


def _partial_score(graph: TaskGraph, done: set[TaskKey], completion: dict[str, float], makespan: float, objective: str, order: tuple[TaskKey, ...]) -> tuple:
    remaining_sp = sum(task.sp_remaining for key, task in graph.tasks.items() if key not in done)
    completed_score = sum(graph.plan_priority[name] * seconds for name, seconds in completion.items())
    plan_bonus = -sum(graph.plan_priority[name] for name in completion)
    return (completed_score, remaining_sp + makespan, plan_bonus, _switch_count(graph, order), tuple(_stable_key(key) for key in order))


def _heuristic_key(graph: TaskGraph, key: TaskKey, done: set[TaskKey]) -> tuple:
    task = graph.tasks[key]
    after = done | {key}
    completes_weight = sum(graph.plan_priority[name] for name, keys in graph.plan_required.items() if key in keys and keys <= after)
    unlock_count = sum(1 for other, deps in graph.deps.items() if other not in after and key in deps and deps <= after)
    plan_weight = sum(graph.plan_priority[name] for name in task.plan_names)
    return (-completes_weight, -unlock_count, -len(task.plan_names), -plan_weight, task.sp_remaining, *_stable_key(key))


def _switch_count(graph: TaskGraph, order: tuple[TaskKey, ...]) -> int:
    switches = 0
    prev: int | None = None
    for key in order:
        if prev is not None and key[0] != prev:
            switches += 1
        prev = key[0]
    return switches


def _stable_key(key: TaskKey) -> tuple:
    return (key[0], key[1])
