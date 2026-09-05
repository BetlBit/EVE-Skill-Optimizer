from __future__ import annotations

from collections import defaultdict

from .models import CharacterSkill, SkillPlan, SkillTarget, TrainingTask
from .sde import SdeRepository
from .training_math import total_sp_for_level


class PlanAnalyzer:
    def __init__(self, sde: SdeRepository):
        self.sde = sde

    def normalize_targets(self, plans: list[SkillPlan]) -> tuple[dict[int, int], dict[int, set[str]]]:
        targets: dict[int, int] = {}
        memberships: dict[int, set[str]] = defaultdict(set)
        for plan in plans:
            for target in plan.targets:
                skill = self.sde.resolve_skill(target.skill_name)
                targets[skill.type_id] = max(targets.get(skill.type_id, 0), target.level)
                memberships[skill.type_id].add(plan.name)
        return targets, memberships

    def expand_prerequisites(self, targets: dict[int, int], memberships: dict[int, set[str]]) -> tuple[dict[int, int], dict[int, set[str]]]:
        expanded = dict(targets)
        out_memberships = {k: set(v) for k, v in memberships.items()}
        visiting: set[int] = set()

        def visit(skill_id: int, inherited_plans: set[str]) -> None:
            if skill_id in visiting:
                raise RuntimeError(f"Cycle in skill prerequisites at {skill_id}")
            visiting.add(skill_id)
            skill = self.sde.skills_by_id[skill_id]
            for req_id, req_level in skill.prerequisites:
                if req_id not in self.sde.skills_by_id:
                    continue
                expanded[req_id] = max(expanded.get(req_id, 0), req_level)
                out_memberships.setdefault(req_id, set()).update(inherited_plans)
                visit(req_id, inherited_plans)
            visiting.remove(skill_id)

        for skill_id in list(targets):
            visit(skill_id, out_memberships.get(skill_id, set()))
        return expanded, out_memberships

    def build_tasks(self, plans: list[SkillPlan], current_skills: dict[int, CharacterSkill]) -> list[TrainingTask]:
        targets, memberships = self.normalize_targets(plans)
        targets, memberships = self.expand_prerequisites(targets, memberships)
        tasks: list[TrainingTask] = []
        for skill_id, target_level in targets.items():
            skill = self.sde.skills_by_id[skill_id]
            current = current_skills.get(skill_id)
            current_sp = current.skillpoints if current else 0
            trained_level = current.trained_level if current else 0
            for level in range(max(1, trained_level + 1), target_level + 1):
                start_total = total_sp_for_level(skill.rank, level - 1)
                end_total = total_sp_for_level(skill.rank, level)
                effective_start = max(start_total, current_sp if level == trained_level + 1 else start_total)
                remaining = max(0, end_total - effective_start)
                if remaining == 0:
                    continue
                tasks.append(
                    TrainingTask(
                        skill_id=skill_id,
                        skill_name=skill.name,
                        level=level,
                        sp_start=effective_start,
                        sp_end=end_total,
                        sp_remaining=remaining,
                        primary=skill.primary,
                        secondary=skill.secondary,
                        rank=skill.rank,
                        plan_names=tuple(sorted(memberships.get(skill_id, set()))),
                    )
                )
        return self.topological_order(tasks)

    def topological_order(self, tasks: list[TrainingTask]) -> list[TrainingTask]:
        """Stable prerequisite-first order at per-level granularity.

        This is the safe baseline scheduler. A later optimizer can reorder available
        nodes to minimize weighted plan completion time while preserving these edges.
        """
        task_by_key = {(t.skill_id, t.level): t for t in tasks}
        deps: dict[tuple[int, int], set[tuple[int, int]]] = {k: set() for k in task_by_key}

        for key, task in task_by_key.items():
            if task.level > 1 and (task.skill_id, task.level - 1) in task_by_key:
                deps[key].add((task.skill_id, task.level - 1))
            skill = self.sde.skills_by_id[task.skill_id]
            if task.level == 1:
                for req_id, req_level in skill.prerequisites:
                    req_key = (req_id, req_level)
                    if req_key in task_by_key:
                        deps[key].add(req_key)

        ordered: list[TrainingTask] = []
        pending = dict(deps)
        while pending:
            ready = [k for k, d in pending.items() if not d]
            if not ready:
                raise RuntimeError("Could not topologically order training tasks")
            # Heuristic: prerequisites shared by more plans first, then shorter level.
            ready.sort(key=lambda k: (-len(task_by_key[k].plan_names), task_by_key[k].sp_remaining, task_by_key[k].skill_name, k[1]))
            chosen = ready[0]
            ordered.append(task_by_key[chosen])
            del pending[chosen]
            for d in pending.values():
                d.discard(chosen)
        return ordered
