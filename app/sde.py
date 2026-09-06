from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path
from typing import Iterator

import httpx

from .config import settings
from .models import AttributeName, SkillDefinition

# Dogma IDs used by CCP's SDE for skill training metadata.
ATTR_PRIMARY = 180
ATTR_SECONDARY = 181
ATTR_SKILL_TIME_CONSTANT = 275
REQUIRED_SKILL_IDS = (182, 183, 184, 1285, 1289, 1290)
REQUIRED_LEVEL_IDS = (277, 278, 279, 1286, 1287, 1288)

# Attribute dogma IDs. We also validate/fill these from dogmaAttributes.jsonl when present.
ATTRIBUTE_DOGMA_ID_TO_NAME = {
    164: AttributeName.charisma,
    165: AttributeName.intelligence,
    166: AttributeName.memory,
    167: AttributeName.perception,
    168: AttributeName.willpower,
}

CYBERNETICS_SKILL_ID = 3411
IMPLANT_BONUS_ATTRS = (175, 176, 177, 178, 179)


def iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _find_extracted_file(root: Path, filename: str) -> Path:
    candidates = list(root.rglob(filename))
    if not candidates:
        raise FileNotFoundError(f"{filename} not found under {root}")
    return candidates[0]


class SdeRepository:
    def __init__(self, root: Path | None = None):
        self.root = root or settings.sde_dir
        self.skills_by_id: dict[int, SkillDefinition] = {}
        self.skills_by_name: dict[str, SkillDefinition] = {}
        self.skill_names_by_id: dict[int, dict[str, str]] = {}
        self.build: str | None = None

    async def update(self) -> str:
        self.root.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": settings.user_agent}
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0), headers=headers, follow_redirects=True) as client:
            r = await client.get(settings.sde_manifest_url)
            r.raise_for_status()
            build = None
            for line in r.text.splitlines():
                if not line.strip():
                    continue
                obj = json.loads(line)
                key = obj.get("_key") or obj.get("key")
                if key == "sde":
                    value = obj.get("_value", obj.get("value", obj))
                    if isinstance(value, dict):
                        build = value.get("buildNumber") or value.get("build") or value.get("version")
                    else:
                        build = value
                    break
            if not build:
                # The manifest schema can evolve; preserve the raw file to make diagnosis easy.
                (self.root / "latest.jsonl").write_text(r.text, encoding="utf-8")
                raise RuntimeError("Could not determine SDE build from latest.jsonl")

            build = str(build)
            target = self.root / build
            if target.exists() and (target / ".ready").exists():
                self.build = build
                return build

            zip_url = f"{settings.sde_base_url}/eve-online-static-data-{build}-jsonl.zip"
            zip_path = self.root / f"sde-{build}.zip"
            tmp = self.root / f".{build}.tmp"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir(parents=True)

            async with client.stream("GET", zip_url) as resp:
                resp.raise_for_status()
                with zip_path.open("wb") as out:
                    async for chunk in resp.aiter_bytes():
                        out.write(chunk)

            with zipfile.ZipFile(zip_path) as zf:
                wanted = {"types.jsonl", "groups.jsonl", "typeDogma.jsonl", "dogmaAttributes.jsonl"}
                for member in zf.infolist():
                    if Path(member.filename).name in wanted:
                        zf.extract(member, tmp)

            if target.exists():
                shutil.rmtree(target)
            tmp.rename(target)
            (target / ".ready").write_text(build, encoding="utf-8")
            zip_path.unlink(missing_ok=True)
            self.build = build
            return build

    def _current_root(self) -> Path:
        if self.build:
            root = self.root / self.build
            if root.exists():
                return root
        ready = sorted((p for p in self.root.iterdir() if p.is_dir() and (p / ".ready").exists()), reverse=True) if self.root.exists() else []
        if not ready:
            raise FileNotFoundError("No SDE installed. Run update_sde first.")
        self.build = ready[0].name
        return ready[0]

    def load(self) -> None:
        root = self._current_root()
        groups_path = _find_extracted_file(root, "groups.jsonl")
        types_path = _find_extracted_file(root, "types.jsonl")
        dogma_path = _find_extracted_file(root, "typeDogma.jsonl")

        skill_group_ids: set[int] = set()
        for row in iter_jsonl(groups_path):
            if int(row.get("categoryID", -1)) == 16:
                skill_group_ids.add(int(row["_key"]))

        type_rows: dict[int, dict] = {}
        for row in iter_jsonl(types_path):
            if int(row.get("groupID", -1)) in skill_group_ids and row.get("published", True):
                type_rows[int(row["_key"])] = row

        dogma_rows: dict[int, dict[int, float]] = {}
        wanted_type_ids = set(type_rows)
        for row in iter_jsonl(dogma_path):
            type_id = int(row["_key"])
            if type_id not in wanted_type_ids:
                continue
            dogma_rows[type_id] = {
                int(a["attributeID"]): float(a["value"])
                for a in row.get("dogmaAttributes", [])
            }

        skills_by_id: dict[int, SkillDefinition] = {}
        skill_names_by_id: dict[int, dict[str, str]] = {}
        for type_id, row in type_rows.items():
            attrs = dogma_rows.get(type_id, {})
            if ATTR_PRIMARY not in attrs or ATTR_SECONDARY not in attrs or ATTR_SKILL_TIME_CONSTANT not in attrs:
                continue
            primary = ATTRIBUTE_DOGMA_ID_TO_NAME.get(int(attrs[ATTR_PRIMARY]))
            secondary = ATTRIBUTE_DOGMA_ID_TO_NAME.get(int(attrs[ATTR_SECONDARY]))
            if not primary or not secondary:
                continue
            prereqs: list[tuple[int, int]] = []
            for skill_attr, level_attr in zip(REQUIRED_SKILL_IDS, REQUIRED_LEVEL_IDS):
                if skill_attr in attrs:
                    req_skill = int(attrs[skill_attr])
                    req_level = int(attrs.get(level_attr, 1))
                    prereqs.append((req_skill, req_level))
            localized_name = row.get("name") or {}
            skill_names_by_id[type_id] = {
                str(lang): str(value)
                for lang, value in localized_name.items()
                if value is not None and str(value).strip()
            }
            name = localized_name.get("en") or localized_name.get("ru") or str(type_id)
            skill = SkillDefinition(
                type_id=type_id,
                name=name,
                rank=float(attrs[ATTR_SKILL_TIME_CONSTANT]),
                primary=primary,
                secondary=secondary,
                prerequisites=tuple(prereqs),
            )
            skills_by_id[type_id] = skill

        self.skills_by_id = skills_by_id
        self.skills_by_name = {s.name.casefold(): s for s in skills_by_id.values()}
        self.skill_names_by_id = skill_names_by_id

    def skill_localized_name(self, type_id: int, language: str = "ru") -> str:
        names = self.skill_names_by_id.get(int(type_id), {})
        if names.get(language):
            return names[language]
        if names.get("en"):
            return names["en"]
        skill = self.skills_by_id.get(int(type_id))
        if skill is not None:
            return skill.name
        return str(type_id)

    def resolve_skill(self, name: str) -> SkillDefinition:
        try:
            return self.skills_by_name[name.casefold()]
        except KeyError:
            raise KeyError(f"Unknown EVE skill: {name}") from None

    def type_id_by_name(self, name: str) -> int:
        root = self._current_root()
        types_path = _find_extracted_file(root, "types.jsonl")
        wanted = name.casefold()
        for row in iter_jsonl(types_path):
            localized_name = row.get("name") or {}
            type_name = localized_name.get("en") or localized_name.get("ru") or ""
            if type_name.casefold() == wanted:
                return int(row["_key"])
        raise KeyError(f"Unknown EVE type: {name}")

    def type_name(self, type_id: int) -> str:
        root = self._current_root()
        types_path = _find_extracted_file(root, "types.jsonl")
        for row in iter_jsonl(types_path):
            if int(row["_key"]) == int(type_id):
                localized_name = row.get("name") or {}
                return localized_name.get("en") or localized_name.get("ru") or str(type_id)
        return str(type_id)

    def cybernetics_level_for_attribute_implants(self, bonus: int) -> int:
        """Derive the Cybernetics prerequisite level for +N attribute implants from SDE dogma."""
        if bonus <= 0:
            return 0
        root = self._current_root()
        types_path = _find_extracted_file(root, "types.jsonl")
        dogma_path = _find_extracted_file(root, "typeDogma.jsonl")

        candidate_ids: set[int] = set()
        bonus_pattern = re.compile(rf"\+{bonus}\s+Bonus to ", re.IGNORECASE)
        for row in iter_jsonl(types_path):
            if not row.get("published", True):
                continue
            name = (row.get("name") or {}).get("en", "")
            description = (row.get("description") or {}).get("en", "")
            if (
                row.get("groupID") == 745
                and any(token in name for token in ("Ocular Filter", "Memory Augmentation", "Neural Boost", "Cybernetic Subprocessor", "Social Adaptation Chip"))
                and bonus_pattern.search(description)
            ):
                candidate_ids.add(int(row["_key"]))

        levels: set[int] = set()
        for row in iter_jsonl(dogma_path):
            type_id = int(row["_key"])
            if type_id not in candidate_ids:
                continue
            attrs = {int(a["attributeID"]): float(a["value"]) for a in row.get("dogmaAttributes", [])}
            if int(attrs.get(182, 0)) != CYBERNETICS_SKILL_ID:
                continue
            if not any(int(attrs.get(attr, 0)) == bonus for attr in IMPLANT_BONUS_ATTRS):
                continue
            levels.add(int(attrs.get(277, 0)))

        if not levels:
            raise RuntimeError(f"Could not derive Cybernetics requirement for +{bonus} attribute implants from SDE")
        if len(levels) != 1:
            raise RuntimeError(f"Ambiguous Cybernetics requirements for +{bonus} attribute implants: {sorted(levels)}")
        return levels.pop()
