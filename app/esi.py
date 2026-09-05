from __future__ import annotations

from datetime import datetime

import httpx

from .config import settings
from .models import CharacterSkill, CharacterSnapshot, attribute_set_from_dict


class EsiClient:
    def __init__(self, access_token: str):
        self.access_token = access_token

    async def _get(self, path: str, **params):
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": settings.user_agent,
        }
        async with httpx.AsyncClient(base_url=settings.esi_base_url, headers=headers, timeout=30.0) as client:
            r = await client.get(path, params=params or None)
            r.raise_for_status()
            return r.json()

    async def character_snapshot(self, character_id: int) -> CharacterSnapshot:
        skills_raw = await self._get(f"/characters/{character_id}/skills")
        attrs_raw = await self._get(f"/characters/{character_id}/attributes")
        implants_raw = await self._get(f"/characters/{character_id}/implants")

        skills = {
            int(s["skill_id"]): CharacterSkill(
                skill_id=int(s["skill_id"]),
                trained_level=int(s["trained_skill_level"]),
                active_level=int(s["active_skill_level"]),
                skillpoints=int(s["skillpoints_in_skill"]),
            )
            for s in skills_raw.get("skills", [])
        }
        def dt(value):
            return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None

        return CharacterSnapshot(
            character_id=character_id,
            total_sp=int(skills_raw.get("total_sp", 0)),
            unallocated_sp=int(skills_raw.get("unallocated_sp", 0) or 0),
            attributes=attribute_set_from_dict(attrs_raw),
            skills=skills,
            implants=tuple(int(x) for x in implants_raw),
            bonus_remaps=attrs_raw.get("bonus_remaps"),
            last_remap_date=dt(attrs_raw.get("last_remap_date")),
            accrued_remap_cooldown_date=dt(attrs_raw.get("accrued_remap_cooldown_date")),
        )

    async def skill_queue(self, character_id: int):
        return await self._get(f"/characters/{character_id}/skillqueue")
