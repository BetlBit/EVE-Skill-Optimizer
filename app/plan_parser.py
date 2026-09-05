from __future__ import annotations

import html
import re

from .models import PlanTarget, SkillPlan

ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
LINE_RE = re.compile(r"^(?P<name>.+?)\s+(?P<level>I{1,3}|IV|V|[1-5])\s*$", re.IGNORECASE)
LOCALIZED_NAME_RE = re.compile(
    r"^<localized\b[^>]*\bhint\s*=\s*(?P<quote>[\"'])(?P<hint>.*?)(?P=quote)[^>]*>.*?</localized>$",
    re.IGNORECASE | re.DOTALL,
)


def _canonical_skill_name(raw_name: str) -> str:
    """Return the canonical EVE skill name from common localized exports.

    The Russian EVE client / EVEMon-style exports can contain lines like::

        <localized hint="Spaceship Command">Допуски к управлению кораблями*</localized> 1

    SDE lookup is English-first, so the value from ``hint`` is the correct
    canonical key. Ordinary ``Skill Name IV`` input remains unchanged.
    """
    name = raw_name.strip()
    localized = LOCALIZED_NAME_RE.match(name)
    if localized:
        return html.unescape(localized.group("hint")).strip()
    return html.unescape(name).strip()


def parse_plan(text: str, *, name: str = "Plan") -> SkillPlan:
    targets: list[PlanTarget] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        # Strip common EVE/EVEMon list prefixes.
        line = re.sub(r"^[\-•*]+\s*", "", line)
        match = LINE_RE.match(line)
        if not match:
            raise ValueError(f"Cannot parse line {line_no}: {raw!r}")
        skill_name = _canonical_skill_name(match.group("name"))
        raw_level = match.group("level").upper()
        level = int(raw_level) if raw_level.isdigit() else ROMAN[raw_level]
        targets.append(PlanTarget(skill_name=skill_name, level=level))
    return SkillPlan(name=name, targets=targets)
