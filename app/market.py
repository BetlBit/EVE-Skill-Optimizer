from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import logging
from typing import Awaitable, Callable

import httpx

from .config import settings

THE_FORGE_REGION_ID = 10000002
JITA_4_4_LOCATION_ID = 60003760
MARKET_SOURCE = "esi_jita_4_4"
logger = logging.getLogger(__name__)

OrderFetcher = Callable[[int, str, int], Awaitable[tuple[list[dict], dict[str, str]]]]


@dataclass(frozen=True)
class MarketOrder:
    unit_price: float
    volume_remain: int
    order_id: int
    fetched_at: datetime

    def to_dict(self) -> dict:
        return {
            "unit_price": self.unit_price,
            "volume_remain": self.volume_remain,
            "order_id": self.order_id,
            "fetched_at": self.fetched_at.isoformat(),
        }


@dataclass(frozen=True)
class MarketSnapshot:
    type_id: int
    type_name: str
    sell: MarketOrder | None
    buy: MarketOrder | None
    source: str
    warnings: tuple[str, ...]
    fetched_at: datetime
    cache_age_seconds: float

    @property
    def available_to_buy(self) -> bool:
        return self.sell is not None

    def to_dict(self) -> dict:
        return {
            "type_id": self.type_id,
            "type_name": self.type_name,
            "sell": self.sell.to_dict() if self.sell else None,
            "buy": self.buy.to_dict() if self.buy else None,
            "source": self.source,
            "warnings": list(self.warnings),
            "fetched_at": self.fetched_at.isoformat(),
            "cache_age_seconds": self.cache_age_seconds,
        }


class MarketClient:
    def __init__(self, *, order_fetcher: OrderFetcher | None = None):
        self._order_fetcher = order_fetcher
        self._cache: dict[tuple[int, str], tuple[datetime, MarketSnapshot]] = {}

    async def get_jita_sell_price(self, type_id: int, *, type_name: str | None = None) -> float | None:
        snapshot = await self.get_jita_market_snapshot([type_id], type_names={type_id: type_name or str(type_id)})
        order = snapshot[int(type_id)].sell
        return order.unit_price if order else None

    async def get_jita_buy_price(self, type_id: int, *, type_name: str | None = None) -> float | None:
        snapshot = await self.get_jita_market_snapshot([type_id], type_names={type_id: type_name or str(type_id)})
        order = snapshot[int(type_id)].buy
        return order.unit_price if order else None

    async def get_jita_market_snapshot(
        self,
        type_ids: list[int] | tuple[int, ...],
        *,
        type_names: dict[int, str] | None = None,
    ) -> dict[int, MarketSnapshot]:
        type_names = type_names or {}
        out: dict[int, MarketSnapshot] = {}
        for type_id in dict.fromkeys(int(t) for t in type_ids):
            out[type_id] = await self._snapshot_one(type_id, type_names.get(type_id, str(type_id)))
        return out

    async def _snapshot_one(self, type_id: int, type_name: str) -> MarketSnapshot:
        now = _utc_now()
        cached = self._cache.get((type_id, type_name))
        if cached:
            cached_at, snapshot = cached
            return _with_cache_age(snapshot, now, (now - cached_at).total_seconds())

        warnings: list[str] = []
        fetched_at = now
        try:
            sell_orders, sell_headers = await self._fetch_all_pages(type_id, "sell")
            buy_orders, buy_headers = await self._fetch_all_pages(type_id, "buy")
            fetched_at = _header_date(sell_headers) or _header_date(buy_headers) or now
        except Exception as exc:
            logger.warning("ESI market request failed for type_id=%s order_type=sell/buy: %s", type_id, exc)
            warnings.append(f"ESI market request failed: {exc}")
            snapshot = MarketSnapshot(type_id, type_name, None, None, MARKET_SOURCE, tuple(warnings), fetched_at, 0.0)
            self._cache[(type_id, type_name)] = (now, snapshot)
            return snapshot

        jita_sells = [
            order for order in sell_orders
            if not order.get("is_buy_order", False) and int(order.get("location_id", 0)) == JITA_4_4_LOCATION_ID
        ]
        jita_buys = [
            order for order in buy_orders
            if order.get("is_buy_order", True) and int(order.get("location_id", 0)) == JITA_4_4_LOCATION_ID
        ]
        sell = min(jita_sells, key=lambda order: (float(order["price"]), int(order.get("order_id", 0))), default=None)
        buy = max(jita_buys, key=lambda order: (float(order["price"]), -int(order.get("order_id", 0))), default=None)
        if sell is None:
            warnings.append("no active Jita 4-4 sell order")
        snapshot = MarketSnapshot(
            type_id=type_id,
            type_name=type_name,
            sell=_order_to_snapshot(sell, fetched_at) if sell else None,
            buy=_order_to_snapshot(buy, fetched_at) if buy else None,
            source=MARKET_SOURCE,
            warnings=tuple(warnings),
            fetched_at=fetched_at,
            cache_age_seconds=0.0,
        )
        self._cache[(type_id, type_name)] = (now, snapshot)
        return snapshot

    async def _fetch_all_pages(self, type_id: int, order_type: str) -> tuple[list[dict], dict[str, str]]:
        if self._order_fetcher is not None:
            orders, headers = await self._order_fetcher(type_id, order_type, 1)
            return orders, headers

        headers = {"User-Agent": settings.user_agent}
        params = {"order_type": order_type, "type_id": int(type_id), "page": 1}
        all_orders: list[dict] = []
        last_headers: dict[str, str] = {}
        async with httpx.AsyncClient(base_url=settings.esi_base_url, headers=headers, timeout=30.0) as client:
            while True:
                response = await client.get(f"/latest/markets/{THE_FORGE_REGION_ID}/orders/", params=params)
                response.raise_for_status()
                last_headers = dict(response.headers)
                all_orders.extend(response.json())
                pages = int(response.headers.get("X-Pages", "1"))
                if params["page"] >= pages:
                    break
                params["page"] += 1
        return all_orders, last_headers


def market_metadata(snapshots: list[MarketSnapshot] | tuple[MarketSnapshot, ...]) -> dict:
    fetched = max((snapshot.fetched_at for snapshot in snapshots), default=_utc_now())
    cache_age = max((snapshot.cache_age_seconds for snapshot in snapshots), default=0.0)
    warnings = sorted({warning for snapshot in snapshots for warning in snapshot.warnings})
    return {
        "source": "ESI Jita 4-4",
        "region_id": THE_FORGE_REGION_ID,
        "location_id": JITA_4_4_LOCATION_ID,
        "fetched_at": fetched.isoformat(),
        "cache_age_seconds": cache_age,
        "warnings": warnings,
    }


def _order_to_snapshot(order: dict, fetched_at: datetime) -> MarketOrder:
    return MarketOrder(
        unit_price=float(order["price"]),
        volume_remain=int(order.get("volume_remain", 0)),
        order_id=int(order.get("order_id", 0)),
        fetched_at=fetched_at,
    )


def _with_cache_age(snapshot: MarketSnapshot, now: datetime, cache_age: float) -> MarketSnapshot:
    return MarketSnapshot(
        snapshot.type_id,
        snapshot.type_name,
        snapshot.sell,
        snapshot.buy,
        snapshot.source,
        snapshot.warnings,
        snapshot.fetched_at,
        cache_age,
    )


def _header_date(headers: dict[str, str]) -> datetime | None:
    raw = headers.get("Date") or headers.get("date")
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
