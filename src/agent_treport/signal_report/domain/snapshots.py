from __future__ import annotations

from agent_pack.models import RuntimeModel


class SecurityHolding(RuntimeModel):
    security_id: str
    analytical_identity_key: str | None = None
    security_group_id: str | None = None
    listing_key: str | None = None
    security_group_name: str | None = None
    security_group_ticker: str | None = None
    ticker: str | None
    name: str
    market: str | None = None
    sector: str | None = None
    theme: str | None = None
    country: str | None = None
    weight_percent: float
    shares: float | None = None
    market_value_krw: float | None = None
    price_krw: float | None = None
    is_cash: bool = False


class ETFHoldingsSnapshots(RuntimeModel):
    etf_id: str
    etf_name: str
    brand_id: str
    source_provider_id: str
    previous: tuple[SecurityHolding, ...]
    current: tuple[SecurityHolding, ...]


class MultiETFHoldingsSnapshots(RuntimeModel):
    as_of_date: str
    previous_date: str
    current_date: str
    lookback_days: int
    universe: str
    etfs: tuple[ETFHoldingsSnapshots, ...]
