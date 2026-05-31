from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from typing import Literal

from agent_pack.models import Message, ModelRequest, RuntimeModel, TextBlock
from agent_pack.models_client import ModelClient
from pydantic import field_validator

from agent_treport.signal_report.domain.evidence import EvidenceItemInput
from agent_treport.signal_report.domain.payload import SignalBoardRow
from agent_treport.signal_report.external_evidence.contracts import (
    AlignmentClassifier,
    ExternalEvidenceCandidate,
)

AlignmentStance = Literal["supporting", "counter", "neutral", "ambiguous"]
AlignmentEvidenceRole = Literal[
    "interpretation_support",
    "interpretation_challenge",
    "context",
]


class AlignmentDecision(RuntimeModel):
    candidate_id: str
    alignment_stance: AlignmentStance
    evidence_role: AlignmentEvidenceRole
    relevance: Literal["high", "medium", "low"]
    confidence: float
    interpretation_basis: str | None = None

    @field_validator("confidence")
    @classmethod
    def _validate_confidence(cls, value: float) -> float:
        if value < 0 or value > 1:
            raise ValueError("confidence must be between 0 and 1")
        return value


class FakeAlignmentClassifier:
    def __init__(self, decisions: Mapping[str, AlignmentDecision]) -> None:
        self._decisions = dict(decisions)

    def classify(
        self,
        *,
        candidates: Sequence[ExternalEvidenceCandidate],
        signal_rows: Sequence[object],
    ) -> Mapping[str, AlignmentDecision]:
        _ = signal_rows
        return {
            candidate.candidate_id: self._decisions[candidate.candidate_id]
            for candidate in candidates
            if candidate.candidate_id in self._decisions
        }


class CodexAlignmentClassifier:
    def __init__(self, model_client: ModelClient) -> None:
        self._model_client = model_client

    def classify(
        self,
        *,
        candidates: Sequence[ExternalEvidenceCandidate],
        signal_rows: Sequence[object],
    ) -> Mapping[str, AlignmentDecision]:
        response = asyncio.run(
            self._model_client.complete(
                _alignment_request(candidates=candidates, signal_rows=signal_rows)
            )
        )
        text = _assistant_text(response)
        data = json.loads(text)
        raw_decisions = data.get("decisions") if isinstance(data, dict) else None
        if not isinstance(raw_decisions, list):
            raise ValueError("alignment response must contain decisions list")
        decisions: dict[str, AlignmentDecision] = {}
        for item in raw_decisions:
            decision = AlignmentDecision.model_validate(item)
            decisions[decision.candidate_id] = decision
        return decisions


def compile_candidates_to_evidence(
    *,
    candidates: Sequence[ExternalEvidenceCandidate],
    signal_rows: Sequence[SignalBoardRow],
    classifier: AlignmentClassifier | None = None,
) -> tuple[EvidenceItemInput, ...]:
    decisions: Mapping[str, object] = {}
    if classifier is not None and candidates:
        decisions = classifier.classify(candidates=candidates, signal_rows=signal_rows)
    signals_by_ticker = _signals_by_ticker(signal_rows)
    evidence = [
        _candidate_to_evidence(
            candidate=candidate,
            decision=AlignmentDecision.model_validate(decisions[candidate.candidate_id])
            if candidate.candidate_id in decisions
            else None,
            signal=signals_by_ticker.get(candidate.ticker),
        )
        for candidate in candidates
    ]
    return tuple(evidence)


def _candidate_to_evidence(
    *,
    candidate: ExternalEvidenceCandidate,
    decision: AlignmentDecision | None,
    signal: SignalBoardRow | None,
) -> EvidenceItemInput:
    if signal is None or decision is None or not _is_high_confidence_alignment(decision):
        return EvidenceItemInput(
            evidence_id=candidate.candidate_id,
            ticker=candidate.ticker,
            scope=candidate.category,
            type=candidate.event_type,
            source=candidate.source_label,
            title=candidate.title,
            published_at=candidate.published_at,
            url=candidate.safe_url,
            stance="neutral",
            strength="weak",
            claim_scope=None,
            evidence_role="context",
            relevance="medium",
            novelty="unknown",
            interpretation_basis=None,
        )

    stance = "counter" if decision.alignment_stance == "counter" else "supporting"
    return EvidenceItemInput(
        evidence_id=candidate.candidate_id,
        ticker=candidate.ticker,
        scope=candidate.category,
        type=candidate.event_type,
        source=candidate.source_label,
        title=candidate.title,
        published_at=candidate.published_at,
        url=candidate.safe_url,
        stance=stance,
        strength="strong" if decision.confidence >= 0.9 else "moderate",
        claim_scope=signal.claim_scope,
        evidence_role=decision.evidence_role,
        relevance=decision.relevance,
        novelty="new",
        interpretation_basis=decision.interpretation_basis,
    )


def _is_high_confidence_alignment(decision: AlignmentDecision | None) -> bool:
    if decision is None:
        return False
    if decision.confidence < 0.8:
        return False
    if decision.alignment_stance not in {"supporting", "counter"}:
        return False
    if decision.evidence_role not in {"interpretation_support", "interpretation_challenge"}:
        return False
    if decision.relevance not in {"high", "medium"}:
        return False
    return bool(decision.interpretation_basis and decision.interpretation_basis.strip())


def _signals_by_ticker(signal_rows: Sequence[SignalBoardRow]) -> dict[str, SignalBoardRow]:
    result: dict[str, SignalBoardRow] = {}
    for signal in sorted(signal_rows, key=lambda row: row.rank):
        if signal.ticker is not None and signal.ticker not in result:
            result[signal.ticker] = signal
    return result


def _alignment_request(
    *,
    candidates: Sequence[ExternalEvidenceCandidate],
    signal_rows: Sequence[object],
) -> ModelRequest:
    claims = []
    for row in signal_rows:
        if not isinstance(row, SignalBoardRow) or row.ticker is None:
            continue
        claims.append(
            {
                "claim_scope": row.claim_scope,
                "ticker": row.ticker,
                "name": row.name,
                "rank": row.rank,
                "signal_type": row.signal_type,
                "signal_direction": row.signal_direction,
                "primary_reason": row.primary_reason,
            }
        )
    normalized_candidates = [
        {
            "candidate_id": candidate.candidate_id,
            "provider_id": candidate.provider_id,
            "category": candidate.category,
            "ticker": candidate.ticker,
            "title": candidate.title,
            "published_at": candidate.published_at,
            "event_type": candidate.event_type,
            "summary": candidate.summary,
            "metadata": candidate.metadata,
        }
        for candidate in candidates
    ]
    return ModelRequest(
        messages=(
            Message(
                role="system",
                content=(
                    TextBlock(
                        text=(
                            "Classify normalized external evidence against SignalBoard "
                            "claim summaries. Do not use outside knowledge. Return only JSON "
                            "with a decisions array. Each item must include candidate_id, "
                            "alignment_stance (supporting, counter, neutral, ambiguous), "
                            "evidence_role (interpretation_support, interpretation_challenge, "
                            "context), relevance (high, medium, low), confidence 0..1, and "
                            "interpretation_basis. Abstain as context for weak or ambiguous "
                            "matches."
                        )
                    ),
                ),
            ),
            Message(
                role="user",
                content=(
                    TextBlock(
                        text=json.dumps(
                            {
                                "signal_claims": claims,
                                "normalized_candidates": normalized_candidates,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                ),
            ),
        )
    )


def _assistant_text(response: object) -> str:
    message = getattr(response, "message", None)
    if message is None or getattr(message, "role", None) != "assistant":
        raise ValueError("alignment response must be an assistant message")
    content = getattr(message, "content", ())
    if len(content) != 1 or not isinstance(content[0], TextBlock):
        raise ValueError("alignment response must contain exactly one text block")
    return content[0].text
