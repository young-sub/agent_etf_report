from __future__ import annotations

from typing import Literal

from agent_pack.models import RuntimeModel

EvidenceEventType = Literal[
    "holding_change",
    "company_disclosure",
    "earnings",
    "analyst_report",
    "regulatory",
    "macro",
    "market_reaction",
    "news",
    "price_volume",
    "valuation",
    "sector_data",
    "other",
]
EvidenceRole = Literal[
    "primary_observation",
    "interpretation_support",
    "interpretation_challenge",
    "context",
]
EvidenceRelevance = Literal["high", "medium", "low"]
EvidenceNovelty = Literal["new", "repeat", "unknown"]
EvidenceStance = Literal["supporting", "counter", "neutral"]
EvidenceStrength = Literal["strong", "moderate", "weak"]
ObservedDirection = Literal["increase", "decrease", "uncertain"]


class EvidenceItemInput(RuntimeModel):
    evidence_id: str
    ticker: str | None = None
    scope: str | None = None
    type: EvidenceEventType
    source: str
    title: str
    published_at: str | None = None
    url: str | None = None
    stance: EvidenceStance
    strength: EvidenceStrength
    claim_scope: str | None = None
    evidence_role: EvidenceRole = "context"
    relevance: EvidenceRelevance | None = None
    novelty: EvidenceNovelty | None = None
    interpretation_basis: str | None = None
    observed_direction: ObservedDirection | None = None
