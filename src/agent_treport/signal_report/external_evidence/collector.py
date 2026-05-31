from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from agent_pack.models import JsonValue

from agent_treport.signal_report.adapters.operational_holdings import (
    load_operational_signal_report_inputs,
)
from agent_treport.signal_report.domain.inputs import SignalReportInputs
from agent_treport.signal_report.domain.payload import SignalBoardRow
from agent_treport.signal_report.domain.snapshots import MultiETFHoldingsSnapshots
from agent_treport.signal_report.external_evidence.alignment import (
    compile_candidates_to_evidence,
)
from agent_treport.signal_report.external_evidence.contracts import (
    EvidenceCategory,
    ExternalEvidenceCandidate,
    ExternalEvidenceCollectionResult,
    ExternalEvidenceProvider,
    ExternalEvidenceProviderOutcome,
    ExternalEvidenceRequest,
    ExternalEvidenceRequestContext,
    ExternalEvidenceSummary,
    ExternalEvidenceTarget,
)
from agent_treport.signal_report.external_evidence.providers import (
    EXTERNAL_EVIDENCE_PROVIDER_IDS,
    create_external_evidence_provider,
)
from agent_treport.signal_report.pipeline.build_payload import build_signal_report_payload

COOLDOWN_SCHEMA_VERSION = "agent_treport.external_evidence.cooldown.v1"
SUMMARY_SCHEMA_VERSION = "agent_treport.external_evidence.summary.v1"
POLICY_FAILURE_STATUSES = {
    "credential_required",
    "blocked",
    "rate_limited_exhausted",
    "provider_unavailable",
    "invalid_provider_payload",
    "timeout_exhausted",
}
COOLDOWN_STATUSES = {"blocked", "rate_limited_exhausted"}
EVIDENCE_CATEGORIES: tuple[EvidenceCategory, ...] = ("financial", "disclosure", "news")


class ExternalEvidenceCollectionError(RuntimeError):
    def __init__(
        self,
        *,
        error_code: str,
        summary_path: str,
        evidence_path: str,
    ) -> None:
        super().__init__(f"external evidence collection failed: {error_code}")
        self.error_code = error_code
        self.summary_path = summary_path
        self.evidence_path = evidence_path


def collect_external_evidence(
    request: ExternalEvidenceRequest,
) -> ExternalEvidenceCollectionResult:
    if request.max_targets <= 0:
        raise ValueError("max_targets must be a positive integer")
    provider_ids = tuple(_split_provider_ids(request.provider_ids))
    if not provider_ids:
        raise ValueError("at least one provider must be selected")
    unknown = sorted(
        set(provider_ids)
        .difference(EXTERNAL_EVIDENCE_PROVIDER_IDS)
        .difference(request.provider_overrides)
    )
    if unknown:
        raise ValueError(f"unsupported external evidence provider: {unknown[0]}")

    now = request.now or (lambda: datetime.now(UTC))
    context = ExternalEvidenceRequestContext(
        now=now,
        timeout_seconds=request.timeout_seconds,
        min_interval_seconds=request.min_interval_seconds,
    )
    if request.holdings_source == "targets":
        targets, excluded_targets, signal_rows = _load_existing_target_candidates(request)
    else:
        inputs = _load_signal_report_inputs(request)
        preliminary_payload = build_signal_report_payload(
            snapshots=inputs.snapshots,
            focus_etf_id=inputs.focus_etf_id,
            focus_etf_ids=inputs.focus_etf_ids,
            evidence=(),
        )
        signal_rows = preliminary_payload.signal_board
        targets, excluded_targets = _select_targets(
            signal_rows=signal_rows,
            snapshots=inputs.snapshots,
            max_targets=request.max_targets,
        )
    cooldowns = _read_cooldowns(Path(request.cooldown_path)) if request.cooldown_path else {}
    candidates: list[ExternalEvidenceCandidate] = []
    outcomes: list[ExternalEvidenceProviderOutcome] = []

    for provider_id in provider_ids:
        provider = _provider_for(provider_id, request.provider_overrides)
        active_until: datetime | None = None
        active_reason: str | None = None
        if not request.ignore_cooldown:
            active_until, active_reason = _active_cooldown(
                provider_id=provider.provider_id,
                cooldowns=cooldowns,
                now=now(),
            )
        if active_until is not None:
            outcomes.append(
                ExternalEvidenceProviderOutcome(
                    provider_id=provider.provider_id,
                    category=provider.category,
                    status="cooldown_active",
                    error_code="cooldown_active",
                    retryable=False,
                    attempt_count=0,
                    stopped_reason=active_reason,
                    target_tickers=tuple(target.ticker for target in targets),
                    safe_message="Provider cooldown is active; live calls were skipped.",
                )
            )
            continue
        provider_candidates, outcome = provider.collect(
            targets,
            live=request.live,
            request_context=context,
        )
        outcomes.append(outcome)
        candidates.extend(provider_candidates)
        if outcome.status in COOLDOWN_STATUSES and request.cooldown_path is not None:
            cooldown_duration = _provider_cooldown_duration(provider.provider_id)
            cooldowns[provider.provider_id] = {
                "cooldown_until": (now() + cooldown_duration).isoformat(),
                "reason": outcome.error_code or outcome.status,
            }

    deduped_candidates, dedupe = _dedupe_candidates(candidates)
    evidence = compile_candidates_to_evidence(
        candidates=deduped_candidates,
        signal_rows=signal_rows,
        classifier=request.classifier if request.align_claims else None,
    )
    category_coverage = _category_coverage(
        selected_targets=targets,
        excluded_targets=excluded_targets,
        candidates=deduped_candidates,
        outcomes=outcomes,
        selected_provider_ids=provider_ids,
    )
    policy_failure = _policy_failure(outcomes)
    evidence_path = Path(request.evidence_path)
    summary_path = Path(request.summary_path)
    _write_json(
        evidence_path,
        [item.model_dump(mode="json") for item in evidence],
    )
    summary = ExternalEvidenceSummary(
        schema_version=SUMMARY_SCHEMA_VERSION,
        generated_at=now().isoformat(),
        target_selection={
            "selected_targets": [target.model_dump(mode="json") for target in targets],
            "excluded_targets": excluded_targets,
            "max_targets": request.max_targets,
        },
        provider_outcomes=tuple(outcomes),
        category_coverage=category_coverage,
        dedupe=dedupe,
        policy_failure=policy_failure,
        evidence_path=str(evidence_path),
        cooldown_path=str(request.cooldown_path) if request.cooldown_path is not None else None,
        provider_limitations=tuple(_provider_limitations(outcomes)),
    )
    _write_json(summary_path, summary.model_dump(mode="json"))
    if request.cooldown_path is not None:
        _write_cooldowns(Path(request.cooldown_path), cooldowns)
    if policy_failure is not None:
        raise ExternalEvidenceCollectionError(
            error_code=str(policy_failure["error_code"]),
            summary_path=str(summary_path),
            evidence_path=str(evidence_path),
        )
    return ExternalEvidenceCollectionResult(evidence_count=len(evidence), summary=summary)


def provider_cooldown_until(
    *,
    provider_id: str,
    cooldown_path: str | Path,
    now: datetime,
) -> datetime | None:
    cooldowns = _read_cooldowns(Path(cooldown_path))
    active_until, _reason = _active_cooldown(
        provider_id=provider_id,
        cooldowns=cooldowns,
        now=now,
    )
    return active_until


def _split_provider_ids(provider_ids: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    for item in provider_ids:
        result.extend(part.strip() for part in item.split(",") if part.strip())
    return tuple(dict.fromkeys(result))


def _provider_for(
    provider_id: str,
    overrides: Mapping[str, ExternalEvidenceProvider],
) -> ExternalEvidenceProvider:
    if provider_id in overrides:
        return overrides[provider_id]
    return create_external_evidence_provider(provider_id)


def _load_signal_report_inputs(request: ExternalEvidenceRequest) -> SignalReportInputs:
    if request.holdings_source == "fixture":
        if request.holdings_path is None:
            raise ValueError("fixture holdings source requires holdings_path")
        holdings = json.loads(Path(request.holdings_path).read_text(encoding="utf-8"))
        if not isinstance(holdings, Mapping):
            raise ValueError("fixture holdings input must be a JSON object")
        snapshots = MultiETFHoldingsSnapshots.model_validate(holdings.get("snapshots"))
        focus_etf_id = request.focus_etf_id
        if focus_etf_id is None:
            focus_value = holdings.get("focus_etf_id")
            focus_etf_id = focus_value if isinstance(focus_value, str) else None
        return SignalReportInputs(
            snapshots=snapshots,
            focus_etf_id=focus_etf_id,
            focus_etf_ids=tuple(request.focus_etf_ids or ()),
            evidence=(),
        )
    if request.holdings_source == "operational":
        if request.holdings_path is None:
            raise ValueError("operational holdings source requires holdings_path")
        inputs, _provenance = load_operational_signal_report_inputs(
            manifest_path=request.holdings_path,
            focus_etf_id=request.focus_etf_id,
            focus_etf_ids=request.focus_etf_ids,
            observed_partitions=request.observed_partitions,
            evidence_path=None,
        )
        return inputs
    if request.holdings_source == "targets":
        raise ValueError("target-candidates source is handled before holdings loading")
    raise ValueError(f"unsupported holdings source: {request.holdings_source}")


def _load_existing_target_candidates(
    request: ExternalEvidenceRequest,
) -> tuple[
    tuple[ExternalEvidenceTarget, ...],
    list[dict[str, JsonValue]],
    tuple[SignalBoardRow, ...],
]:
    if request.target_candidates_path is None:
        raise ValueError("targets holdings source requires target_candidates_path")
    data = json.loads(Path(request.target_candidates_path).read_text(encoding="utf-8"))
    rows_value = data.get("signal_board") if isinstance(data, Mapping) else data
    if not isinstance(rows_value, list):
        raise ValueError("target candidates input must be a list or object with signal_board")

    full_signal_rows: list[SignalBoardRow] = []
    full_rows_available = True
    for item in rows_value:
        try:
            full_signal_rows.append(SignalBoardRow.model_validate(item))
        except Exception:
            full_rows_available = False
            break
    if full_rows_available:
        targets, excluded = _select_targets_from_rows(
            rows=tuple(full_signal_rows),
            max_targets=request.max_targets,
        )
        return targets, excluded, tuple(full_signal_rows)

    selected: list[ExternalEvidenceTarget] = []
    excluded: list[dict[str, JsonValue]] = []
    parsed_rows = sorted(
        (_target_candidate_row(item) for item in rows_value if isinstance(item, Mapping)),
        key=lambda item: item["rank"],
    )
    ambiguous_claim_scopes = _ambiguous_bare_ticker_claim_scopes(parsed_rows)
    for row in parsed_rows:
        ticker = row["ticker"]
        if ticker is None:
            excluded.append({**row, "reason_code": "missing_ticker"})
            continue
        if row["claim_scope"] in ambiguous_claim_scopes:
            excluded.append({**row, "reason_code": "ambiguous_bare_ticker"})
            continue
        if len(selected) >= request.max_targets:
            excluded.append({**row, "reason_code": "max_targets_exceeded"})
            continue
        selected.append(
            ExternalEvidenceTarget(
                ticker=str(ticker),
                name=str(row["name"]),
                aggregation_key=cast(str | None, row["aggregation_key"]),
                security_group_id=cast(str | None, row["security_group_id"]),
                member_security_ids=tuple(
                    str(item) for item in row["member_security_ids"] if isinstance(item, str)
                )
                if isinstance(row["member_security_ids"], list)
                else (),
                listing_keys=tuple(
                    str(item) for item in row["listing_keys"] if isinstance(item, str)
                )
                if isinstance(row["listing_keys"], list)
                else (),
                claim_scope=cast(str | None, row["claim_scope"]),
                rank=int(row["rank"]),
                signal_type=cast(str | None, row["signal_type"]),
                signal_direction=cast(str | None, row["signal_direction"]),
                summary=str(row["summary"]),
            )
        )
    return tuple(selected), excluded, ()


def _select_targets_from_rows(
    *,
    rows: Sequence[SignalBoardRow],
    max_targets: int,
) -> tuple[tuple[ExternalEvidenceTarget, ...], list[dict[str, JsonValue]]]:
    selected: list[ExternalEvidenceTarget] = []
    excluded: list[dict[str, JsonValue]] = []
    ambiguous_claim_scopes = _ambiguous_bare_ticker_signal_claims(rows)
    for row in sorted(rows, key=lambda item: item.rank):
        if row.ticker is None:
            excluded.append(
                {
                    "rank": row.rank,
                    "name": row.name,
                    "reason_code": "missing_ticker",
                    "claim_scope": row.claim_scope,
                }
            )
            continue
        if row.claim_scope in ambiguous_claim_scopes:
            excluded.append(
                {
                    "rank": row.rank,
                    "ticker": row.ticker,
                    "name": row.name,
                    "reason_code": "ambiguous_bare_ticker",
                    "claim_scope": row.claim_scope,
                    "aggregation_key": row.aggregation_key,
                    "security_group_id": row.security_group_id,
                    "member_security_ids": list(row.member_security_ids),
                    "listing_keys": list(row.listing_keys),
                }
            )
            continue
        if len(selected) >= max_targets:
            excluded.append(
                {
                    "rank": row.rank,
                    "ticker": row.ticker,
                    "name": row.name,
                    "reason_code": "max_targets_exceeded",
                    "claim_scope": row.claim_scope,
                }
            )
            continue
        selected.append(
            ExternalEvidenceTarget(
                ticker=row.ticker,
                name=row.name,
                aggregation_key=row.aggregation_key,
                security_group_id=row.security_group_id,
                member_security_ids=row.member_security_ids,
                listing_keys=row.listing_keys,
                claim_scope=row.claim_scope,
                rank=row.rank,
                signal_type=row.signal_type,
                signal_direction=row.signal_direction,
                summary=row.primary_reason,
            )
        )
    return tuple(selected), excluded


def _target_candidate_row(item: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    ticker = item.get("ticker")
    rank = item.get("rank")
    name = item.get("name")
    if not isinstance(rank, int) or isinstance(rank, bool):
        raise ValueError("target candidate rank must be an integer")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("target candidate name must be a non-empty string")
    if ticker is not None and not isinstance(ticker, str):
        raise ValueError("target candidate ticker must be a string or null")
    claim_scope = item.get("claim_scope")
    signal_type = item.get("signal_type")
    signal_direction = item.get("signal_direction")
    primary_reason = item.get("primary_reason", item.get("summary"))
    aggregation_key, security_group_id, member_security_ids = _identity_from_claim_scope(
        claim_scope if isinstance(claim_scope, str) else None
    )
    raw_listing_keys = item.get("listing_keys")
    listing_keys = (
        [value for value in raw_listing_keys if isinstance(value, str)]
        if isinstance(raw_listing_keys, list)
        else []
    )
    return {
        "rank": rank,
        "ticker": ticker,
        "name": name,
        "aggregation_key": item.get("aggregation_key")
        if isinstance(item.get("aggregation_key"), str)
        else aggregation_key,
        "security_group_id": item.get("security_group_id")
        if isinstance(item.get("security_group_id"), str)
        else security_group_id,
        "member_security_ids": item.get("member_security_ids")
        if isinstance(item.get("member_security_ids"), list)
        else member_security_ids,
        "listing_keys": listing_keys,
        "claim_scope": claim_scope if isinstance(claim_scope, str) else None,
        "signal_type": signal_type if isinstance(signal_type, str) else None,
        "signal_direction": signal_direction if isinstance(signal_direction, str) else None,
        "summary": primary_reason if isinstance(primary_reason, str) else "",
    }


def _select_targets(
    *,
    signal_rows: Sequence[SignalBoardRow],
    snapshots: MultiETFHoldingsSnapshots,
    max_targets: int,
) -> tuple[tuple[ExternalEvidenceTarget, ...], list[dict[str, JsonValue]]]:
    selected: list[ExternalEvidenceTarget] = []
    excluded: list[dict[str, JsonValue]] = []
    ambiguous_claim_scopes = _ambiguous_bare_ticker_signal_claims(signal_rows)
    for row in sorted(signal_rows, key=lambda item: item.rank):
        if row.ticker is None:
            excluded.append(
                {
                    "rank": row.rank,
                    "name": row.name,
                    "reason_code": "missing_ticker",
                    "claim_scope": row.claim_scope,
                }
            )
            continue
        if row.claim_scope in ambiguous_claim_scopes:
            excluded.append(
                {
                    "rank": row.rank,
                    "ticker": row.ticker,
                    "name": row.name,
                    "reason_code": "ambiguous_bare_ticker",
                    "claim_scope": row.claim_scope,
                    "aggregation_key": row.aggregation_key,
                    "security_group_id": row.security_group_id,
                    "member_security_ids": list(row.member_security_ids),
                    "listing_keys": list(row.listing_keys),
                }
            )
            continue
        if len(selected) >= max_targets:
            excluded.append(
                {
                    "rank": row.rank,
                    "ticker": row.ticker,
                    "name": row.name,
                    "reason_code": "max_targets_exceeded",
                    "claim_scope": row.claim_scope,
                }
            )
            continue
        selected.append(
            ExternalEvidenceTarget(
                ticker=row.ticker,
                name=row.name,
                aggregation_key=row.aggregation_key,
                security_group_id=row.security_group_id,
                member_security_ids=row.member_security_ids,
                listing_keys=row.listing_keys,
                claim_scope=row.claim_scope,
                rank=row.rank,
                signal_type=row.signal_type,
                signal_direction=row.signal_direction,
                summary=row.primary_reason,
            )
        )
    selected_tickers = {target.ticker for target in selected}
    for etf in snapshots.etfs:
        for holding in etf.current:
            if holding.is_cash:
                excluded.append(
                    {
                        "ticker": holding.ticker,
                        "name": holding.name,
                        "reason_code": "cash_like",
                        "scope": f"{etf.etf_id}:{holding.security_id}",
                    }
                )
            elif holding.ticker is None:
                excluded.append(
                    {
                        "ticker": None,
                        "name": holding.name,
                        "reason_code": "missing_ticker",
                        "scope": f"{etf.etf_id}:{holding.security_id}",
                    }
                )
            elif holding.ticker not in selected_tickers:
                continue
    return tuple(selected), excluded


def _dedupe_candidates(
    candidates: Sequence[ExternalEvidenceCandidate],
) -> tuple[tuple[ExternalEvidenceCandidate, ...], dict[str, JsonValue]]:
    representatives: dict[tuple[str, ...], ExternalEvidenceCandidate] = {}
    duplicates_by_category: dict[str, int] = defaultdict(int)
    provider_overlap: list[dict[str, JsonValue]] = []
    for candidate in candidates:
        key = _dedupe_key(candidate)
        existing = representatives.get(key)
        if existing is None:
            representatives[key] = candidate
            continue
        duplicates_by_category[candidate.category] += 1
        provider_overlap.append(
            {
                "category": candidate.category,
                "ticker": candidate.ticker,
                "representative_provider_id": existing.provider_id,
                "duplicate_provider_id": candidate.provider_id,
            }
        )
        if _candidate_richness(candidate) > _candidate_richness(existing):
            representatives[key] = candidate
    ordered = tuple(
        sorted(
            representatives.values(),
            key=lambda item: (
                item.ticker,
                item.category,
                item.published_at or "",
                item.title,
                item.provider_id,
            ),
        )
    )
    return ordered, {
        "deduped_count": sum(duplicates_by_category.values()),
        "by_category": dict(sorted(duplicates_by_category.items())),
        "provider_overlap": provider_overlap,
    }


def _dedupe_key(candidate: ExternalEvidenceCandidate) -> tuple[str, ...]:
    filing_or_article = ""
    if candidate.disclosure is not None:
        filing_or_article = candidate.disclosure.filing_id
    elif candidate.news is not None and candidate.news.article_id is not None:
        filing_or_article = candidate.news.article_id
    return (
        candidate.ticker.upper(),
        candidate.category,
        candidate.event_type,
        _normalize_text(candidate.title),
        _normalize_text(candidate.source_label),
        _published_date(candidate.published_at),
        filing_or_article,
    )


def _candidate_richness(candidate: ExternalEvidenceCandidate) -> tuple[int, int, int, int]:
    return (
        1 if candidate.safe_url else 0,
        1 if candidate.published_at else 0,
        1 if candidate.disclosure is not None and candidate.disclosure.filing_id else 0,
        len(candidate.title),
    )


def _normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _published_date(value: str | None) -> str:
    if value is None:
        return ""
    return value[:10]


def _category_coverage(
    *,
    selected_targets: Sequence[ExternalEvidenceTarget],
    excluded_targets: Sequence[Mapping[str, JsonValue]],
    candidates: Sequence[ExternalEvidenceCandidate],
    outcomes: Sequence[ExternalEvidenceProviderOutcome],
    selected_provider_ids: Sequence[str],
) -> dict[str, JsonValue]:
    selected_tickers = tuple(target.ticker for target in selected_targets)
    candidate_categories_by_ticker: dict[tuple[str, str], int] = defaultdict(int)
    for candidate in candidates:
        candidate_categories_by_ticker[(candidate.category, candidate.ticker)] += 1
    outcome_by_category: dict[str, list[ExternalEvidenceProviderOutcome]] = defaultdict(list)
    for outcome in outcomes:
        outcome_by_category[outcome.category].append(outcome)
    coverage: dict[str, JsonValue] = {}
    for category in EVIDENCE_CATEGORIES:
        category_outcomes = outcome_by_category.get(category, [])
        ticker_states: dict[str, str] = {}
        notes: list[str] = []
        for excluded in excluded_targets:
            if excluded.get("reason_code") != "ambiguous_bare_ticker":
                continue
            ticker = excluded.get("ticker")
            if isinstance(ticker, str):
                ticker_states[ticker] = "excluded_ambiguous_bare_ticker"
                notes.append(f"{category}:{ticker}=excluded_ambiguous_bare_ticker")
        for ticker in selected_tickers:
            state = _ticker_state(
                category=category,
                ticker=ticker,
                has_candidate=candidate_categories_by_ticker[(category, ticker)] > 0,
                outcomes=category_outcomes,
                selected_provider_ids=selected_provider_ids,
            )
            ticker_states[ticker] = state
            notes.append(f"{category}:{ticker}={state}")
        covered = sum(1 for state in ticker_states.values() if state == "covered")
        coverage[category] = {
            "coverage_ratio": _ratio(covered, len(selected_tickers)),
            "ticker_states": ticker_states,
            "notes": notes,
            "provider_states": {
                outcome.provider_id: outcome.status for outcome in category_outcomes
            },
        }
    return coverage


def _identity_from_claim_scope(
    claim_scope: str | None,
) -> tuple[str | None, str | None, list[str]]:
    if claim_scope is None:
        return None, None, []
    parts = claim_scope.split(":")
    if len(parts) < 4 or parts[0] != "signal":
        return None, None, []
    identity_kind = parts[1]
    identity_value = parts[2]
    if identity_kind == "security_group":
        return identity_value, identity_value, []
    if identity_kind == "security":
        return identity_value, None, [identity_value]
    return None, None, []


def _ambiguous_bare_ticker_claim_scopes(
    rows: Sequence[Mapping[str, JsonValue]],
) -> set[str]:
    claims_by_ticker: dict[str, set[str]] = defaultdict(set)
    bare_claims_by_ticker: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        ticker = row.get("ticker")
        claim_scope = row.get("claim_scope")
        if not isinstance(ticker, str) or not isinstance(claim_scope, str):
            continue
        identity_key = _target_identity_key(row)
        claims_by_ticker[ticker].add(identity_key)
        listing_keys = row.get("listing_keys")
        if not isinstance(listing_keys, list) or not listing_keys:
            bare_claims_by_ticker[ticker].add(claim_scope)
    ambiguous: set[str] = set()
    for ticker, identities in claims_by_ticker.items():
        if len(identities) > 1:
            ambiguous.update(bare_claims_by_ticker.get(ticker, set()))
    return ambiguous


def _ambiguous_bare_ticker_signal_claims(rows: Sequence[SignalBoardRow]) -> set[str]:
    row_dicts: list[dict[str, JsonValue]] = []
    for row in rows:
        row_dicts.append(
            {
                "ticker": row.ticker,
                "claim_scope": row.claim_scope,
                "aggregation_key": row.aggregation_key,
                "security_group_id": row.security_group_id,
                "member_security_ids": list(row.member_security_ids),
                "listing_keys": list(row.listing_keys),
            }
        )
    return _ambiguous_bare_ticker_claim_scopes(row_dicts)


def _target_identity_key(row: Mapping[str, JsonValue]) -> str:
    security_group_id = row.get("security_group_id")
    if isinstance(security_group_id, str):
        return f"security_group:{security_group_id}"
    aggregation_key = row.get("aggregation_key")
    if isinstance(aggregation_key, str):
        return f"security:{aggregation_key}"
    claim_scope = row.get("claim_scope")
    return str(claim_scope)


def _ticker_state(
    *,
    category: str,
    ticker: str,
    has_candidate: bool,
    outcomes: Sequence[ExternalEvidenceProviderOutcome],
    selected_provider_ids: Sequence[str],
) -> str:
    _ = selected_provider_ids
    if has_candidate:
        return "covered"
    if not outcomes:
        return "not_run"
    if any(outcome.status in POLICY_FAILURE_STATUSES for outcome in outcomes):
        return "failed"
    if any(outcome.status in {"cooldown_active", "skipped"} for outcome in outcomes):
        return "skipped"
    if any(outcome.status == "no_data" for outcome in outcomes):
        return "no_data"
    if any(ticker in outcome.target_tickers for outcome in outcomes):
        return "no_data"
    return "not_run"


def _policy_failure(
    outcomes: Sequence[ExternalEvidenceProviderOutcome],
) -> dict[str, JsonValue] | None:
    for outcome in outcomes:
        if outcome.status in POLICY_FAILURE_STATUSES:
            return {
                "error_code": outcome.error_code or outcome.status,
                "provider_id": outcome.provider_id,
                "category": outcome.category,
                "safe_message": outcome.safe_message,
            }
    return None


def _provider_limitations(
    outcomes: Sequence[ExternalEvidenceProviderOutcome],
) -> list[dict[str, JsonValue]]:
    limitations: list[dict[str, JsonValue]] = []
    for outcome in outcomes:
        omitted = outcome.metadata.get("omitted_target_count")
        if not isinstance(omitted, int) or omitted <= 0:
            continue
        cap = outcome.metadata.get("provider_target_cap")
        limitations.append(
            {
                "provider_id": outcome.provider_id,
                "category": outcome.category,
                "limitation_type": "provider_target_cap",
                "provider_target_cap": cap if isinstance(cap, int) else None,
                "requested_target_count": outcome.metadata.get(
                    "requested_target_count"
                ),
                "queried_target_count": outcome.metadata.get("queried_target_count"),
                "omitted_target_count": omitted,
                "policy": outcome.metadata.get("policy"),
            }
        )
    return limitations


def _read_cooldowns(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping):
        return {}
    providers = data.get("providers")
    if not isinstance(providers, Mapping):
        return {}
    result: dict[str, dict[str, str]] = {}
    for provider_id, value in providers.items():
        if not isinstance(provider_id, str) or not isinstance(value, Mapping):
            continue
        until = value.get("cooldown_until")
        reason = value.get("reason")
        if isinstance(until, str) and isinstance(reason, str):
            result[provider_id] = {"cooldown_until": until, "reason": reason}
    return result


def _write_cooldowns(path: Path, cooldowns: Mapping[str, Mapping[str, str]]) -> None:
    payload = {
        "schema_version": COOLDOWN_SCHEMA_VERSION,
        "providers": cooldowns,
    }
    _write_json(path, payload)


def _active_cooldown(
    *,
    provider_id: str,
    cooldowns: Mapping[str, Mapping[str, str]],
    now: datetime,
) -> tuple[datetime | None, str | None]:
    item = cooldowns.get(provider_id)
    if item is None:
        return None, None
    try:
        until = datetime.fromisoformat(item["cooldown_until"].replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None, None
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    if until <= now:
        return None, None
    return until, item.get("reason")


def _provider_cooldown_duration(provider_id: str) -> timedelta:
    if provider_id == "sec_edgar":
        return timedelta(minutes=15)
    return timedelta(hours=24)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 4)
