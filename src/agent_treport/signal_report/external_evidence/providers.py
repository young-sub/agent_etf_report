from __future__ import annotations

import importlib
import os
import random
import re
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import cast
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import requests
from agent_pack.models import JsonValue

from agent_treport.signal_report.external_evidence.contracts import (
    DisclosureEvidenceDetails,
    EvidenceCategory,
    ExternalEvidenceCandidate,
    ExternalEvidenceProvider,
    ExternalEvidenceProviderOutcome,
    ExternalEvidenceRequestContext,
    ExternalEvidenceTarget,
    FinancialEvidenceDetails,
    NewsEvidenceDetails,
)
from agent_treport.signal_report.external_evidence.url_safety import safe_public_url

EXTERNAL_EVIDENCE_PROVIDER_IDS = (
    "fixture_financial",
    "fixture_disclosure",
    "fixture_news",
    "finnhub",
    "yfinance",
    "dart",
    "sec_edgar",
    "alpha_vantage",
    "newsapi",
    "naver",
)

_LIVE_PROVIDER_IDS = {
    "finnhub",
    "yfinance",
    "dart",
    "sec_edgar",
    "alpha_vantage",
    "newsapi",
    "naver",
}
SEC_MIN_REQUEST_INTERVAL_SECONDS = 0.11
ALPHA_VANTAGE_MAX_NEWS_SENTIMENT_TARGETS = 5


class ProviderRequestError(RuntimeError):
    def __init__(
        self,
        *,
        error_code: str,
        safe_message: str,
        retryable: bool,
        attempt_count: int,
        stopped_reason: str | None = None,
    ) -> None:
        super().__init__(safe_message)
        self.error_code = error_code
        self.safe_message = safe_message
        self.retryable = retryable
        self.attempt_count = attempt_count
        self.stopped_reason = stopped_reason


def create_external_evidence_provider(provider_id: str) -> ExternalEvidenceProvider:
    if provider_id == "fixture_financial":
        return FixtureFinancialProvider()
    if provider_id == "fixture_disclosure":
        return FixtureDisclosureProvider()
    if provider_id == "fixture_news":
        return FixtureNewsProvider()
    if provider_id == "finnhub":
        return FinnhubProvider()
    if provider_id == "yfinance":
        return YFinanceProvider()
    if provider_id == "dart":
        return DartProvider()
    if provider_id == "sec_edgar":
        return SecEdgarProvider()
    if provider_id == "alpha_vantage":
        return AlphaVantageProvider()
    if provider_id == "newsapi":
        return NewsApiProvider()
    if provider_id == "naver":
        return NaverNewsProvider()
    raise ValueError(f"unsupported external evidence provider: {provider_id}")


def is_live_provider(provider_id: str) -> bool:
    return provider_id in _LIVE_PROVIDER_IDS


def _env_value(name: str) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    env_path = Path.cwd() / ".env"
    if not env_path.is_file():
        return None
    try:
        dotenv = importlib.import_module("dotenv")
        values = dotenv.dotenv_values(env_path)
    except Exception:
        return None
    dotenv_value = values.get(name)
    if isinstance(dotenv_value, str) and dotenv_value:
        return dotenv_value
    return None


class FixtureFinancialProvider:
    provider_id = "fixture_financial"
    category: EvidenceCategory = "financial"

    def collect(
        self,
        targets: Sequence[ExternalEvidenceTarget],
        *,
        live: bool,
        request_context: ExternalEvidenceRequestContext,
    ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
        _ = live, request_context
        candidates = [
            ExternalEvidenceCandidate(
                candidate_id=f"fixture_financial_{target.ticker.lower()}",
                provider_id=self.provider_id,
                category=self.category,
                ticker=target.ticker,
                source_label="Fixture Financial Metrics",
                title=f"{target.ticker} fixture price-volume signal",
                published_at="2026-05-15T00:00:00+00:00",
                event_type="price_volume",
                summary=(
                    f"Fixture normalized financial evidence for {target.ticker}; "
                    "no raw provider payload is retained."
                ),
                metadata={"event_kind": "fixture_financial_metric"},
                financial=FinancialEvidenceDetails(
                    metric="five_day_price_change_pct",
                    value=4.2,
                    unit="percent",
                    period="5d",
                ),
            )
            for target in targets
        ]
        return candidates, _outcome(
            provider_id=self.provider_id,
            category=self.category,
            status="success" if candidates else "no_data",
            targets=targets,
            safe_message="Fixture financial evidence collected.",
        )


class FixtureDisclosureProvider:
    provider_id = "fixture_disclosure"
    category: EvidenceCategory = "disclosure"

    def collect(
        self,
        targets: Sequence[ExternalEvidenceTarget],
        *,
        live: bool,
        request_context: ExternalEvidenceRequestContext,
    ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
        _ = live, request_context
        candidates = [
            ExternalEvidenceCandidate(
                candidate_id=f"fixture_disclosure_{target.ticker.lower()}",
                provider_id=self.provider_id,
                category=self.category,
                ticker=target.ticker,
                source_label="Fixture Disclosure",
                title=f"{target.ticker} fixture company disclosure",
                published_at="2026-05-14T00:00:00+00:00",
                event_type="company_disclosure",
                summary=f"Fixture normalized disclosure evidence for {target.ticker}.",
                safe_url=safe_public_url(
                    f"https://www.sec.gov/Archives/edgar/data/000000/"
                    f"{target.ticker.lower()}-fixture-index.html",
                    provider_id=self.provider_id,
                ),
                metadata={"event_kind": "fixture_disclosure"},
                disclosure=DisclosureEvidenceDetails(
                    filing_id=f"fixture-{target.ticker.lower()}-8k",
                    filing_type="8-K",
                    filing_date="2026-05-14",
                ),
            )
            for target in targets
        ]
        return candidates, _outcome(
            provider_id=self.provider_id,
            category=self.category,
            status="success" if candidates else "no_data",
            targets=targets,
            safe_message="Fixture disclosure evidence collected.",
        )


class FixtureNewsProvider:
    provider_id = "fixture_news"
    category: EvidenceCategory = "news"

    def collect(
        self,
        targets: Sequence[ExternalEvidenceTarget],
        *,
        live: bool,
        request_context: ExternalEvidenceRequestContext,
    ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
        _ = live, request_context
        candidates: list[ExternalEvidenceCandidate] = []
        for target in targets:
            candidate = ExternalEvidenceCandidate(
                candidate_id=f"fixture_news_{target.ticker.lower()}",
                provider_id=self.provider_id,
                category=self.category,
                ticker=target.ticker,
                source_label="Fixture News",
                title=f"{target.ticker} fixture market news",
                published_at="2026-05-13T00:00:00+00:00",
                event_type="news",
                summary=f"Fixture normalized news evidence for {target.ticker}.",
                safe_url="https://example.com/fixture-news",
                metadata={"event_kind": "fixture_news"},
                news=NewsEvidenceDetails(
                    publisher="Fixture News",
                    sentiment_label="neutral",
                    article_id=f"fixture-news-{target.ticker.lower()}",
                ),
            )
            candidates.append(candidate)
            candidates.append(
                candidate.model_copy(update={"candidate_id": candidate.candidate_id + "_dup"})
            )
        return candidates, _outcome(
            provider_id=self.provider_id,
            category=self.category,
            status="success" if candidates else "no_data",
            targets=targets,
            safe_message="Fixture news evidence collected.",
        )


class FinnhubProvider:
    provider_id = "finnhub"
    category: EvidenceCategory = "financial"

    def collect(
        self,
        targets: Sequence[ExternalEvidenceTarget],
        *,
        live: bool,
        request_context: ExternalEvidenceRequestContext,
    ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
        if not live:
            return (), _skipped_live_outcome(self.provider_id, self.category, targets)
        token = _env_value("FINNHUB_API_KEY")
        if not token:
            return (), _failure_outcome(
                provider_id=self.provider_id,
                category=self.category,
                targets=targets,
                error_code="credential_required",
                stopped_reason="missing_api_key",
                safe_message="Finnhub API key is required.",
            )
        candidates: list[ExternalEvidenceCandidate] = []
        attempt_count = 0
        for target in targets:
            try:
                payload, attempts = _request_json(
                    "https://finnhub.io/api/v1/quote",
                    params={"symbol": target.ticker, "token": token},
                    headers={},
                    context=request_context,
                    provider_id=self.provider_id,
                )
            except ProviderRequestError as exc:
                return (), _outcome_from_error(
                    provider_id=self.provider_id,
                    category=self.category,
                    targets=targets,
                    error=exc,
                )
            attempt_count = max(attempt_count, attempts)
            if not isinstance(payload, Mapping) or "c" not in payload:
                return (), _failure_outcome(
                    provider_id=self.provider_id,
                    category=self.category,
                    targets=targets,
                    error_code="invalid_provider_payload",
                    stopped_reason="invalid_quote_payload",
                    safe_message="Finnhub quote payload was invalid.",
                    attempt_count=attempt_count,
                )
            price = payload.get("c")
            delta = payload.get("d")
            pct = payload.get("dp")
            if not isinstance(price, int | float) or price == 0:
                continue
            price_value = float(price)
            published_at = _timestamp_from_epoch(payload.get("t"))
            candidate_id = (
                f"finnhub_quote_{target.ticker.lower()}_"
                f"{published_at or 'latest'}"
            )
            candidates.append(
                ExternalEvidenceCandidate(
                    candidate_id=candidate_id,
                    provider_id=self.provider_id,
                    category=self.category,
                    ticker=target.ticker,
                    source_label="Finnhub quote",
                    title=f"{target.ticker} quote latest price {price_value}",
                    published_at=published_at,
                    event_type="price_volume",
                    summary=(
                        f"Finnhub normalized quote for {target.ticker}: "
                        f"change={delta}, pct={pct}."
                    ),
                    metadata={"event_kind": "quote"},
                    financial=FinancialEvidenceDetails(
                        metric="latest_price",
                        value=price_value,
                        comparison_value=float(delta) if isinstance(delta, int | float) else None,
                        unit="provider_currency",
                    ),
                )
            )
        return candidates, _outcome(
            provider_id=self.provider_id,
            category=self.category,
            status="success" if candidates else "no_data",
            targets=targets,
            safe_message="Finnhub financial evidence collected."
            if candidates
            else "Finnhub returned no financial evidence for selected targets.",
            attempt_count=max(1, attempt_count),
        )


class YFinanceProvider:
    provider_id = "yfinance"
    category: EvidenceCategory = "financial"

    def collect(
        self,
        targets: Sequence[ExternalEvidenceTarget],
        *,
        live: bool,
        request_context: ExternalEvidenceRequestContext,
    ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
        _ = request_context
        if not live:
            return (), _skipped_live_outcome(self.provider_id, self.category, targets)
        try:
            yfinance = importlib.import_module("yfinance")
        except ModuleNotFoundError:
            return (), _failure_outcome(
                provider_id=self.provider_id,
                category=self.category,
                targets=targets,
                error_code="provider_unavailable",
                stopped_reason="missing_optional_dependency",
                safe_message="yfinance is not installed in this environment.",
            )
        candidates: list[ExternalEvidenceCandidate] = []
        try:
            for target in targets:
                history = yfinance.Ticker(target.ticker).history(period="5d")
                if history.empty or "Close" not in history:
                    continue
                closes = list(history["Close"])
                latest = float(closes[-1])
                previous = float(closes[0])
                change_pct = round(((latest - previous) / previous) * 100, 4) if previous else None
                candidates.append(
                    ExternalEvidenceCandidate(
                        candidate_id=f"yfinance_history_{target.ticker.lower()}",
                        provider_id=self.provider_id,
                        category=self.category,
                        ticker=target.ticker,
                        source_label="yfinance",
                        title=f"{target.ticker} yfinance five-day close change",
                        published_at=None,
                        event_type="price_volume",
                        summary=(
                            f"yfinance normalized close history for {target.ticker}: "
                            f"latest={latest}, change_pct={change_pct}."
                        ),
                        metadata={"event_kind": "history"},
                        financial=FinancialEvidenceDetails(
                            metric="five_day_close_change_pct",
                            value=change_pct,
                            comparison_value=latest,
                            unit="percent",
                            period="5d",
                        ),
                    )
                )
        except Exception as exc:
            message = str(exc).lower()
            error_code = "rate_limited_exhausted" if "rate" in message else "provider_unavailable"
            return (), _failure_outcome(
                provider_id=self.provider_id,
                category=self.category,
                targets=targets,
                error_code=error_code,
                stopped_reason=error_code,
                safe_message="yfinance live collection failed.",
            )
        return candidates, _outcome(
            provider_id=self.provider_id,
            category=self.category,
            status="success" if candidates else "no_data",
            targets=targets,
            safe_message="yfinance financial evidence collected."
            if candidates
            else "yfinance returned no financial evidence for selected targets.",
        )


class DartProvider:
    provider_id = "dart"
    category: EvidenceCategory = "disclosure"

    def collect(
        self,
        targets: Sequence[ExternalEvidenceTarget],
        *,
        live: bool,
        request_context: ExternalEvidenceRequestContext,
    ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
        if not live:
            return (), _skipped_live_outcome(self.provider_id, self.category, targets)
        api_key = _env_value("DART_API_KEY")
        if not api_key:
            return (), _failure_outcome(
                provider_id=self.provider_id,
                category=self.category,
                targets=targets,
                error_code="credential_required",
                stopped_reason="missing_api_key",
                safe_message="DART API key is required.",
            )
        try:
            corp_codes, attempt_count = _fetch_dart_corp_codes(
                api_key=api_key,
                context=request_context,
            )
        except ProviderRequestError as exc:
            return (), _outcome_from_error(
                provider_id=self.provider_id,
                category=self.category,
                targets=targets,
                error=exc,
            )
        candidates: list[ExternalEvidenceCandidate] = []
        for target in targets:
            corp_code = corp_codes.get(target.ticker)
            if corp_code is None:
                continue
            end_date = request_context.now().date()
            start_date = end_date - timedelta(days=365)
            try:
                payload, attempts = _request_json(
                    "https://opendart.fss.or.kr/api/list.json",
                    params={
                        "crtfc_key": api_key,
                        "corp_code": corp_code,
                        "bgn_de": start_date.strftime("%Y%m%d"),
                        "end_de": end_date.strftime("%Y%m%d"),
                        "sort": "date",
                        "sort_mth": "desc",
                        "page_count": "5",
                    },
                    headers={},
                    context=request_context,
                    provider_id=self.provider_id,
                )
            except ProviderRequestError as exc:
                return (), _outcome_from_error(
                    provider_id=self.provider_id,
                    category=self.category,
                    targets=targets,
                    error=exc,
                )
            attempt_count = max(attempt_count, attempts)
            if not isinstance(payload, Mapping):
                return (), _invalid_payload_outcome(self.provider_id, self.category, targets)
            status = str(payload.get("status", ""))
            if status == "013":
                continue
            if status != "000":
                return (), _dart_error_outcome(
                    status=status,
                    provider_id=self.provider_id,
                    category=self.category,
                    targets=targets,
                    attempt_count=attempt_count,
                )
            filings = payload.get("list")
            if not isinstance(filings, list):
                return (), _invalid_payload_outcome(self.provider_id, self.category, targets)
            for filing in filings[:1]:
                if not isinstance(filing, Mapping):
                    continue
                rcept_no = _string(filing.get("rcept_no"))
                report_nm = _string(filing.get("report_nm")) or "DART filing"
                rcept_dt = _string(filing.get("rcept_dt"))
                if rcept_no is None:
                    continue
                candidates.append(
                    ExternalEvidenceCandidate(
                        candidate_id=f"dart_{target.ticker.lower()}_{rcept_no}",
                        provider_id=self.provider_id,
                        category=self.category,
                        ticker=target.ticker,
                        source_label="DART filing",
                        title=f"{target.ticker} {report_nm}",
                        published_at=_date_to_timestamp(rcept_dt),
                        event_type="company_disclosure",
                        summary=f"DART normalized filing for {target.ticker}: {report_nm}.",
                        safe_url=safe_public_url(
                            f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
                            provider_id=self.provider_id,
                        ),
                        metadata={"event_kind": "filing"},
                        disclosure=DisclosureEvidenceDetails(
                            filing_id=rcept_no,
                            filing_type=report_nm,
                            filing_date=rcept_dt,
                        ),
                    )
                )
        return candidates, _outcome(
            provider_id=self.provider_id,
            category=self.category,
            status="success" if candidates else "no_data",
            targets=targets,
            safe_message="DART disclosure evidence collected."
            if candidates
            else "DART returned no disclosure evidence for selected targets.",
            attempt_count=max(1, attempt_count),
        )


class SecEdgarProvider:
    provider_id = "sec_edgar"
    category: EvidenceCategory = "disclosure"

    def collect(
        self,
        targets: Sequence[ExternalEvidenceTarget],
        *,
        live: bool,
        request_context: ExternalEvidenceRequestContext,
    ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
        if not live:
            return (), _skipped_live_outcome(self.provider_id, self.category, targets)
        user_agent = _env_value("SEC_USER_AGENT")
        if not user_agent:
            return (), _failure_outcome(
                provider_id=self.provider_id,
                category=self.category,
                targets=targets,
                error_code="credential_required",
                stopped_reason="missing_user_agent",
                safe_message="SEC EDGAR user agent is required.",
            )
        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
        }
        context = _sec_request_context(request_context)
        try:
            ticker_map, attempts = _sec_load_ticker_map(
                headers=headers,
                context=context,
                provider_id=self.provider_id,
            )
        except ProviderRequestError as exc:
            return (), _outcome_from_error(
                provider_id=self.provider_id,
                category=self.category,
                targets=targets,
                error=exc,
            )
        candidates: list[ExternalEvidenceCandidate] = []
        attempt_count = attempts
        for target in targets:
            cik = ticker_map.get(target.ticker.upper())
            if cik is None:
                continue
            try:
                _sec_sleep_between_requests(context)
                submissions, attempts = _request_json(
                    f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                    params={},
                    headers=headers,
                    context=context,
                    provider_id=self.provider_id,
                )
            except ProviderRequestError as exc:
                return (), _outcome_from_error(
                    provider_id=self.provider_id,
                    category=self.category,
                    targets=targets,
                    error=exc,
                )
            attempt_count = max(attempt_count, attempts)
            candidate = _sec_latest_filing_candidate(
                target=target,
                cik=int(cik),
                submissions=submissions,
            )
            if candidate is not None:
                candidates.append(candidate)
        return candidates, _outcome(
            provider_id=self.provider_id,
            category=self.category,
            status="success" if candidates else "no_data",
            targets=targets,
            safe_message="SEC EDGAR disclosure evidence collected."
            if candidates
            else "SEC EDGAR returned no disclosure evidence for selected targets.",
            attempt_count=max(1, attempt_count),
        )


class AlphaVantageProvider:
    provider_id = "alpha_vantage"
    category: EvidenceCategory = "news"

    def collect(
        self,
        targets: Sequence[ExternalEvidenceTarget],
        *,
        live: bool,
        request_context: ExternalEvidenceRequestContext,
    ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
        if not live:
            return (), _skipped_live_outcome(self.provider_id, self.category, targets)
        api_key = _env_value("ALPHAVANTAGE_API_KEY")
        if not api_key:
            return (), _failure_outcome(
                provider_id=self.provider_id,
                category=self.category,
                targets=targets,
                error_code="credential_required",
                stopped_reason="missing_api_key",
                safe_message="Alpha Vantage API key is required.",
            )
        queried_targets = tuple(targets[:ALPHA_VANTAGE_MAX_NEWS_SENTIMENT_TARGETS])
        limitation_metadata = _alpha_vantage_policy_metadata(
            requested_targets=targets,
            queried_targets=queried_targets,
        )
        if not queried_targets:
            return (), _outcome(
                provider_id=self.provider_id,
                category=self.category,
                status="no_data",
                targets=queried_targets,
                safe_message="Alpha Vantage returned no news evidence for selected targets.",
                metadata=limitation_metadata,
            )
        candidates: list[ExternalEvidenceCandidate] = []
        try:
            payload, attempt_count = _request_json(
                "https://www.alphavantage.co/query",
                params={
                    "function": "NEWS_SENTIMENT",
                    "tickers": ",".join(target.ticker for target in queried_targets),
                    "limit": "50",
                    "apikey": api_key,
                },
                headers={},
                context=request_context,
                provider_id=self.provider_id,
            )
        except ProviderRequestError as exc:
            return (), _outcome_from_error(
                provider_id=self.provider_id,
                category=self.category,
                targets=queried_targets,
                error=exc,
                metadata=limitation_metadata,
            )
        if not isinstance(payload, Mapping):
            return (), _invalid_payload_outcome(
                self.provider_id,
                self.category,
                queried_targets,
                metadata=limitation_metadata,
            )
        note = payload.get("Note") or payload.get("Information")
        if isinstance(note, str) and note:
            return (), _failure_outcome(
                provider_id=self.provider_id,
                category=self.category,
                targets=queried_targets,
                error_code="rate_limited_exhausted"
                if "frequency" in note.lower() or "rate" in note.lower()
                else "provider_unavailable",
                stopped_reason="provider_message",
                safe_message="Alpha Vantage returned a provider message instead of news.",
                attempt_count=attempt_count,
                metadata=limitation_metadata,
            )
        feed = payload.get("feed")
        if not isinstance(feed, list):
            return (), _invalid_payload_outcome(
                self.provider_id,
                self.category,
                queried_targets,
                metadata=limitation_metadata,
            )
        candidates.extend(
            _alpha_vantage_grouped_candidates(
                targets=queried_targets,
                feed=feed,
            )
        )
        return candidates, _outcome(
            provider_id=self.provider_id,
            category=self.category,
            status="success" if candidates else "no_data",
            targets=queried_targets,
            safe_message="Alpha Vantage news evidence collected."
            if candidates
            else "Alpha Vantage returned no news evidence for selected targets.",
            attempt_count=max(1, attempt_count),
            metadata=limitation_metadata,
        )


class NewsApiProvider:
    provider_id = "newsapi"
    category: EvidenceCategory = "news"

    def collect(
        self,
        targets: Sequence[ExternalEvidenceTarget],
        *,
        live: bool,
        request_context: ExternalEvidenceRequestContext,
    ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
        if not live:
            return (), _skipped_live_outcome(self.provider_id, self.category, targets)
        api_key = _env_value("NEWS_API_KEY")
        if not api_key:
            return (), _failure_outcome(
                provider_id=self.provider_id,
                category=self.category,
                targets=targets,
                error_code="credential_required",
                stopped_reason="missing_api_key",
                safe_message="NewsAPI key is required.",
            )
        candidates: list[ExternalEvidenceCandidate] = []
        attempt_count = 0
        for target in targets:
            try:
                payload, attempts = _request_json(
                    "https://newsapi.org/v2/everything",
                    params={
                        "q": target.ticker,
                        "searchIn": "title",
                        "language": "en",
                        "sortBy": "publishedAt",
                        "pageSize": "10",
                    },
                    headers={"X-Api-Key": api_key},
                    context=request_context,
                    provider_id=self.provider_id,
                )
            except ProviderRequestError as exc:
                return (), _outcome_from_error(
                    provider_id=self.provider_id,
                    category=self.category,
                    targets=targets,
                    error=exc,
                )
            attempt_count = max(attempt_count, attempts)
            if not isinstance(payload, Mapping):
                return (), _invalid_payload_outcome(self.provider_id, self.category, targets)
            if payload.get("status") == "error":
                code = str(payload.get("code", "provider_unavailable"))
                error_code = (
                    "rate_limited_exhausted"
                    if "rate" in code.lower()
                    else "credential_required"
                    if "key" in code.lower() or "auth" in code.lower()
                    else "provider_unavailable"
                )
                return (), _failure_outcome(
                    provider_id=self.provider_id,
                    category=self.category,
                    targets=targets,
                    error_code=error_code,
                    stopped_reason=code,
                    safe_message="NewsAPI returned an error status.",
                    attempt_count=attempt_count,
                )
            articles = payload.get("articles")
            if not isinstance(articles, list):
                return (), _invalid_payload_outcome(self.provider_id, self.category, targets)
            candidates.extend(_newsapi_candidates(target=target, articles=articles))
        return candidates, _outcome(
            provider_id=self.provider_id,
            category=self.category,
            status="success" if candidates else "no_data",
            targets=targets,
            safe_message="NewsAPI evidence collected."
            if candidates
            else "NewsAPI returned no news evidence for selected targets.",
            attempt_count=max(1, attempt_count),
        )


class NaverNewsProvider:
    provider_id = "naver"
    category: EvidenceCategory = "news"

    def collect(
        self,
        targets: Sequence[ExternalEvidenceTarget],
        *,
        live: bool,
        request_context: ExternalEvidenceRequestContext,
    ) -> tuple[Sequence[ExternalEvidenceCandidate], ExternalEvidenceProviderOutcome]:
        if not live:
            return (), _skipped_live_outcome(self.provider_id, self.category, targets)
        client_id = _env_value("NAVER_CLIENT_ID")
        client_secret = _env_value("NAVER_CLIENT_SECRET")
        if not client_id or not client_secret:
            return (), _failure_outcome(
                provider_id=self.provider_id,
                category=self.category,
                targets=targets,
                error_code="credential_required",
                stopped_reason="missing_client_credentials",
                safe_message="Naver client credentials are required.",
            )
        candidates: list[ExternalEvidenceCandidate] = []
        attempt_count = 0
        for target in targets:
            try:
                payload, attempts = _request_json(
                    "https://openapi.naver.com/v1/search/news.json",
                    params={"query": target.ticker, "display": "10", "sort": "date"},
                    headers={
                        "X-Naver-Client-Id": client_id,
                        "X-Naver-Client-Secret": client_secret,
                    },
                    context=request_context,
                    provider_id=self.provider_id,
                )
            except ProviderRequestError as exc:
                return (), _outcome_from_error(
                    provider_id=self.provider_id,
                    category=self.category,
                    targets=targets,
                    error=exc,
                )
            attempt_count = max(attempt_count, attempts)
            if not isinstance(payload, Mapping):
                return (), _invalid_payload_outcome(self.provider_id, self.category, targets)
            items = payload.get("items")
            if not isinstance(items, list):
                return (), _invalid_payload_outcome(self.provider_id, self.category, targets)
            candidates.extend(_naver_candidates(target=target, items=items))
        return candidates, _outcome(
            provider_id=self.provider_id,
            category=self.category,
            status="success" if candidates else "no_data",
            targets=targets,
            safe_message="Naver news evidence collected."
            if candidates
            else "Naver returned no news evidence for selected targets.",
            attempt_count=max(1, attempt_count),
        )


def _request_json(
    url: str,
    *,
    params: Mapping[str, str],
    headers: Mapping[str, str],
    context: ExternalEvidenceRequestContext,
    provider_id: str,
) -> tuple[JsonValue, int]:
    attempts = 0
    last_status: int | None = None
    for attempts in range(1, context.max_attempts + 1):
        try:
            response = requests.get(
                url,
                params=dict(params),
                headers=dict(headers),
                timeout=context.timeout_seconds,
            )
        except requests.Timeout as exc:
            if attempts >= context.max_attempts:
                raise ProviderRequestError(
                    error_code="timeout_exhausted",
                    safe_message=f"{provider_id} request timed out.",
                    retryable=False,
                    attempt_count=attempts,
                    stopped_reason="timeout",
                ) from exc
            _sleep_before_retry(context, attempts)
            continue
        except requests.ConnectionError as exc:
            if attempts >= context.max_attempts:
                raise ProviderRequestError(
                    error_code="provider_unavailable",
                    safe_message=f"{provider_id} request connection failed.",
                    retryable=False,
                    attempt_count=attempts,
                    stopped_reason="network_disconnect",
                ) from exc
            _sleep_before_retry(context, attempts)
            continue
        last_status = response.status_code
        if _http_should_retry(provider_id, response.status_code, response.text):
            if attempts < context.max_attempts:
                _sleep_before_retry(context, attempts)
                continue
            raise ProviderRequestError(
                error_code=_http_retry_exhausted_code(
                    provider_id,
                    response.status_code,
                    response.text,
                ),
                safe_message=f"{provider_id} request retry policy was exhausted.",
                retryable=False,
                attempt_count=attempts,
                stopped_reason=f"http_{response.status_code}",
            )
        if response.status_code >= 400:
            raise ProviderRequestError(
                error_code=_http_non_retry_code(provider_id, response.status_code, response.text),
                safe_message=f"{provider_id} request was rejected by provider.",
                retryable=False,
                attempt_count=attempts,
                stopped_reason=f"http_{response.status_code}",
            )
        try:
            return response.json(), attempts
        except ValueError as exc:
            raise ProviderRequestError(
                error_code="invalid_provider_payload",
                safe_message=f"{provider_id} response was not valid JSON.",
                retryable=False,
                attempt_count=attempts,
                stopped_reason="json_parse_failed",
            ) from exc
    raise ProviderRequestError(
        error_code=_http_retry_exhausted_code(provider_id, last_status or 0, ""),
        safe_message=f"{provider_id} request retry policy was exhausted.",
        retryable=False,
        attempt_count=attempts,
        stopped_reason="retry_exhausted",
    )


def _request_content(
    url: str,
    *,
    params: Mapping[str, str],
    headers: Mapping[str, str],
    context: ExternalEvidenceRequestContext,
    provider_id: str,
) -> tuple[bytes, int]:
    attempts = 0
    last_status: int | None = None
    for attempts in range(1, context.max_attempts + 1):
        try:
            response = requests.get(
                url,
                params=dict(params),
                headers=dict(headers),
                timeout=context.timeout_seconds,
            )
        except requests.Timeout as exc:
            if attempts >= context.max_attempts:
                raise ProviderRequestError(
                    error_code="timeout_exhausted",
                    safe_message=f"{provider_id} request timed out.",
                    retryable=False,
                    attempt_count=attempts,
                    stopped_reason="timeout",
                ) from exc
            _sleep_before_retry(context, attempts)
            continue
        except requests.ConnectionError as exc:
            if attempts >= context.max_attempts:
                raise ProviderRequestError(
                    error_code="provider_unavailable",
                    safe_message=f"{provider_id} request connection failed.",
                    retryable=False,
                    attempt_count=attempts,
                    stopped_reason="network_disconnect",
                ) from exc
            _sleep_before_retry(context, attempts)
            continue
        last_status = response.status_code
        if _http_should_retry(provider_id, response.status_code, response.text):
            if attempts < context.max_attempts:
                _sleep_before_retry(context, attempts)
                continue
            raise ProviderRequestError(
                error_code=_http_retry_exhausted_code(
                    provider_id,
                    response.status_code,
                    response.text,
                ),
                safe_message=f"{provider_id} request retry policy was exhausted.",
                retryable=False,
                attempt_count=attempts,
                stopped_reason=f"http_{response.status_code}",
            )
        if response.status_code >= 400:
            raise ProviderRequestError(
                error_code=_http_non_retry_code(provider_id, response.status_code, response.text),
                safe_message=f"{provider_id} request was rejected by provider.",
                retryable=False,
                attempt_count=attempts,
                stopped_reason=f"http_{response.status_code}",
            )
        return bytes(response.content), attempts
    raise ProviderRequestError(
        error_code=_http_retry_exhausted_code(provider_id, last_status or 0, ""),
        safe_message=f"{provider_id} request retry policy was exhausted.",
        retryable=False,
        attempt_count=attempts,
        stopped_reason="retry_exhausted",
    )


def _request_text(
    url: str,
    *,
    params: Mapping[str, str],
    headers: Mapping[str, str],
    context: ExternalEvidenceRequestContext,
    provider_id: str,
) -> tuple[str, int]:
    content, attempts = _request_content(
        url,
        params=params,
        headers=headers,
        context=context,
        provider_id=provider_id,
    )
    try:
        return content.decode("utf-8"), attempts
    except UnicodeDecodeError as exc:
        raise ProviderRequestError(
            error_code="invalid_provider_payload",
            safe_message=f"{provider_id} response was not valid UTF-8 text.",
            retryable=False,
            attempt_count=attempts,
            stopped_reason="text_decode_failed",
        ) from exc


def _sleep_before_retry(context: ExternalEvidenceRequestContext, attempts: int) -> None:
    delay = context.min_interval_seconds * (2 ** max(0, attempts - 1))
    time.sleep(delay + random.uniform(0, min(delay, 0.2)))


def _sec_request_context(
    context: ExternalEvidenceRequestContext,
) -> ExternalEvidenceRequestContext:
    if context.min_interval_seconds >= SEC_MIN_REQUEST_INTERVAL_SECONDS:
        return context
    return ExternalEvidenceRequestContext(
        now=context.now,
        timeout_seconds=context.timeout_seconds,
        min_interval_seconds=SEC_MIN_REQUEST_INTERVAL_SECONDS,
        max_attempts=context.max_attempts,
    )


def _sec_sleep_between_requests(context: ExternalEvidenceRequestContext) -> None:
    time.sleep(max(context.min_interval_seconds, SEC_MIN_REQUEST_INTERVAL_SECONDS))


def _http_should_retry(provider_id: str, status_code: int, text: str) -> bool:
    if status_code in {408, 429} or status_code >= 500:
        return True
    return (
        provider_id == "sec_edgar"
        and status_code == 403
        and _sec_rate_threshold_response(text)
    )


def _http_retry_exhausted_code(provider_id: str, status_code: int, text: str) -> str:
    if (
        provider_id == "sec_edgar"
        and status_code == 403
        and _sec_rate_threshold_response(text)
    ):
        return "rate_limited_exhausted"
    if status_code == 429:
        return "rate_limited_exhausted"
    if status_code == 408:
        return "timeout_exhausted"
    return "provider_unavailable"


def _http_non_retry_code(provider_id: str, status_code: int, text: str) -> str:
    lowered = text.lower()
    if provider_id == "sec_edgar" and status_code == 403 and _sec_rate_threshold_response(text):
        return "rate_limited_exhausted"
    if status_code == 403 and (
        provider_id == "sec_edgar" or "blocked" in lowered or "rate" in lowered
    ):
        return "blocked"
    if status_code in {401, 403}:
        return "credential_required"
    if status_code == 429:
        return "rate_limited_exhausted"
    return "invalid_provider_payload" if status_code == 400 else "provider_unavailable"


def _sec_rate_threshold_response(text: str) -> bool:
    lowered = text.lower()
    return (
        "request rate threshold exceeded" in lowered
        or "exceeded the allowable request rate" in lowered
        or "exceeding the sec's request rate" in lowered
    )


def _outcome(
    *,
    provider_id: str,
    category: EvidenceCategory,
    status: str,
    targets: Sequence[ExternalEvidenceTarget],
    safe_message: str,
    attempt_count: int = 1,
    error_code: str | None = None,
    retryable: bool = False,
    stopped_reason: str | None = None,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ExternalEvidenceProviderOutcome:
    return ExternalEvidenceProviderOutcome(
        provider_id=provider_id,
        category=category,
        status=status,  # type: ignore[arg-type]
        error_code=error_code,
        retryable=retryable,
        attempt_count=attempt_count,
        stopped_reason=stopped_reason,
        target_tickers=tuple(target.ticker for target in targets),
        safe_message=safe_message,
        metadata=metadata or {},
    )


def _failure_outcome(
    *,
    provider_id: str,
    category: EvidenceCategory,
    targets: Sequence[ExternalEvidenceTarget],
    error_code: str,
    stopped_reason: str,
    safe_message: str,
    attempt_count: int = 1,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ExternalEvidenceProviderOutcome:
    return _outcome(
        provider_id=provider_id,
        category=category,
        status=error_code,
        targets=targets,
        safe_message=safe_message,
        error_code=error_code,
        stopped_reason=stopped_reason,
        attempt_count=attempt_count,
        metadata=metadata,
    )


def _skipped_live_outcome(
    provider_id: str,
    category: EvidenceCategory,
    targets: Sequence[ExternalEvidenceTarget],
) -> ExternalEvidenceProviderOutcome:
    return _outcome(
        provider_id=provider_id,
        category=category,
        status="skipped",
        targets=targets,
        safe_message=f"{provider_id} live provider was skipped because --live was not set.",
        stopped_reason="live_not_enabled",
    )


def _outcome_from_error(
    *,
    provider_id: str,
    category: EvidenceCategory,
    targets: Sequence[ExternalEvidenceTarget],
    error: ProviderRequestError,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ExternalEvidenceProviderOutcome:
    return _outcome(
        provider_id=provider_id,
        category=category,
        status=error.error_code,
        targets=targets,
        safe_message=error.safe_message,
        error_code=error.error_code,
        retryable=error.retryable,
        attempt_count=error.attempt_count,
        stopped_reason=error.stopped_reason,
        metadata=metadata,
    )


def _invalid_payload_outcome(
    provider_id: str,
    category: EvidenceCategory,
    targets: Sequence[ExternalEvidenceTarget],
    *,
    metadata: Mapping[str, JsonValue] | None = None,
) -> ExternalEvidenceProviderOutcome:
    return _failure_outcome(
        provider_id=provider_id,
        category=category,
        targets=targets,
        error_code="invalid_provider_payload",
        stopped_reason="invalid_payload",
        safe_message=f"{provider_id} response payload was invalid.",
        metadata=metadata,
    )


def _dart_error_outcome(
    *,
    status: str,
    provider_id: str,
    category: EvidenceCategory,
    targets: Sequence[ExternalEvidenceTarget],
    attempt_count: int,
) -> ExternalEvidenceProviderOutcome:
    error_code = _dart_status_error_code(status)
    return _failure_outcome(
        provider_id=provider_id,
        category=category,
        targets=targets,
        error_code=error_code,
        stopped_reason=f"dart_status_{status}",
        safe_message="DART returned a non-success status.",
        attempt_count=attempt_count,
    )


def _dart_status_error_code(status: str) -> str:
    if status in {"010", "011", "012", "100", "901"}:
        return "credential_required"
    if status == "020":
        return "rate_limited_exhausted"
    if status == "800":
        return "provider_unavailable"
    return "invalid_provider_payload"


def _fetch_dart_corp_codes(
    *,
    api_key: str,
    context: ExternalEvidenceRequestContext,
) -> tuple[Mapping[str, str], int]:
    content, attempts = _request_content(
        "https://opendart.fss.or.kr/api/corpCode.xml",
        params={"crtfc_key": api_key},
        headers={},
        context=context,
        provider_id="dart",
    )
    return _parse_dart_corp_codes(content, attempt_count=attempts), attempts


def _parse_dart_corp_codes(content: bytes, *, attempt_count: int) -> Mapping[str, str]:
    xml_bytes = _dart_corp_code_xml_bytes(content, attempt_count=attempt_count)
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ProviderRequestError(
            error_code="invalid_provider_payload",
            safe_message="DART corp-code payload was invalid.",
            retryable=False,
            attempt_count=attempt_count,
            stopped_reason="corp_code_xml_parse_failed",
        ) from exc
    status = root.findtext(".//status")
    if status and status != "000":
        raise ProviderRequestError(
            error_code=_dart_status_error_code(status),
            safe_message="DART returned a non-success status.",
            retryable=False,
            attempt_count=attempt_count,
            stopped_reason=f"dart_status_{status}",
        )
    result: dict[str, str] = {}
    for item in root.findall(".//list"):
        stock_code = (item.findtext("stock_code") or "").strip().upper()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            result[stock_code] = corp_code
    return result


def _dart_corp_code_xml_bytes(content: bytes, *, attempt_count: int) -> bytes:
    stripped = content.lstrip()
    if stripped.startswith(b"<"):
        return content
    try:
        with ZipFile(BytesIO(content)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
            if not names:
                raise ProviderRequestError(
                    error_code="invalid_provider_payload",
                    safe_message="DART corp-code archive did not contain XML.",
                    retryable=False,
                    attempt_count=attempt_count,
                    stopped_reason="corp_code_zip_missing_xml",
                )
            return archive.read(names[0])
    except BadZipFile as exc:
        raise ProviderRequestError(
            error_code="invalid_provider_payload",
            safe_message="DART corp-code archive was invalid.",
            retryable=False,
            attempt_count=attempt_count,
            stopped_reason="corp_code_zip_parse_failed",
        ) from exc


_SEC_TICKER_MAP_FALLBACK_ERROR_CODES = {
    "blocked",
    "provider_unavailable",
    "invalid_provider_payload",
}


def _sec_load_ticker_map(
    *,
    headers: Mapping[str, str],
    context: ExternalEvidenceRequestContext,
    provider_id: str,
) -> tuple[dict[str, int], int]:
    try:
        payload, attempts = _request_json(
            "https://www.sec.gov/files/company_tickers.json",
            params={},
            headers=headers,
            context=context,
            provider_id=provider_id,
        )
        return _sec_ticker_map(payload, attempt_count=attempts), attempts
    except ProviderRequestError as exc:
        if exc.error_code not in _SEC_TICKER_MAP_FALLBACK_ERROR_CODES:
            raise
        _sec_sleep_between_requests(context)
        text, attempts = _request_text(
            "https://www.sec.gov/include/ticker.txt",
            params={},
            headers=headers,
            context=context,
            provider_id=provider_id,
        )
        return _sec_ticker_text_map(text, attempt_count=attempts), attempts


def _sec_ticker_map(payload: JsonValue, *, attempt_count: int = 1) -> dict[str, int]:
    if not isinstance(payload, Mapping):
        raise ProviderRequestError(
            error_code="invalid_provider_payload",
            safe_message="SEC ticker mapping payload was invalid.",
            retryable=False,
            attempt_count=attempt_count,
            stopped_reason="invalid_ticker_map",
        )
    result: dict[str, int] = {}
    for item in payload.values():
        if not isinstance(item, Mapping):
            continue
        ticker = item.get("ticker")
        cik = item.get("cik_str")
        if isinstance(ticker, str) and isinstance(cik, int):
            result[ticker.upper()] = cik
    return result


def _sec_ticker_text_map(text: str, *, attempt_count: int = 1) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        ticker, cik = parts[0], parts[1]
        if ticker and cik.isdigit():
            result[ticker.upper()] = int(cik)
    if not result:
        raise ProviderRequestError(
            error_code="invalid_provider_payload",
            safe_message="SEC ticker text mapping payload was invalid.",
            retryable=False,
            attempt_count=attempt_count,
            stopped_reason="invalid_ticker_text_map",
        )
    return result


def _sec_latest_filing_candidate(
    *,
    target: ExternalEvidenceTarget,
    cik: int,
    submissions: JsonValue,
) -> ExternalEvidenceCandidate | None:
    if not isinstance(submissions, Mapping):
        return None
    filings = submissions.get("filings")
    if not isinstance(filings, Mapping):
        return None
    recent = filings.get("recent")
    if not isinstance(recent, Mapping):
        return None
    forms = recent.get("form")
    accession_numbers = recent.get("accessionNumber")
    filing_dates = recent.get("filingDate")
    report_dates = recent.get("reportDate")
    primary_documents = recent.get("primaryDocument")
    if not all(isinstance(value, list) for value in (forms, accession_numbers, filing_dates)):
        return None
    forms_list = cast(list[object], forms)
    accession_numbers_list = cast(list[object], accession_numbers)
    filing_dates_list = cast(list[object], filing_dates)
    for index, form in enumerate(forms_list):
        if form not in {"10-K", "10-Q", "8-K", "6-K"}:
            continue
        accession = _list_string(accession_numbers_list, index)
        filing_date = _list_string(filing_dates_list, index)
        primary_document = (
            _list_string(primary_documents, index)
            if isinstance(primary_documents, list)
            else None
        )
        if accession is None:
            continue
        safe_url = _sec_filing_url(cik=cik, accession=accession, primary_document=primary_document)
        return ExternalEvidenceCandidate(
            candidate_id=f"sec_edgar_{target.ticker.lower()}_{accession.replace('-', '')}",
            provider_id="sec_edgar",
            category="disclosure",
            ticker=target.ticker,
            source_label="SEC EDGAR filing",
            title=f"{target.ticker} SEC EDGAR {form}",
            published_at=_date_to_timestamp(filing_date),
            event_type="company_disclosure",
            summary=f"SEC EDGAR normalized filing for {target.ticker}: {form}.",
            safe_url=safe_url,
            metadata={"event_kind": "filing"},
            disclosure=DisclosureEvidenceDetails(
                filing_id=accession,
                filing_type=str(form),
                filing_date=filing_date,
                report_date=_list_string(report_dates, index)
                if isinstance(report_dates, list)
                else None,
            ),
        )
    return None


def _sec_filing_url(*, cik: int, accession: str, primary_document: str | None) -> str | None:
    accession_compact = accession.replace("-", "")
    document = primary_document or f"{accession}-index.html"
    return safe_public_url(
        f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_compact}/{document}",
        provider_id="sec_edgar",
    )


def _alpha_vantage_candidates(
    *,
    target: ExternalEvidenceTarget,
    feed: Sequence[object],
    candidate_index: int | None = None,
) -> list[ExternalEvidenceCandidate]:
    candidates: list[ExternalEvidenceCandidate] = []
    for index, item in enumerate(feed[:3]):
        if not isinstance(item, Mapping):
            continue
        title = _string(item.get("title"))
        if title is None:
            continue
        source = _string(item.get("source")) or "Alpha Vantage news"
        time_published = _string(item.get("time_published"))
        candidates.append(
            ExternalEvidenceCandidate(
                candidate_id=(
                    f"alpha_vantage_{target.ticker.lower()}_"
                    f"{candidate_index if candidate_index is not None else index}"
                ),
                provider_id="alpha_vantage",
                category="news",
                ticker=target.ticker,
                source_label=source,
                title=title,
                published_at=_alpha_time_to_timestamp(time_published),
                event_type="news",
                summary=_string(item.get("summary")) or title,
                safe_url=safe_public_url(_string(item.get("url")), provider_id="alpha_vantage"),
                metadata={"event_kind": "news"},
                news=NewsEvidenceDetails(
                    publisher=source,
                    sentiment_label=_string(item.get("overall_sentiment_label")),
                    article_id=f"alpha_vantage_{index}",
                ),
            )
        )
    return candidates


def _alpha_vantage_grouped_candidates(
    *,
    targets: Sequence[ExternalEvidenceTarget],
    feed: Sequence[object],
) -> list[ExternalEvidenceCandidate]:
    candidates: list[ExternalEvidenceCandidate] = []
    targets_by_ticker = {target.ticker.upper(): target for target in targets}
    per_ticker_count: dict[str, int] = {}
    for item in feed:
        if not isinstance(item, Mapping):
            continue
        mentioned_tickers = _alpha_vantage_mentioned_tickers(item)
        for ticker in mentioned_tickers:
            target = targets_by_ticker.get(ticker)
            if target is None:
                continue
            count = per_ticker_count.get(target.ticker, 0)
            if count >= 3:
                continue
            candidates.extend(
                _alpha_vantage_candidates(
                    target=target,
                    feed=(item,),
                    candidate_index=count,
                )
            )
            per_ticker_count[target.ticker] = count + 1
    return candidates


def _alpha_vantage_mentioned_tickers(item: Mapping[str, object]) -> set[str]:
    raw = item.get("ticker_sentiment")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return set()
    result: set[str] = set()
    for sentiment in raw:
        if not isinstance(sentiment, Mapping):
            continue
        ticker = _string(sentiment.get("ticker"))
        if ticker:
            result.add(ticker.upper())
    return result


def _alpha_vantage_policy_metadata(
    *,
    requested_targets: Sequence[ExternalEvidenceTarget],
    queried_targets: Sequence[ExternalEvidenceTarget],
) -> dict[str, JsonValue]:
    requested_count = len(requested_targets)
    queried_count = len(queried_targets)
    return {
        "policy": "alpha_vantage_free_api_grouped_news_sentiment",
        "provider_target_cap": ALPHA_VANTAGE_MAX_NEWS_SENTIMENT_TARGETS,
        "requested_target_count": requested_count,
        "queried_target_count": queried_count,
        "omitted_target_count": max(0, requested_count - queried_count),
    }


def _newsapi_candidates(
    *,
    target: ExternalEvidenceTarget,
    articles: Sequence[object],
) -> list[ExternalEvidenceCandidate]:
    candidates: list[ExternalEvidenceCandidate] = []
    for index, item in enumerate(articles[:3]):
        if not isinstance(item, Mapping):
            continue
        title = _string(item.get("title"))
        if title is None:
            continue
        source = item.get("source")
        source_name = "NewsAPI"
        if isinstance(source, Mapping):
            source_name = _string(source.get("name")) or source_name
        candidates.append(
            ExternalEvidenceCandidate(
                candidate_id=f"newsapi_{target.ticker.lower()}_{index}",
                provider_id="newsapi",
                category="news",
                ticker=target.ticker,
                source_label=source_name,
                title=title,
                published_at=_string(item.get("publishedAt")),
                event_type="news",
                summary=_string(item.get("description")) or title,
                safe_url=safe_public_url(_string(item.get("url")), provider_id="newsapi"),
                metadata={"event_kind": "news"},
                news=NewsEvidenceDetails(
                    publisher=source_name,
                    article_id=f"newsapi_{target.ticker.lower()}_{index}",
                ),
            )
        )
    return candidates


def _naver_candidates(
    *,
    target: ExternalEvidenceTarget,
    items: Sequence[object],
) -> list[ExternalEvidenceCandidate]:
    candidates: list[ExternalEvidenceCandidate] = []
    for index, item in enumerate(items[:3]):
        if not isinstance(item, Mapping):
            continue
        title = _strip_html(_string(item.get("title")) or "")
        if not title:
            continue
        candidates.append(
            ExternalEvidenceCandidate(
                candidate_id=f"naver_{target.ticker.lower()}_{index}",
                provider_id="naver",
                category="news",
                ticker=target.ticker,
                source_label="Naver News Search",
                title=title,
                published_at=_naver_date_to_timestamp(_string(item.get("pubDate"))),
                event_type="news",
                summary=_strip_html(_string(item.get("description")) or title),
                safe_url=safe_public_url(
                    _string(item.get("originallink")) or _string(item.get("link")),
                    provider_id="naver",
                ),
                metadata={"event_kind": "news"},
                news=NewsEvidenceDetails(
                    publisher="Naver News Search",
                    article_id=f"naver_{target.ticker.lower()}_{index}",
                ),
            )
        )
    return candidates


def _string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _list_string(values: Sequence[object], index: int) -> str | None:
    if index >= len(values):
        return None
    return _string(values[index])


def _date_to_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00+00:00"
    return None


def _alpha_time_to_timestamp(value: str | None) -> str | None:
    if value is None or not re.fullmatch(r"\d{8}T\d{6}", value):
        return None
    parsed = datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    return parsed.isoformat()


def _naver_date_to_timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.strptime(value, "%a, %d %b %Y %H:%M:%S %z")
    except ValueError:
        return None
    return parsed.astimezone(UTC).isoformat()


def _timestamp_from_epoch(value: object) -> str | None:
    if not isinstance(value, int | float) or value <= 0:
        return None
    return datetime.fromtimestamp(float(value), tz=UTC).isoformat()


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)
