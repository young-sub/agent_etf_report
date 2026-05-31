from __future__ import annotations

import hashlib
import html
import importlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from agent_pack.models import JsonValue

from agent_treport.signal_report.adapters.errors import SignalReportInputError
from agent_treport.signal_report.adapters.operational_holdings import (
    HOLDINGS_HISTORY_MANIFEST_FILENAME,
    _history_snapshot_fingerprint,
    _parse_iso_date,
    _read_history_snapshot_rows,
    _validate_normalized_row,
    _write_history_snapshot_rows,
)
from agent_treport.signal_report.adapters.operational_universe import (
    UNIVERSE_STATE_FILENAME,
    UNIVERSE_STATE_SCHEMA_VERSION,
    UniverseBrandRecord,
    UniverseETFRecord,
    _brand_state_item,
    _etf_state_item,
    _next_state_brands,
    _next_state_etfs,
    _read_existing_state,
    load_active_universe_etfs,
)
from agent_treport.signal_report.domain.security_resolution import (
    SecurityClassificationPolicy,
)

FAKE_SOURCE_PROVIDER_SCHEMA_VERSION = "agent_treport.source_provider.fake.v1"
SOURCE_CATALOG_SCHEMA_VERSION = "agent_treport.source_catalog.v1"
ACTIVE_STRATEGY_SEED_SCHEMA_VERSION = "agent_treport.active_strategy_seed.v1"
SOURCE_ACQUISITION_SUMMARY_SCHEMA_VERSION = (
    "agent_treport.source_acquisition.summary.v1"
)
KOREA_STANDARD_TIME = timezone(timedelta(hours=9))
SOURCE_CATALOG_FILENAME = "source_catalog.json"
SOURCE_ACQUISITION_SUMMARY_FILENAME = "source_acquisition_summary.json"
SOURCE_RETRY_STATE_FILENAME = "source_retry_state.json"
SOURCE_RETRY_STATE_SCHEMA_VERSION = "agent_treport.source_acquisition.retry_state.v1"
DEFAULT_RETRY_COOLDOWN_SECONDS = 24 * 60 * 60
ACTIVE_STRATEGY_SEED_PATH = (
    Path(__file__).parents[2]
    / "fixtures"
    / "source_provider"
    / "active_strategy_seed.json"
)
KODEX_SOURCE_PROVIDER_ID = "kodex"
KODEX_BRAND_ID = "brand_samsung_asset_management"
KODEX_BRAND_NAME = "Samsung Asset Management"
KODEX_BASE_URL = "https://www.samsungfund.com"
KODEX_CASH_EXACT_CODES = {"KRD010010001", "010010", "USDZZ0000001"}
_PROVIDER_WEIGHT_MISSING_FIELD = "_provider_weight_percent_missing"
_WEIGHT_FIT_TOLERANCE_PERCENT_POINTS = 0.5
ACE_SOURCE_PROVIDER_ID = "ace"
ACE_BRAND_ID = "brand_korea_investment_asset_management"
ACE_BRAND_NAME = "Korea Investment Asset Management"
ACE_API_BASE_URL = "https://papi.aceetf.co.kr"
HYUNDAI_SOURCE_PROVIDER_ID = "hyundai"
HYUNDAI_BRAND_ID = "brand_hyundai_asset_management"
HYUNDAI_BRAND_NAME = "Hyundai Asset Management"
HYUNDAI_BASE_URL = "https://www.hyundaiam.com"
TIMEFOLIO_SOURCE_PROVIDER_ID = "timefolio"
TIMEFOLIO_BRAND_ID = "brand_timefolio_asset_management"
TIMEFOLIO_BRAND_NAME = "TIMEFOLIO Asset Management"
TIMEFOLIO_BASE_URL = "https://timeetf.co.kr"
TIGER_SOURCE_PROVIDER_ID = "tiger"
TIGER_BRAND_ID = "brand_mirae_asset_management"
TIGER_BRAND_NAME = "Mirae Asset Management"
TIGER_BASE_URL = "https://investments.miraeasset.com"
RISE_SOURCE_PROVIDER_ID = "rise"
RISE_BRAND_ID = "brand_kb_asset_management"
RISE_BRAND_NAME = "KB Asset Management"
RISE_BASE_URL = "https://www.riseetf.co.kr"
SOL_SOURCE_PROVIDER_ID = "sol"
SOL_BRAND_ID = "brand_shinhan_asset_management"
SOL_BRAND_NAME = "Shinhan Asset Management"
SOL_BASE_URL = "https://www.soletf.com"
LIVE_SOURCE_PROVIDER_IDS = (
    KODEX_SOURCE_PROVIDER_ID,
    ACE_SOURCE_PROVIDER_ID,
    HYUNDAI_SOURCE_PROVIDER_ID,
    TIMEFOLIO_SOURCE_PROVIDER_ID,
    TIGER_SOURCE_PROVIDER_ID,
    RISE_SOURCE_PROVIDER_ID,
    SOL_SOURCE_PROVIDER_ID,
)
_SECURITY_CLASSIFICATION_POLICY = SecurityClassificationPolicy()
_ACTIVE_STRATEGY_ACTIVE_MARKERS = ("ACTIVE", "액티브")
_ACTIVE_STRATEGY_PASSIVE_MARKERS = ("PASSIVE", "패시브")
_ACTIVE_STRATEGY_PASSIVE_KEYWORDS = (
    "BOND",
    "CORPORATE BOND",
    "CREDIT",
    "GOVERNMENT BOND",
    "TREASURY",
    "채권",
    "회사채",
    "국고채",
    "국채",
    "금융채",
    "특수채",
    "통안채",
    "크레딧",
    "CD금리",
    "REIT",
    "REITS",
    "MONEY MARKET",
    "MONEYMARKET",
    "머니마켓",
    "MMF",
    "INVERSE",
    "인버스",
    "LEVERAGE",
    "레버리지",
    "COVEREDCALL",
    "COVERED CALL",
    "커버드콜",
    "TDF",
    "TIF",
    "KOFR",
    "SOFR",
    "INDEX",
    "인덱스",
)
_ACTIVE_STRATEGY_SOURCES = {
    "unknown",
    "source_metadata",
    "passive_keyword",
    "timefolio_provider_default",
    "name_token",
    "reference_seed",
}
_ACTIVE_STRATEGY_CONFIDENCES = {"high", "medium", "low"}


class SourceAcquisitionInputError(SignalReportInputError):
    """Raised when SourceProvider acquisition input violates the domain contract."""


@dataclass(frozen=True)
class SourceCatalogEntry:
    source_provider_id: str
    provider_etf_id: str
    etf_id: str
    etf_name: str
    brand_id: str
    brand_name: str
    is_active_strategy_etf: bool | None = None
    active_strategy_source: str = "unknown"
    active_strategy_confidence: str = "low"
    strategy_label: str | None = None
    locator: str | None = None


@dataclass(frozen=True)
class SourceCatalogResult:
    source_provider_id: str
    complete: bool
    entries: tuple[SourceCatalogEntry, ...]


@dataclass(frozen=True)
class HoldingsFetchTarget:
    source_provider_id: str
    provider_etf_id: str
    etf_id: str
    requested_date: str
    provider_query_date: str


@dataclass(frozen=True)
class HoldingsFetchResult:
    source_provider_id: str
    provider_etf_id: str
    etf_id: str
    requested_date: str
    outcome: str
    provider_query_date: str | None = None
    observed_date: str | None = None
    holdings: tuple[Mapping[str, JsonValue], ...] = ()
    failure_code_class: str | None = None
    retry_attempt_count: int = 0


class SourceProvider(Protocol):
    source_provider_id: str

    def fetch_catalog(self) -> SourceCatalogResult:
        """Return the full source catalog exposed by this provider."""
        ...

    def fetch_holdings(self, target: HoldingsFetchTarget) -> HoldingsFetchResult:
        """Return holdings for one provider ETF target and requested business date."""
        ...


class FakeSourceProvider:
    def __init__(
        self,
        *,
        source_provider_id: str,
        catalog: SourceCatalogResult,
        holdings_results: Mapping[tuple[str, str], HoldingsFetchResult] | None = None,
    ) -> None:
        self.source_provider_id = source_provider_id
        self._catalog = catalog
        self._holdings_results = dict(holdings_results or {})

    @classmethod
    def from_fixture_path(cls, fixture_path: str | Path) -> FakeSourceProvider:
        fixture = _read_json_object(Path(fixture_path), label="fake source provider fixture")
        if fixture.get("schema_version") != FAKE_SOURCE_PROVIDER_SCHEMA_VERSION:
            raise SourceAcquisitionInputError("invalid fake source provider fixture schema")
        source_provider_id = _required_safe_text(
            fixture.get("source_provider_id"),
            "source_provider_id",
        )
        catalog = _fake_catalog_result(
            fixture.get("catalog"),
            source_provider_id=source_provider_id,
        )
        holdings_results = _fake_holdings_results(
            fixture.get("holdings_results", []),
            source_provider_id=source_provider_id,
        )
        return cls(
            source_provider_id=source_provider_id,
            catalog=catalog,
            holdings_results=holdings_results,
        )

    def fetch_catalog(self) -> SourceCatalogResult:
        return self._catalog

    def fetch_holdings(self, target: HoldingsFetchTarget) -> HoldingsFetchResult:
        result = self._holdings_results.get(
            (target.provider_etf_id, target.requested_date)
        )
        if result is None:
            return HoldingsFetchResult(
                source_provider_id=target.source_provider_id,
                provider_etf_id=target.provider_etf_id,
                etf_id=target.etf_id,
                requested_date=target.requested_date,
                outcome="unsupported",
                failure_code_class="unsupported_target",
            )
        return result


class _SourceProviderRateLimited(RuntimeError):
    pass


class _SourceProviderBlocked(_SourceProviderRateLimited):
    pass


class _SourceProviderUnavailable(RuntimeError):
    pass


class _SourceProviderRequestFailed(RuntimeError):
    pass


class KodexSourceProvider:
    source_provider_id = KODEX_SOURCE_PROVIDER_ID

    def __init__(self, *, session: object | None = None) -> None:
        if session is None:
            requests = importlib.import_module("requests")
            session = requests.Session()
        self._session = session
        self._blocked_hosts: dict[str, str] = {}

    def fetch_catalog(self) -> SourceCatalogResult:
        entries: list[SourceCatalogEntry] = []
        page = 1
        page_size = 200
        total_count: int | None = None
        while True:
            payload = _http_get_json(
                self._session,
                f"{KODEX_BASE_URL}/api/v1/kodex/product.do",
                params={
                    "ordrColm": "byDwm03Rt",
                    "ordrType": "DESC",
                    "pageNo": str(page),
                    "pageSize": str(page_size),
                },
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
            if not isinstance(payload, list) or not payload:
                break
            for row in payload:
                if not isinstance(row, Mapping):
                    continue
                entry = _kodex_catalog_entry(row)
                if entry is not None:
                    entries.append(entry)
                if total_count is None:
                    total_count = _int_or_none(row.get("totalCnt"))
            if total_count is not None and len(entries) >= total_count:
                break
            page += 1
            if page > 20:
                break
        if not entries:
            raise SourceAcquisitionInputError("KODEX source catalog returned no ETFs")
        return SourceCatalogResult(
            source_provider_id=self.source_provider_id,
            complete=True,
            entries=tuple(entries),
        )

    def fetch_holdings(self, target: HoldingsFetchTarget) -> HoldingsFetchResult:
        try:
            detail_payload = _http_get_json(
                self._session,
                f"{KODEX_BASE_URL}/api/v1/kodex/product/{target.provider_etf_id}.do",
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
            query_dotted = target.provider_query_date.replace("-", ".")
            pdf_payload = _http_get_json(
                self._session,
                (
                    f"{KODEX_BASE_URL}/api/v1/kodex/product-pdf/"
                    f"{target.provider_etf_id}.do"
                ),
                params={"gijunYMD": query_dotted},
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
        except _SourceProviderBlocked:
            return _kodex_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="blocked",
            )
        except _SourceProviderRateLimited:
            return _kodex_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="rate_limited",
            )
        except _SourceProviderUnavailable:
            return _kodex_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_unavailable",
            )
        except _SourceProviderRequestFailed:
            return _kodex_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_response",
            )

        try:
            observed_date, rows = _kodex_holdings_rows(
                detail_payload=detail_payload,
                pdf_payload=pdf_payload,
                target=target,
            )
        except SourceAcquisitionInputError:
            return _kodex_failed_result(
                target,
                outcome="failed",
                failure_code_class="invalid_provider_payload",
            )
        return HoldingsFetchResult(
            source_provider_id=target.source_provider_id,
            provider_etf_id=target.provider_etf_id,
            etf_id=target.etf_id,
            requested_date=target.requested_date,
            provider_query_date=target.provider_query_date,
            observed_date=observed_date,
            outcome="fetched",
            holdings=tuple(rows),
        )


class AceSourceProvider:
    source_provider_id = ACE_SOURCE_PROVIDER_ID

    def __init__(self, *, session: object | None = None) -> None:
        if session is None:
            requests = importlib.import_module("requests")
            session = requests.Session()
        self._session = session
        self._blocked_hosts: dict[str, str] = {}

    def fetch_catalog(self) -> SourceCatalogResult:
        entries: list[SourceCatalogEntry] = []
        page = 1
        size = 50
        while True:
            payload = _http_get_json(
                self._session,
                f"{ACE_API_BASE_URL}/api/funds",
                params={"page": str(page), "size": str(size)},
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
            rows = payload.get("data", []) if isinstance(payload, Mapping) else []
            if not isinstance(rows, list) or not rows:
                break
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                entry = _ace_catalog_entry(row)
                if entry is not None:
                    entries.append(entry)
            if len(rows) < size:
                break
            page += 1
            if page > 20:
                break
        if not entries:
            raise SourceAcquisitionInputError("ACE source catalog returned no ETFs")
        return SourceCatalogResult(
            source_provider_id=self.source_provider_id,
            complete=True,
            entries=tuple(entries),
        )

    def fetch_holdings(self, target: HoldingsFetchTarget) -> HoldingsFetchResult:
        query_ymd = target.provider_query_date.replace("-", "")
        try:
            _http_get_json(
                self._session,
                f"{ACE_API_BASE_URL}/api/funds/{target.provider_etf_id}",
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
            _http_get_json(
                self._session,
                f"{ACE_API_BASE_URL}/api/funds/{target.provider_etf_id}/product",
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
            pdf_payload = _http_get_json(
                self._session,
                (
                    f"{ACE_API_BASE_URL}/api/funds/{target.provider_etf_id}/pdf"
                    f"?page=1&size=1000&std_dt={query_ymd}"
                ),
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
        except _SourceProviderBlocked:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="blocked",
            )
        except _SourceProviderRateLimited:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="rate_limited",
            )
        except _SourceProviderUnavailable:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_unavailable",
            )
        except _SourceProviderRequestFailed:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_response",
            )

        try:
            observed_date, rows = _ace_holdings_rows(
                pdf_payload=pdf_payload,
                target=target,
            )
        except SourceAcquisitionInputError:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="invalid_provider_payload",
            )
        return HoldingsFetchResult(
            source_provider_id=target.source_provider_id,
            provider_etf_id=target.provider_etf_id,
            etf_id=target.etf_id,
            requested_date=target.requested_date,
            provider_query_date=target.provider_query_date,
            observed_date=observed_date,
            outcome="fetched",
            holdings=tuple(rows),
        )


class HyundaiSourceProvider:
    source_provider_id = HYUNDAI_SOURCE_PROVIDER_ID

    def __init__(self, *, session: object | None = None) -> None:
        if session is None:
            requests = importlib.import_module("requests")
            session = requests.Session()
        self._session = session
        self._blocked_hosts: dict[str, str] = {}

    def fetch_catalog(self) -> SourceCatalogResult:
        payload = _http_get_json(
            self._session,
            f"{HYUNDAI_BASE_URL}/api/etfList",
            timeout=20,
            blocked_hosts=self._blocked_hosts,
        )
        if not isinstance(payload, list):
            raise SourceAcquisitionInputError("HYUNDAI source catalog returned invalid payload")
        entries = [
            entry
            for row in payload
            if isinstance(row, Mapping)
            for entry in (_hyundai_catalog_entry(row),)
            if entry is not None
        ]
        if not entries:
            raise SourceAcquisitionInputError("HYUNDAI source catalog returned no ETFs")
        return SourceCatalogResult(
            source_provider_id=self.source_provider_id,
            complete=True,
            entries=tuple(entries),
        )

    def fetch_holdings(self, target: HoldingsFetchTarget) -> HoldingsFetchResult:
        query_ymd = target.provider_query_date.replace("-", "")
        try:
            info_payload = _http_get_json(
                self._session,
                f"{HYUNDAI_BASE_URL}/api/funds/etf/{target.provider_etf_id}",
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
            fund_code, etf_code = _hyundai_pdf_codes(info_payload)
            pdf_payload = _http_get_json(
                self._session,
                (
                    f"{HYUNDAI_BASE_URL}/api/etfPdf"
                    f"?fundCode={fund_code}&etfCode={etf_code}&ymd={query_ymd}"
                ),
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
        except _SourceProviderBlocked:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="blocked",
            )
        except _SourceProviderRateLimited:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="rate_limited",
            )
        except _SourceProviderUnavailable:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_unavailable",
            )
        except _SourceProviderRequestFailed:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_response",
            )
        except SourceAcquisitionInputError:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="invalid_provider_payload",
            )

        try:
            observed_date, rows = _hyundai_holdings_rows(
                pdf_payload=pdf_payload,
                target=target,
            )
        except SourceAcquisitionInputError:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="invalid_provider_payload",
            )
        return HoldingsFetchResult(
            source_provider_id=target.source_provider_id,
            provider_etf_id=target.provider_etf_id,
            etf_id=target.etf_id,
            requested_date=target.requested_date,
            provider_query_date=target.provider_query_date,
            observed_date=observed_date,
            outcome="fetched",
            holdings=tuple(rows),
        )


class TimefolioSourceProvider:
    source_provider_id = TIMEFOLIO_SOURCE_PROVIDER_ID

    def __init__(self, *, session: object | None = None) -> None:
        if session is None:
            requests = importlib.import_module("requests")
            session = requests.Session()
        self._session = session
        self._blocked_hosts: dict[str, str] = {}

    def fetch_catalog(self) -> SourceCatalogResult:
        html_text = _http_get_text(
            self._session,
            f"{TIMEFOLIO_BASE_URL}/m11.php",
            timeout=20,
            blocked_hosts=self._blocked_hosts,
        )
        entries = tuple(_timefolio_catalog_entries(html_text))
        if not entries:
            raise SourceAcquisitionInputError("TIMEFOLIO source catalog returned no ETFs")
        return SourceCatalogResult(
            source_provider_id=self.source_provider_id,
            complete=True,
            entries=entries,
        )

    def fetch_holdings(self, target: HoldingsFetchTarget) -> HoldingsFetchResult:
        try:
            detail_html = _http_get_text(
                self._session,
                (
                    f"{TIMEFOLIO_BASE_URL}/m11_view.php"
                    f"?idx={target.provider_etf_id}&pdfDate={target.provider_query_date}"
                ),
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
        except _SourceProviderBlocked:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="blocked",
            )
        except _SourceProviderRateLimited:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="rate_limited",
            )
        except _SourceProviderUnavailable:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_unavailable",
            )
        except _SourceProviderRequestFailed:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_response",
            )

        try:
            observed_date, rows = _timefolio_holdings_rows(
                detail_html=detail_html,
                target=target,
            )
        except SourceAcquisitionInputError:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="invalid_provider_payload",
            )
        return HoldingsFetchResult(
            source_provider_id=target.source_provider_id,
            provider_etf_id=target.provider_etf_id,
            etf_id=target.etf_id,
            requested_date=target.requested_date,
            provider_query_date=target.provider_query_date,
            observed_date=observed_date,
            outcome="fetched",
            holdings=tuple(rows),
        )


class TigerSourceProvider:
    source_provider_id = TIGER_SOURCE_PROVIDER_ID

    def __init__(self, *, session: object | None = None) -> None:
        if session is None:
            requests = importlib.import_module("requests")
            session = requests.Session()
        self._session = session
        self._blocked_hosts: dict[str, str] = {}

    def fetch_catalog(self) -> SourceCatalogResult:
        entries: list[SourceCatalogEntry] = []
        page = 1
        list_count = 200
        total_count: int | None = None
        while True:
            html_text = _http_get_text(
                self._session,
                (
                    f"{TIGER_BASE_URL}/tigeretf/ko/product/search/list.ajax"
                    f"?pdfNameYn=N&pageIndex={page}&listCnt={list_count}"
                    "&periodType=short&listType=table&etfTemaCode=&cateNameYn=N"
                    "&inCateNationNot=&inCateFundNot=&q=&prfPrd=1w"
                    "&orderA=Month03&orderB=descending"
                ),
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
            page_entries, page_total = _tiger_catalog_entries(html_text)
            if page_total is not None and total_count is None:
                total_count = page_total
            if not page_entries:
                break
            entries.extend(page_entries)
            if total_count is not None and len(entries) >= total_count:
                break
            page += 1
            if page > 30:
                break
        if not entries:
            raise SourceAcquisitionInputError("TIGER source catalog returned no ETFs")
        return SourceCatalogResult(
            source_provider_id=self.source_provider_id,
            complete=True,
            entries=tuple(entries),
        )

    def fetch_holdings(self, target: HoldingsFetchTarget) -> HoldingsFetchResult:
        query_ymd = target.provider_query_date.replace("-", "")
        try:
            payload = _http_post_json(
                self._session,
                (
                    f"{TIGER_BASE_URL}/tigeretf/ko/product/chart/prdct-item-list.ajax"
                    f"?ksdFund={target.provider_etf_id}&prfPrd=Week01"
                    f"&fixDate={query_ymd}&listCnt=200"
                ),
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
        except _SourceProviderBlocked:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="blocked",
            )
        except _SourceProviderRateLimited:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="rate_limited",
            )
        except _SourceProviderUnavailable:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_unavailable",
            )
        except _SourceProviderRequestFailed:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_response",
            )

        try:
            observed_date, rows = _tiger_holdings_rows(
                payload=payload,
                target=target,
            )
        except SourceAcquisitionInputError:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="invalid_provider_payload",
            )
        return HoldingsFetchResult(
            source_provider_id=target.source_provider_id,
            provider_etf_id=target.provider_etf_id,
            etf_id=target.etf_id,
            requested_date=target.requested_date,
            provider_query_date=target.provider_query_date,
            observed_date=observed_date,
            outcome="fetched",
            holdings=tuple(rows),
        )


class RiseSourceProvider:
    source_provider_id = RISE_SOURCE_PROVIDER_ID

    def __init__(self, *, session: object | None = None) -> None:
        if session is None:
            requests = importlib.import_module("requests")
            session = requests.Session()
        self._session = session
        self._blocked_hosts: dict[str, str] = {}

    def fetch_catalog(self) -> SourceCatalogResult:
        html_text = _http_get_text(
            self._session,
            f"{RISE_BASE_URL}/prod/finder",
            timeout=20,
            blocked_hosts=self._blocked_hosts,
        )
        entries = tuple(_rise_catalog_entries(html_text))
        if not entries:
            raise SourceAcquisitionInputError("RISE source catalog returned no ETFs")
        return SourceCatalogResult(
            source_provider_id=self.source_provider_id,
            complete=True,
            entries=entries,
        )

    def fetch_holdings(self, target: HoldingsFetchTarget) -> HoldingsFetchResult:
        query_date = target.provider_query_date
        try:
            _http_get_text(
                self._session,
                (
                    f"{RISE_BASE_URL}/prod/finderDetail/{target.provider_etf_id}"
                    "?searchFlag=viewtab3"
                ),
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
            holdings_html = _http_post_text(
                self._session,
                f"{RISE_BASE_URL}/prod/finder/productViewSearchTabJquery3",
                data={"fundCd": target.provider_etf_id, "searchDate": query_date},
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
        except _SourceProviderBlocked:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="blocked",
            )
        except _SourceProviderRateLimited:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="rate_limited",
            )
        except _SourceProviderUnavailable:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_unavailable",
            )
        except _SourceProviderRequestFailed:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_response",
            )

        try:
            observed_date, rows = _rise_holdings_rows(
                holdings_html=holdings_html,
                target=target,
            )
        except SourceAcquisitionInputError:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="invalid_provider_payload",
            )
        return HoldingsFetchResult(
            source_provider_id=target.source_provider_id,
            provider_etf_id=target.provider_etf_id,
            etf_id=target.etf_id,
            requested_date=target.requested_date,
            provider_query_date=target.provider_query_date,
            observed_date=observed_date,
            outcome="fetched",
            holdings=tuple(rows),
        )


class SolSourceProvider:
    source_provider_id = SOL_SOURCE_PROVIDER_ID

    def __init__(self, *, session: object | None = None) -> None:
        if session is None:
            requests = importlib.import_module("requests")
            session = requests.Session()
        self._session = session
        self._blocked_hosts: dict[str, str] = {}

    def fetch_catalog(self) -> SourceCatalogResult:
        entries_by_id: dict[str, SourceCatalogEntry] = {}
        html_text = _http_get_text(
            self._session,
            f"{SOL_BASE_URL}/ko/fund",
            timeout=20,
            blocked_hosts=self._blocked_hosts,
        )
        for entry in _sol_catalog_entries_from_html(html_text):
            entries_by_id[entry.provider_etf_id] = entry

        page = 1
        while True:
            payload = _http_post_json(
                self._session,
                f"{SOL_BASE_URL}/api/common/searchByEtfNameOrFilter",
                data={"viewCount": "100", "nowPage": str(page)},
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
            page_entries = _sol_catalog_entries_from_payload(payload)
            for entry in page_entries:
                entries_by_id[entry.provider_etf_id] = entry
            if not page_entries:
                break
            total_pages = 1
            if isinstance(payload, Mapping):
                total_pages = _int_or_none(payload.get("toalPage")) or 1
            if page >= total_pages:
                break
            page += 1
            if page > 20:
                break

        entries = tuple(entries_by_id.values())
        if not entries:
            raise SourceAcquisitionInputError("SOL source catalog returned no ETFs")
        return SourceCatalogResult(
            source_provider_id=self.source_provider_id,
            complete=True,
            entries=entries,
        )

    def fetch_holdings(self, target: HoldingsFetchTarget) -> HoldingsFetchResult:
        query_ymd = target.provider_query_date.replace("-", "")
        try:
            _http_get_text(
                self._session,
                f"{SOL_BASE_URL}/ko/fund/etf/{target.provider_etf_id}?tabIndex=3",
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
            _http_get_text(
                self._session,
                f"{SOL_BASE_URL}/ko/fund/etf/{target.provider_etf_id}?tabIndex=1",
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
            payload = _http_get_json(
                self._session,
                f"{SOL_BASE_URL}/api/fund/pdfList",
                params={"fund_cd": target.provider_etf_id, "work_dt": query_ymd},
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": (
                        f"{SOL_BASE_URL}/ko/fund/etf/{target.provider_etf_id}"
                        "?tabIndex=3"
                    ),
                    "Origin": SOL_BASE_URL,
                },
                timeout=20,
                blocked_hosts=self._blocked_hosts,
            )
        except _SourceProviderBlocked:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="blocked",
            )
        except _SourceProviderRateLimited:
            return _source_failed_result(
                target,
                outcome="rate_limited",
                failure_code_class="rate_limited",
            )
        except _SourceProviderUnavailable:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_unavailable",
            )
        except _SourceProviderRequestFailed:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="provider_response",
            )

        try:
            observed_date, rows = _sol_holdings_rows(payload=payload, target=target)
        except SourceAcquisitionInputError:
            return _source_failed_result(
                target,
                outcome="failed",
                failure_code_class="invalid_provider_payload",
            )
        return HoldingsFetchResult(
            source_provider_id=target.source_provider_id,
            provider_etf_id=target.provider_etf_id,
            etf_id=target.etf_id,
            requested_date=target.requested_date,
            provider_query_date=target.provider_query_date,
            observed_date=observed_date,
            outcome="fetched",
            holdings=tuple(rows),
        )


def create_live_source_provider(
    source_provider_id: str,
    *,
    session: object | None = None,
) -> SourceProvider:
    if source_provider_id == KODEX_SOURCE_PROVIDER_ID:
        return KodexSourceProvider(session=session)
    if source_provider_id == ACE_SOURCE_PROVIDER_ID:
        return AceSourceProvider(session=session)
    if source_provider_id == HYUNDAI_SOURCE_PROVIDER_ID:
        return HyundaiSourceProvider(session=session)
    if source_provider_id == TIMEFOLIO_SOURCE_PROVIDER_ID:
        return TimefolioSourceProvider(session=session)
    if source_provider_id == TIGER_SOURCE_PROVIDER_ID:
        return TigerSourceProvider(session=session)
    if source_provider_id == RISE_SOURCE_PROVIDER_ID:
        return RiseSourceProvider(session=session)
    if source_provider_id == SOL_SOURCE_PROVIDER_ID:
        return SolSourceProvider(session=session)
    raise SourceAcquisitionInputError(
        f"unsupported live source provider: {source_provider_id}"
    )


def collect_source_catalog(
    *,
    provider: SourceProvider,
    dest_dir: str | Path,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    catalog = provider.fetch_catalog()
    entries = _validate_complete_catalog(
        catalog,
        expected_source_provider_id=provider.source_provider_id,
    )
    collected_at = _timestamp(now)
    destination = Path(dest_dir)
    state_path = destination / UNIVERSE_STATE_FILENAME
    previous_etfs, previous_brands = _read_existing_state(state_path)
    active_strategy_entries = tuple(
        entry for entry in entries if entry.is_active_strategy_etf is True
    )
    current_etfs = {
        entry.etf_id: UniverseETFRecord(
            etf_id=entry.etf_id,
            etf_name=entry.etf_name,
            brand_id=entry.brand_id,
            source_provider_id=entry.source_provider_id,
        )
        for entry in active_strategy_entries
    }
    current_brands = {
        entry.brand_id: UniverseBrandRecord(
            brand_id=entry.brand_id,
            brand_name=entry.brand_name,
            source_provider_id=entry.source_provider_id,
        )
        for entry in active_strategy_entries
    }
    state_etfs = _next_state_etfs(current_etfs, previous_etfs=previous_etfs)
    state_brands = _next_state_brands(
        current_brands,
        active_brand_ids={entry.brand_id for entry in active_strategy_entries},
        previous_brands=previous_brands,
    )
    source_catalog = _source_catalog_document(
        source_provider_id=catalog.source_provider_id,
        complete=catalog.complete,
        entries=entries,
        collected_at=collected_at,
    )
    summary = _source_catalog_summary(
        source_provider_id=catalog.source_provider_id,
        entries=entries,
        collected_at=collected_at,
    )
    state = _source_provider_state_document(
        updated_at=collected_at,
        etfs=state_etfs,
        brands=state_brands,
    )

    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / SOURCE_CATALOG_FILENAME, source_catalog)
    _write_json(destination / SOURCE_ACQUISITION_SUMMARY_FILENAME, summary)
    _write_json(state_path, state)
    return summary


def update_holdings_history_source(
    *,
    provider: SourceProvider,
    source_catalog_path: str | Path,
    universe_state_path: str | Path,
    history_dir: str | Path,
    requested_date: str | None = None,
    provider_etf_ids: set[str] | None = None,
    refresh_snapshots: set[tuple[str, str]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    requested_date = requested_date or _previous_weekday_from_execution_date(now)
    _parse_iso_date(requested_date)
    catalog_entries = _read_source_catalog_entries(
        Path(source_catalog_path),
        expected_source_provider_id=provider.source_provider_id,
    )
    active_etfs = load_active_universe_etfs(universe_state_path)
    targets = _source_holdings_targets(
        catalog_entries=catalog_entries,
        active_etf_ids=set(active_etfs),
        requested_date=requested_date,
        provider_etf_ids=provider_etf_ids,
    )
    if not targets:
        raise SourceAcquisitionInputError("source catalog has no active holdings targets")

    history_path = Path(history_dir) / HOLDINGS_HISTORY_MANIFEST_FILENAME
    stored = _read_history_snapshot_rows(history_path)
    next_stored = {key: [dict(row) for row in rows] for key, rows in stored.items()}
    refresh_set = set(refresh_snapshots or set())
    run_started_at = _timestamp(now)
    run_started_time = _parse_timestamp(run_started_at)
    retry_state_path = Path(history_dir) / SOURCE_RETRY_STATE_FILENAME
    retry_state = _read_retry_state(retry_state_path)
    next_retry_state = dict(retry_state)
    target_outcomes: list[dict[str, JsonValue]] = []
    observed_dates: set[str] = set()
    selected_snapshot_keys: set[tuple[str, str]] = set()
    written_row_count = 0
    written_snapshot_count = 0
    catalog_by_target = {
        (entry.source_provider_id, entry.provider_etf_id): entry
        for entry in catalog_entries
    }
    bounded_candidate_smoke = len(provider_etf_ids or set()) == 1

    for target in targets:
        entry = catalog_by_target[(target.source_provider_id, target.provider_etf_id)]
        retry_plan = _target_retry_plan(
            target=target,
            stored=stored,
            retry_state=retry_state,
            now=run_started_time,
        )
        if retry_plan.get("cooldown_active") is True:
            target_outcomes.append(
                _cooldown_target_outcome_item(
                    target=target,
                    entry=entry,
                    retry_plan=retry_plan,
                )
            )
            continue
        existing_rows = _existing_exact_snapshot_rows(
            target=target,
            stored=stored,
            refresh_set=refresh_set,
        )
        if existing_rows is not None:
            observed_date = target.provider_query_date
            selected_snapshot_keys.add((target.etf_id, observed_date))
            observed_dates.add(observed_date)
            target_outcomes.append(
                _target_outcome_item(
                    target=target,
                    entry=entry,
                    result=HoldingsFetchResult(
                        source_provider_id=target.source_provider_id,
                        provider_etf_id=target.provider_etf_id,
                        etf_id=target.etf_id,
                        requested_date=target.requested_date,
                        provider_query_date=target.provider_query_date,
                        observed_date=observed_date,
                        outcome="skipped_existing",
                    ),
                    outcome="skipped_existing",
                    observed_date=observed_date,
                    row_count=len(existing_rows),
                    failure_code_class=None,
                    retry_plan=retry_plan,
                )
            )
            next_retry_state.pop(_retry_state_key(target), None)
            if bounded_candidate_smoke and _target_latest_smoke_succeeded(
                target_outcomes[-1]
            ):
                break
            continue
        result = provider.fetch_holdings(target)
        outcome, rows, failure_code_class = _apply_source_fetch_result(
            result=result,
            target=target,
            entry=entry,
            stored=stored,
            next_stored=next_stored,
            refresh_set=refresh_set,
        )
        row_count = len(rows)
        if rows:
            observed_date = str(rows[0]["as_of_date"])
            selected_snapshot_keys.add((target.etf_id, observed_date))
            observed_dates.add(observed_date)
            if outcome in {"fetched", "refreshed"}:
                written_row_count += row_count
                written_snapshot_count += 1
        else:
            observed_date = result.observed_date
            if observed_date is not None:
                observed_dates.add(observed_date)
        target_outcomes.append(
            _target_outcome_item(
                target=target,
                entry=entry,
                result=result,
                outcome="fetched" if outcome == "refreshed" else outcome,
                observed_date=observed_date,
                row_count=row_count,
                failure_code_class=failure_code_class,
                retry_plan=retry_plan,
            )
        )
        if failure_code_class is None and outcome in {"fetched", "skipped_existing"}:
            next_retry_state.pop(_retry_state_key(target), None)
        elif (
            failure_code_class != "provider_unavailable"
            and (
                failure_code_class is not None
                or outcome in {"failed", "rate_limited", "unsupported"}
            )
        ):
            cooldown = _cooldown_retry_state_item(
                target=target,
                reason_code=failure_code_class or outcome,
                now=run_started_time,
                retry_plan=retry_plan,
            )
            next_retry_state[_retry_state_key(target)] = cooldown
            target_outcomes[-1].update(
                _retry_plan_projection(cooldown=cooldown, retry_plan=retry_plan)
            )
        if bounded_candidate_smoke and _target_latest_smoke_succeeded(
            target_outcomes[-1]
        ):
            break

    unselected_refreshes = sorted(
        refresh_set - selected_snapshot_keys,
        key=lambda item: (_parse_iso_date(item[1]), item[0]),
        reverse=True,
    )
    if unselected_refreshes:
        raise SourceAcquisitionInputError(
            "refresh snapshot was not selected: "
            + "; ".join(
                f"etf_id={etf_id} observed_date={observed_date}"
                for etf_id, observed_date in unselected_refreshes
            )
        )

    updated_at = run_started_at
    if written_snapshot_count:
        _write_history_snapshot_rows(
            history_path=history_path,
            snapshot_rows=next_stored,
            updated_at=updated_at,
        )
    if next_retry_state != retry_state:
        _write_retry_state(
            retry_state_path,
            updated_at=updated_at,
            cooldowns=next_retry_state,
        )

    summary = _source_holdings_summary(
        source_provider_id=provider.source_provider_id,
        updated_at=updated_at,
        requested_date=requested_date,
        observed_dates=observed_dates,
        target_outcomes=target_outcomes,
        written_row_count=written_row_count,
        written_snapshot_count=written_snapshot_count,
    )
    history_destination = Path(history_dir)
    history_destination.mkdir(parents=True, exist_ok=True)
    _write_source_acquisition_summary(history_destination, summary)
    return summary


def _fake_catalog_result(
    value: JsonValue,
    *,
    source_provider_id: str,
) -> SourceCatalogResult:
    if not isinstance(value, Mapping):
        raise SourceAcquisitionInputError("fake source provider catalog must be an object")
    complete = value.get("complete")
    if not isinstance(complete, bool):
        raise SourceAcquisitionInputError("fake source provider catalog complete must be boolean")
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list):
        raise SourceAcquisitionInputError("fake source provider catalog entries must be a list")
    return SourceCatalogResult(
        source_provider_id=source_provider_id,
        complete=complete,
        entries=tuple(_fake_catalog_entry(item) for item in raw_entries),
    )


def _fake_catalog_entry(value: object) -> SourceCatalogEntry:
    if not isinstance(value, Mapping):
        raise SourceAcquisitionInputError("fake source catalog entry must be an object")
    return SourceCatalogEntry(
        source_provider_id=_required_safe_text(
            value.get("source_provider_id"),
            "catalog.entry.source_provider_id",
        ),
        provider_etf_id=_required_safe_text(
            value.get("provider_etf_id"),
            "catalog.entry.provider_etf_id",
        ),
        etf_id=_required_safe_text(value.get("etf_id"), "catalog.entry.etf_id"),
        etf_name=_required_safe_text(value.get("etf_name"), "catalog.entry.etf_name"),
        brand_id=_required_safe_text(value.get("brand_id"), "catalog.entry.brand_id"),
        brand_name=_required_safe_text(value.get("brand_name"), "catalog.entry.brand_name"),
        is_active_strategy_etf=_optional_bool(
            value.get("is_active_strategy_etf"),
            "catalog.entry.is_active_strategy_etf",
        ),
        active_strategy_source=_optional_safe_text(
            value.get("active_strategy_source"),
            "catalog.entry.active_strategy_source",
        )
        or "unknown",
        active_strategy_confidence=_optional_safe_text(
            value.get("active_strategy_confidence"),
            "catalog.entry.active_strategy_confidence",
        )
        or "low",
        strategy_label=_optional_safe_text(
            value.get("strategy_label"),
            "catalog.entry.strategy_label",
        ),
        locator=_optional_text(value.get("locator"), "catalog.entry.locator"),
    )


def _fake_holdings_results(
    value: JsonValue,
    *,
    source_provider_id: str,
) -> dict[tuple[str, str], HoldingsFetchResult]:
    if not isinstance(value, list):
        raise SourceAcquisitionInputError("fake source provider holdings_results must be a list")
    results: dict[tuple[str, str], HoldingsFetchResult] = {}
    for item in value:
        result = _fake_holdings_result(item, source_provider_id=source_provider_id)
        key = (result.provider_etf_id, result.requested_date)
        if key in results:
            raise SourceAcquisitionInputError(
                "duplicate fake holdings result: "
                f"provider_etf_id={key[0]} requested_date={key[1]}"
            )
        results[key] = result
    return results


def _fake_holdings_result(
    value: object,
    *,
    source_provider_id: str,
) -> HoldingsFetchResult:
    if not isinstance(value, Mapping):
        raise SourceAcquisitionInputError("fake holdings result must be an object")
    result_provider_id = _required_safe_text(
        value.get("source_provider_id"),
        "holdings_result.source_provider_id",
    )
    if result_provider_id != source_provider_id:
        raise SourceAcquisitionInputError("fake holdings result provider id mismatch")
    outcome = _target_outcome(value.get("outcome"))
    requested_date = _required_safe_text(
        value.get("requested_date"),
        "holdings_result.requested_date",
    )
    _parse_iso_date(requested_date)
    observed_date = _optional_safe_text(
        value.get("observed_date"),
        "holdings_result.observed_date",
    )
    if observed_date is not None:
        _parse_iso_date(observed_date)
    provider_query_date = _optional_safe_text(
        value.get("provider_query_date"),
        "holdings_result.provider_query_date",
    )
    if provider_query_date is not None:
        _parse_iso_date(provider_query_date)
    return HoldingsFetchResult(
        source_provider_id=result_provider_id,
        provider_etf_id=_required_safe_text(
            value.get("provider_etf_id"),
            "holdings_result.provider_etf_id",
        ),
        etf_id=_optional_safe_text(value.get("etf_id"), "holdings_result.etf_id") or "",
        requested_date=requested_date,
        outcome=outcome,
        provider_query_date=provider_query_date,
        observed_date=observed_date,
        holdings=tuple(_fake_holding(row) for row in _holdings_list(value.get("holdings"))),
        failure_code_class=_optional_safe_text(
            value.get("failure_code_class"),
            "holdings_result.failure_code_class",
        ),
        retry_attempt_count=_retry_attempt_count(value.get("retry_attempt_count")),
    )


def _holdings_list(value: JsonValue) -> list[JsonValue]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SourceAcquisitionInputError("fake holdings result holdings must be a list")
    return value


def _fake_holding(value: JsonValue) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise SourceAcquisitionInputError("fake holdings row must be an object")
    return dict(value)


def _http_get_json(
    session: object,
    url: str,
    *,
    params: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: int,
    blocked_hosts: dict[str, str] | None = None,
) -> object:
    host = (urlparse(url).hostname or "").lower()
    if blocked_hosts is not None and host in blocked_hosts:
        if blocked_hosts[host] == "blocked":
            raise _SourceProviderBlocked("source provider host is blocked")
        raise _SourceProviderRateLimited("source provider host is rate limited")
    try:
        response = session.get(url, params=params, headers=headers, timeout=timeout)  # type: ignore[attr-defined]
    except Exception as exc:
        if _looks_like_timeout(exc):
            raise _SourceProviderRateLimited("source provider request timed out") from exc
        raise _SourceProviderUnavailable("source provider request unavailable") from exc
    status_code = int(getattr(response, "status_code", 0) or 0)
    response_text = str(getattr(response, "text", "") or "")
    if status_code == 403:
        if blocked_hosts is not None and host:
            blocked_hosts[host] = "blocked"
        raise _SourceProviderBlocked("source provider blocked request")
    if status_code == 429 or _looks_like_rate_limit_text(response_text):
        if blocked_hosts is not None and host:
            blocked_hosts[host] = "rate_limited"
        raise _SourceProviderRateLimited("source provider rate limited")
    if status_code < 200 or status_code >= 300:
        raise _SourceProviderRequestFailed(
            f"source provider request failed with status {status_code}"
        )
    try:
        return response.json()  # type: ignore[attr-defined]
    except Exception:
        try:
            content = getattr(response, "content", b"")
            if isinstance(content, bytes):
                return json.loads(content.decode("utf-8-sig"))
            return json.loads(str(content))
        except Exception as exc:
            raise _SourceProviderRequestFailed("source provider JSON response invalid") from exc


def _http_get_text(
    session: object,
    url: str,
    *,
    timeout: int,
    blocked_hosts: dict[str, str] | None = None,
) -> str:
    host = (urlparse(url).hostname or "").lower()
    if blocked_hosts is not None and host in blocked_hosts:
        if blocked_hosts[host] == "blocked":
            raise _SourceProviderBlocked("source provider host is blocked")
        raise _SourceProviderRateLimited("source provider host is rate limited")
    try:
        response = session.get(url, timeout=timeout)  # type: ignore[attr-defined]
    except Exception as exc:
        if _looks_like_timeout(exc):
            raise _SourceProviderRateLimited("source provider request timed out") from exc
        raise _SourceProviderUnavailable("source provider request unavailable") from exc
    status_code = int(getattr(response, "status_code", 0) or 0)
    response_text = str(getattr(response, "text", "") or "")
    if status_code == 403:
        if blocked_hosts is not None and host:
            blocked_hosts[host] = "blocked"
        raise _SourceProviderBlocked("source provider blocked request")
    if status_code == 429 or _looks_like_rate_limit_text(response_text):
        if blocked_hosts is not None and host:
            blocked_hosts[host] = "rate_limited"
        raise _SourceProviderRateLimited("source provider rate limited")
    if status_code < 200 or status_code >= 300:
        raise _SourceProviderRequestFailed(
            f"source provider request failed with status {status_code}"
        )
    return _response_text(response)


def _http_post_json(
    session: object,
    url: str,
    *,
    data: Mapping[str, object] | None = None,
    timeout: int,
    blocked_hosts: dict[str, str] | None = None,
) -> object:
    host = (urlparse(url).hostname or "").lower()
    if blocked_hosts is not None and host in blocked_hosts:
        if blocked_hosts[host] == "blocked":
            raise _SourceProviderBlocked("source provider host is blocked")
        raise _SourceProviderRateLimited("source provider host is rate limited")
    try:
        response = session.post(url, data=data, timeout=timeout)  # type: ignore[attr-defined]
    except Exception as exc:
        if _looks_like_timeout(exc):
            raise _SourceProviderRateLimited("source provider request timed out") from exc
        raise _SourceProviderUnavailable("source provider request unavailable") from exc
    status_code = int(getattr(response, "status_code", 0) or 0)
    response_text = str(getattr(response, "text", "") or "")
    if status_code == 403:
        if blocked_hosts is not None and host:
            blocked_hosts[host] = "blocked"
        raise _SourceProviderBlocked("source provider blocked request")
    if status_code == 429 or _looks_like_rate_limit_text(response_text):
        if blocked_hosts is not None and host:
            blocked_hosts[host] = "rate_limited"
        raise _SourceProviderRateLimited("source provider rate limited")
    if status_code < 200 or status_code >= 300:
        raise _SourceProviderRequestFailed(
            f"source provider request failed with status {status_code}"
        )
    try:
        return response.json()  # type: ignore[attr-defined]
    except Exception:
        try:
            return json.loads(_response_text(response))
        except Exception as exc:
            raise _SourceProviderRequestFailed("source provider JSON response invalid") from exc


def _http_post_text(
    session: object,
    url: str,
    *,
    data: Mapping[str, object] | None = None,
    timeout: int,
    blocked_hosts: dict[str, str] | None = None,
) -> str:
    host = (urlparse(url).hostname or "").lower()
    if blocked_hosts is not None and host in blocked_hosts:
        if blocked_hosts[host] == "blocked":
            raise _SourceProviderBlocked("source provider host is blocked")
        raise _SourceProviderRateLimited("source provider host is rate limited")
    try:
        response = session.post(url, data=data, timeout=timeout)  # type: ignore[attr-defined]
    except Exception as exc:
        if _looks_like_timeout(exc):
            raise _SourceProviderRateLimited("source provider request timed out") from exc
        raise _SourceProviderUnavailable("source provider request unavailable") from exc
    status_code = int(getattr(response, "status_code", 0) or 0)
    response_text = str(getattr(response, "text", "") or "")
    if status_code == 403:
        if blocked_hosts is not None and host:
            blocked_hosts[host] = "blocked"
        raise _SourceProviderBlocked("source provider blocked request")
    if status_code == 429 or _looks_like_rate_limit_text(response_text):
        if blocked_hosts is not None and host:
            blocked_hosts[host] = "rate_limited"
        raise _SourceProviderRateLimited("source provider rate limited")
    if status_code < 200 or status_code >= 300:
        raise _SourceProviderRequestFailed(
            f"source provider request failed with status {status_code}"
        )
    return _response_text(response)


def _response_text(response: object) -> str:
    content = getattr(response, "content", None)
    if isinstance(content, bytes) and content:
        for encoding in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return content.decode("utf-8", errors="ignore")
    return str(getattr(response, "text", "") or "")


def _looks_like_timeout(exc: Exception) -> bool:
    name = exc.__class__.__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timed out" in message or "timeout" in message


def _looks_like_rate_limit_text(text: str) -> bool:
    normalized = text.lower()
    return "rate limit" in normalized or "too many" in normalized or "exceeded" in normalized


def _kodex_catalog_entry(row: Mapping[str, object]) -> SourceCatalogEntry | None:
    provider_etf_id = _text_or_none(row.get("fId"))
    etf_name = _text_or_none(row.get("fNm"))
    if provider_etf_id is None or etf_name is None:
        return None
    return SourceCatalogEntry(
        source_provider_id=KODEX_SOURCE_PROVIDER_ID,
        provider_etf_id=provider_etf_id,
        etf_id=_canonical_etf_id(KODEX_SOURCE_PROVIDER_ID, provider_etf_id),
        etf_name=etf_name,
        brand_id=KODEX_BRAND_ID,
        brand_name=KODEX_BRAND_NAME,
        strategy_label=_text_or_none(row.get("typeNm")) or _text_or_none(row.get("typeLnm")),
        locator=f"{KODEX_BASE_URL}/etf/product/view.do?id={provider_etf_id}",
    )


def _ace_catalog_entry(row: Mapping[str, object]) -> SourceCatalogEntry | None:
    provider_etf_id = _text_or_none(row.get("fundCd"))
    etf_name = _text_or_none(row.get("fundNm")) or _text_or_none(row.get("fundWhlNm"))
    if provider_etf_id is None or etf_name is None:
        return None
    return SourceCatalogEntry(
        source_provider_id=ACE_SOURCE_PROVIDER_ID,
        provider_etf_id=provider_etf_id,
        etf_id=_canonical_etf_id(ACE_SOURCE_PROVIDER_ID, provider_etf_id),
        etf_name=html.unescape(etf_name),
        brand_id=ACE_BRAND_ID,
        brand_name=ACE_BRAND_NAME,
        strategy_label=_ace_strategy_label(row),
        locator=f"https://www.aceetf.co.kr/fund/{provider_etf_id}",
    )


def _ace_strategy_label(row: Mapping[str, object]) -> str | None:
    badge = row.get("badge")
    if isinstance(badge, Mapping):
        for value in badge.values():
            text = _text_or_none(value)
            if text is not None:
                return text
    return _text_or_none(row.get("themeNms")) or _text_or_none(row.get("theme_NMS"))


def _hyundai_catalog_entry(row: Mapping[str, object]) -> SourceCatalogEntry | None:
    fund = row.get("fund")
    fund_obj = fund if isinstance(fund, Mapping) else {}
    provider_etf_id = _text_or_none(fund_obj.get("id")) or _text_or_none(row.get("id"))
    etf_name = _hyundai_catalog_name(fund_obj, row)
    if provider_etf_id is None or etf_name is None:
        return None
    return SourceCatalogEntry(
        source_provider_id=HYUNDAI_SOURCE_PROVIDER_ID,
        provider_etf_id=provider_etf_id,
        etf_id=_canonical_etf_id(HYUNDAI_SOURCE_PROVIDER_ID, provider_etf_id),
        etf_name=etf_name,
        brand_id=HYUNDAI_BRAND_ID,
        brand_name=HYUNDAI_BRAND_NAME,
        strategy_label=None,
        locator=f"{HYUNDAI_BASE_URL}/kor/HD-KP-FG/HD-KP-FG-07-D.html?id={provider_etf_id}",
    )


def _hyundai_catalog_name(
    fund_obj: Mapping[str, object],
    row: Mapping[str, object],
) -> str | None:
    for key in ("리스트펀드명", "펀드명", "name", "fNm"):
        text = _text_or_none(fund_obj.get(key)) or _text_or_none(row.get(key))
        if text is not None and "ETF" in text.upper():
            return text
    for value in list(fund_obj.values()) + list(row.values()):
        text = _text_or_none(value)
        if text is not None and "ETF" in text.upper() and len(text) <= 120:
            return text
    return None


def _timefolio_catalog_entries(html_text: str) -> list[SourceCatalogEntry]:
    entries: list[SourceCatalogEntry] = []
    for provider_etf_id, name in re.findall(
        r'href=["\']\./m11_view\.php\?idx=(\d+)["\'][^>]*>.*?<div[^>]*>(.*?)</div>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        fund_id = provider_etf_id.strip()
        fund_name = _strip_html_tags(name)
        if not fund_id or not fund_name:
            continue
        entries.append(
            SourceCatalogEntry(
                source_provider_id=TIMEFOLIO_SOURCE_PROVIDER_ID,
                provider_etf_id=fund_id,
                etf_id=_canonical_etf_id(TIMEFOLIO_SOURCE_PROVIDER_ID, fund_id),
                etf_name=f"TIME {fund_name}",
                brand_id=TIMEFOLIO_BRAND_ID,
                brand_name=TIMEFOLIO_BRAND_NAME,
                locator=f"{TIMEFOLIO_BASE_URL}/m11_view.php?idx={fund_id}",
            )
        )
    return entries


def _tiger_catalog_entries(html_text: str) -> tuple[list[SourceCatalogEntry], int | None]:
    entries: list[SourceCatalogEntry] = []
    total_count: int | None = None
    for count_text, provider_etf_id, name in re.findall(
        (
            r'<div class="c-data-row"[^>]*data-tot-cnt="(\d+)"'
            r'[^>]*data-ksd-fund="([^"]+)"[^>]*>.*?'
            r'<div class="title"><a [^>]*>([^<]+)</a></div>'
        ),
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        if total_count is None:
            total_count = _int_or_none(count_text)
        fund_id = provider_etf_id.strip()
        fund_name = html.unescape(name.strip())
        if not fund_id or not fund_name:
            continue
        entries.append(
            SourceCatalogEntry(
                source_provider_id=TIGER_SOURCE_PROVIDER_ID,
                provider_etf_id=fund_id,
                etf_id=_canonical_etf_id(TIGER_SOURCE_PROVIDER_ID, fund_id),
                etf_name=fund_name,
                brand_id=TIGER_BRAND_ID,
                brand_name=TIGER_BRAND_NAME,
                locator=f"{TIGER_BASE_URL}/tigeretf/ko/product/view.do?ksdFund={fund_id}",
            )
        )
    return entries, total_count


def _rise_catalog_entries(html_text: str) -> list[SourceCatalogEntry]:
    entries: list[SourceCatalogEntry] = []
    seen: set[str] = set()
    detail_patterns = (
        r"location\.href\s*=\s*['\"](?:\.\.)?/prod/finderDetail/([A-Za-z0-9_-]+)['\"][^>]*>(.*?)</",
        r'href=["\'](?:https?://[^"\']+)?/prod/finderDetail/([A-Za-z0-9_-]+)["\'][^>]*>(.*?)</a>',
    )
    for pattern in detail_patterns:
        for provider_etf_id, name_html in re.findall(
            pattern,
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            fund_id = provider_etf_id.strip()
            fund_name = _strip_html_tags(name_html)
            if not fund_id or not fund_name or fund_id in seen:
                continue
            seen.add(fund_id)
            entries.append(
                SourceCatalogEntry(
                    source_provider_id=RISE_SOURCE_PROVIDER_ID,
                    provider_etf_id=fund_id,
                    etf_id=_canonical_etf_id(RISE_SOURCE_PROVIDER_ID, fund_id),
                    etf_name=fund_name,
                    brand_id=RISE_BRAND_ID,
                    brand_name=RISE_BRAND_NAME,
                    locator=f"{RISE_BASE_URL}/prod/finderDetail/{fund_id}",
                )
            )
    return entries


def _sol_catalog_entries_from_html(html_text: str) -> list[SourceCatalogEntry]:
    entries: list[SourceCatalogEntry] = []
    for provider_etf_id, block in re.findall(
        r'<a[^>]*href=["\']/ko/fund/etf/([A-Za-z0-9]+)["\'][^>]*class=["\'][^"\']*fd-link[^"\']*["\'][^>]*>(.*?)</a>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        name_match = re.search(
            r'<span[^>]*class=["\'][^"\']*fd-name[^"\']*["\'][^>]*>(.*?)</span>',
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        fund_name = _strip_html_tags(
            name_match.group(1) if name_match is not None else block
        )
        entry = _sol_catalog_entry(provider_etf_id, fund_name)
        if entry is not None:
            entries.append(entry)
    return entries


def _sol_catalog_entries_from_payload(payload: object) -> list[SourceCatalogEntry]:
    response = payload if isinstance(payload, Mapping) else {}
    raw_items = response.get("items") or []
    if not isinstance(raw_items, list):
        return []
    entries: list[SourceCatalogEntry] = []
    for item in raw_items:
        if not isinstance(item, Mapping):
            continue
        provider_etf_id = _text_or_none(item.get("FUND_CD"))
        fund_name = _text_or_none(item.get("Name")) or _text_or_none(
            item.get("EngName")
        )
        entry = _sol_catalog_entry(provider_etf_id, fund_name)
        if entry is not None:
            entries.append(entry)
    return entries


def _sol_catalog_entry(
    provider_etf_id: str | None,
    etf_name: str | None,
) -> SourceCatalogEntry | None:
    if provider_etf_id is None or etf_name is None:
        return None
    fund_id = provider_etf_id.strip()
    fund_name = html.unescape(etf_name.strip())
    if (
        not fund_id
        or not fund_id.isdigit()
        or fund_id == "0000000"
        or not fund_name
        or fund_name.lower() in {"none", "sol etf"}
    ):
        return None
    return SourceCatalogEntry(
        source_provider_id=SOL_SOURCE_PROVIDER_ID,
        provider_etf_id=fund_id,
        etf_id=_canonical_etf_id(SOL_SOURCE_PROVIDER_ID, fund_id),
        etf_name=fund_name,
        brand_id=SOL_BRAND_ID,
        brand_name=SOL_BRAND_NAME,
        locator=f"{SOL_BASE_URL}/ko/fund/etf/{fund_id}",
    )


def _canonical_etf_id(source_provider_id: str, provider_etf_id: str) -> str:
    token = re.sub(r"[^a-z0-9]+", "_", provider_etf_id.lower()).strip("_")
    if not token:
        raise SourceAcquisitionInputError("provider_etf_id cannot form canonical ETF id")
    return f"etf_{source_provider_id}_{token}"


def _kodex_holdings_rows(
    *,
    detail_payload: object,
    pdf_payload: object,
    target: HoldingsFetchTarget,
) -> tuple[str, list[dict[str, JsonValue]]]:
    detail = detail_payload if isinstance(detail_payload, Mapping) else {}
    pdf_response = pdf_payload if isinstance(pdf_payload, Mapping) else {}
    base_pdf = detail.get("pdf")
    if not isinstance(base_pdf, Mapping):
        base_pdf = {}
    dated_pdf = pdf_response.get("pdf")
    if not isinstance(dated_pdf, Mapping):
        dated_pdf = {}
    observed_date = _provider_date_to_iso(
        dated_pdf.get("gijunYMD") or base_pdf.get("gijunYMD")
    )
    raw_rows = dated_pdf.get("list") or base_pdf.get("list") or []
    if not isinstance(raw_rows, list) or not raw_rows:
        raise SourceAcquisitionInputError("KODEX holdings payload contained no rows")
    rows: list[dict[str, JsonValue]] = []
    for row in raw_rows:
        if not isinstance(row, Mapping):
            raise SourceAcquisitionInputError("KODEX holdings row must be an object")
        security_id = _text_or_none(row.get("itmNo"))
        name = _text_or_none(row.get("secNm"))
        if name is None:
            raise SourceAcquisitionInputError("KODEX holdings row missing security name")
        is_cash = _is_cash_like(security_id=security_id, name=name)
        if security_id is None:
            if not is_cash:
                raise SourceAcquisitionInputError("KODEX holdings row missing security id")
            security_id = f"CASH_UNCODED:kodex:{target.provider_etf_id}"
        weight_percent, weight_missing = _provider_weight_percent(
            row.get("ratio"),
            is_cash=is_cash,
        )
        rows.append(
            {
                "security_id": security_id,
                "ticker": None,
                "name": "Cash" if is_cash else name,
                "market": None,
                "sector": None,
                "theme": None,
                "country": None,
                "weight_percent": weight_percent,
                "shares": _provider_optional_number(row.get("applyQ")),
                "market_value_krw": _provider_optional_number(row.get("evalA")),
                "price_krw": None,
                "is_cash": is_cash,
                "security_classification": "cash_like" if is_cash else "ticker_candidate",
                _PROVIDER_WEIGHT_MISSING_FIELD: weight_missing,
            }
        )
    return observed_date, _finalize_provider_holdings_rows(rows)


def _ace_holdings_rows(
    *,
    pdf_payload: object,
    target: HoldingsFetchTarget,
) -> tuple[str, list[dict[str, JsonValue]]]:
    payload = pdf_payload if isinstance(pdf_payload, Mapping) else {}
    raw_rows = payload.get("pdfList") or []
    if not isinstance(raw_rows, list) or not raw_rows:
        raise SourceAcquisitionInputError("ACE holdings payload contained no rows")
    fallback_date = _text_or_none(payload.get("last_STD_DT")) or _text_or_none(
        payload.get("std_DT")
    )
    observed_dates: set[str] = set()
    rows: list[dict[str, JsonValue]] = []
    for line_number, row in enumerate(raw_rows, 1):
        if not isinstance(row, Mapping):
            raise SourceAcquisitionInputError("ACE holdings row must be an object")
        row_date = _text_or_none(row.get("std_DT")) or fallback_date
        observed_date = _provider_date_to_iso(row_date)
        observed_dates.add(observed_date)
        rows.append(
            _provider_holding_row(
                source_provider_id=ACE_SOURCE_PROVIDER_ID,
                provider_etf_id=target.provider_etf_id,
                observed_date=observed_date,
                line_number=line_number,
                security_id=_text_or_none(row.get("jm_KSC_CD")),
                name=_text_or_none(row.get("sec_NM")),
                weight_percent=row.get("wg"),
                shares=row.get("cu_ITEM_CNT"),
                market_value_krw=row.get("val_AM"),
                price_krw=None,
            )
        )
    if len(observed_dates) != 1:
        raise SourceAcquisitionInputError("ACE holdings payload mixed observed dates")
    return next(iter(observed_dates)), _finalize_provider_holdings_rows(rows)


def _hyundai_pdf_codes(info_payload: object) -> tuple[str, str]:
    payload = info_payload if isinstance(info_payload, Mapping) else {}
    fund = payload.get("fund")
    fund_obj = fund if isinstance(fund, Mapping) else {}
    fund_code = _text_or_none(fund_obj.get("펀드코드")) or _text_or_none(
        payload.get("fundCode")
    )
    etf_code = _text_or_none(payload.get("종목코드")) or _text_or_none(
        payload.get("etfCode")
    )
    if fund_code is None or etf_code is None:
        raise SourceAcquisitionInputError("HYUNDAI holdings metadata missing PDF codes")
    return fund_code, etf_code


def _hyundai_holdings_rows(
    *,
    pdf_payload: object,
    target: HoldingsFetchTarget,
) -> tuple[str, list[dict[str, JsonValue]]]:
    if not isinstance(pdf_payload, list) or not pdf_payload:
        raise SourceAcquisitionInputError("HYUNDAI holdings payload contained no rows")
    observed_dates: set[str] = set()
    rows: list[dict[str, JsonValue]] = []
    for line_number, row in enumerate(pdf_payload, 1):
        if not isinstance(row, Mapping):
            raise SourceAcquisitionInputError("HYUNDAI holdings row must be an object")
        observed_date = _provider_date_to_iso(
            _text_or_none(row.get("date")) or target.provider_query_date
        )
        observed_dates.add(observed_date)
        rows.append(
            _provider_holding_row(
                source_provider_id=HYUNDAI_SOURCE_PROVIDER_ID,
                provider_etf_id=target.provider_etf_id,
                observed_date=observed_date,
                line_number=line_number,
                security_id=_text_or_none(row.get("구성종목코드")),
                name=_text_or_none(row.get("구성종목명")),
                weight_percent=row.get("비중"),
                shares=row.get("구성종목수"),
                market_value_krw=row.get("평가금액"),
                price_krw=None,
            )
        )
    if len(observed_dates) != 1:
        raise SourceAcquisitionInputError("HYUNDAI holdings payload mixed observed dates")
    return next(iter(observed_dates)), _finalize_provider_holdings_rows(rows)


def _timefolio_holdings_rows(
    *,
    detail_html: str,
    target: HoldingsFetchTarget,
) -> tuple[str, list[dict[str, JsonValue]]]:
    observed_date = _provider_date_to_iso(
        _html_input_value(detail_html, "pdfDate") or target.provider_query_date
    )
    parsed_rows = _html_table_rows(detail_html, class_name="moreList1")
    if not parsed_rows:
        raise SourceAcquisitionInputError("TIMEFOLIO holdings payload contained no rows")
    rows: list[dict[str, JsonValue]] = []
    for line_number, cells in enumerate(parsed_rows, 1):
        if len(cells) < 5:
            raise SourceAcquisitionInputError("TIMEFOLIO holdings row is incomplete")
        code, name, shares, market_value, weight = cells[:5]
        rows.append(
            _provider_holding_row(
                source_provider_id=TIMEFOLIO_SOURCE_PROVIDER_ID,
                provider_etf_id=target.provider_etf_id,
                observed_date=observed_date,
                line_number=line_number,
                security_id=_text_or_none(code),
                name=_text_or_none(name),
                weight_percent=weight,
                shares=shares,
                market_value_krw=market_value,
                price_krw=None,
            )
        )
    return observed_date, _finalize_provider_holdings_rows(rows)


def _tiger_holdings_rows(
    *,
    payload: object,
    target: HoldingsFetchTarget,
) -> tuple[str, list[dict[str, JsonValue]]]:
    response = payload if isinstance(payload, Mapping) else {}
    raw_rows = response.get("rtnData") or []
    if not isinstance(raw_rows, list) or not raw_rows:
        raise SourceAcquisitionInputError("TIGER holdings payload contained no rows")
    observed_dates: set[str] = set()
    rows: list[dict[str, JsonValue]] = []
    for line_number, row in enumerate(raw_rows, 1):
        if not isinstance(row, Mapping):
            raise SourceAcquisitionInputError("TIGER holdings row must be an object")
        observed_date = _provider_date_to_iso(
            _text_or_none(row.get("wkdate")) or target.provider_query_date
        )
        observed_dates.add(observed_date)
        security_id = _text_or_none(row.get("memItemcode"))
        if security_id in {None, "0"}:
            security_id = _text_or_none(row.get("code"))
        rows.append(
            _provider_holding_row(
                source_provider_id=TIGER_SOURCE_PROVIDER_ID,
                provider_etf_id=target.provider_etf_id,
                observed_date=observed_date,
                line_number=line_number,
                security_id=security_id,
                name=_text_or_none(row.get("memItemname")),
                weight_percent=row.get("stockRate"),
                shares=row.get("stockQty"),
                market_value_krw=row.get("stockPrc"),
                price_krw=None,
            )
        )
    if len(observed_dates) != 1:
        raise SourceAcquisitionInputError("TIGER holdings payload mixed observed dates")
    return next(iter(observed_dates)), _finalize_provider_holdings_rows(rows)


def _rise_holdings_rows(
    *,
    holdings_html: str,
    target: HoldingsFetchTarget,
) -> tuple[str, list[dict[str, JsonValue]]]:
    observed_date = _provider_date_to_iso(
        _first_provider_date(holdings_html) or target.provider_query_date
    )
    parsed_rows = _html_table_rows(holdings_html, class_name=None)
    if not parsed_rows:
        parsed_rows = _html_fragment_rows(holdings_html)
    rows: list[dict[str, JsonValue]] = []
    for line_number, cells in enumerate(parsed_rows, 1):
        if len(cells) < 6 or not cells[0].strip().isdigit():
            continue
        _, name, security_id, shares, weight, market_value = cells[:6]
        rows.append(
            _provider_holding_row(
                source_provider_id=RISE_SOURCE_PROVIDER_ID,
                provider_etf_id=target.provider_etf_id,
                observed_date=observed_date,
                line_number=line_number,
                security_id=_text_or_none(security_id),
                name=_text_or_none(name),
                weight_percent=weight,
                shares=shares,
                market_value_krw=market_value,
                price_krw=None,
            )
        )
    if not rows:
        raise SourceAcquisitionInputError("RISE holdings payload contained no rows")
    return observed_date, _finalize_provider_holdings_rows(rows)


def _sol_holdings_rows(
    *,
    payload: object,
    target: HoldingsFetchTarget,
) -> tuple[str, list[dict[str, JsonValue]]]:
    if not isinstance(payload, list) or not payload:
        raise SourceAcquisitionInputError("SOL holdings payload contained no rows")
    observed_dates: set[str] = set()
    rows: list[dict[str, JsonValue]] = []
    for line_number, row in enumerate(payload, 1):
        if not isinstance(row, Mapping):
            raise SourceAcquisitionInputError("SOL holdings row must be an object")
        observed_date = _provider_date_to_iso(
            _text_or_none(row.get("WORK_DT")) or target.provider_query_date
        )
        observed_dates.add(observed_date)
        rows.append(
            _provider_holding_row(
                source_provider_id=SOL_SOURCE_PROVIDER_ID,
                provider_etf_id=target.provider_etf_id,
                observed_date=observed_date,
                line_number=line_number,
                security_id=_text_or_none(row.get("STOCK_CODE")),
                name=_text_or_none(row.get("SEC_NM")),
                weight_percent=row.get("WT_DISP"),
                shares=row.get("QTY"),
                market_value_krw=row.get("PRICE"),
                price_krw=None,
            )
        )
    if len(observed_dates) != 1:
        raise SourceAcquisitionInputError("SOL holdings payload mixed observed dates")
    return next(iter(observed_dates)), _finalize_provider_holdings_rows(rows)


def _first_provider_date(text: str) -> str | None:
    match = re.search(r"\b(20\d{2})[.\-/ ]?([01]\d)[.\-/ ]?([0-3]\d)\b", text)
    if match is None:
        return None
    return "-".join(match.groups())


def _html_input_value(html_text: str, input_id: str) -> str | None:
    tag_match = re.search(
        rf'<input[^>]*id=["\']{re.escape(input_id)}["\'][^>]*>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if tag_match is None:
        return None
    value_match = re.search(
        r'value=["\']([^"\']+)["\']',
        tag_match.group(0),
        flags=re.IGNORECASE,
    )
    if value_match is None:
        return None
    return html.unescape(value_match.group(1)).strip() or None


def _html_table_rows(html_text: str, *, class_name: str | None = None) -> list[list[str]]:
    if class_name is None:
        table_htmls = re.findall(
            r"<table[^>]*>(.*?)</table>",
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    else:
        table_htmls = re.findall(
            rf'<table[^>]*class=["\'][^"\']*{re.escape(class_name)}[^"\']*["\'][^>]*>(.*?)</table>',
            html_text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    rows: list[list[str]] = []
    for table_html in table_htmls:
        body_match = re.search(
            r"<tbody[^>]*>(.*?)</tbody>",
            table_html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        body = body_match.group(1) if body_match is not None else table_html
        for row_html in re.findall(
            r"<tr[^>]*>(.*?)</tr>",
            body,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            cells = [
                _strip_html_tags(cell)
                for cell in re.findall(
                    r"<t[hd][^>]*>(.*?)</t[hd]>",
                    row_html,
                    flags=re.IGNORECASE | re.DOTALL,
                )
            ]
            if cells:
                rows.append(cells)
    return rows


def _html_fragment_rows(html_text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for row_html in re.findall(
        r"<tr[^>]*>(.*?)</tr>",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        cells = [
            _strip_html_tags(cell)
            for cell in re.findall(
                r"<t[hd][^>]*>(.*?)</t[hd]>",
                row_html,
                flags=re.IGNORECASE | re.DOTALL,
            )
        ]
        if cells:
            rows.append(cells)
    return rows


def _strip_html_tags(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def _provider_holding_row(
    *,
    source_provider_id: str,
    provider_etf_id: str,
    observed_date: str,
    line_number: int,
    security_id: str | None,
    name: str | None,
    weight_percent: object,
    shares: object,
    market_value_krw: object,
    price_krw: object,
) -> dict[str, JsonValue]:
    if name is None:
        if security_id is None:
            raise SourceAcquisitionInputError("provider holdings row missing security name")
        name = security_id
    classification = _SECURITY_CLASSIFICATION_POLICY.classify(
        security_id=security_id,
        name=name,
        as_of_date=observed_date,
    )
    if security_id is None:
        if classification != "cash_like":
            classification = "unknown"
            security_id = _uncoded_security_id(
                source_provider_id=source_provider_id,
                provider_etf_id=provider_etf_id,
                line_number=line_number,
                name=name,
            )
        else:
            security_id = f"CASH_UNCODED:{source_provider_id}:{provider_etf_id}"
    is_cash = classification == "cash_like"
    parsed_weight, weight_missing = _provider_weight_percent(
        weight_percent,
        is_cash=is_cash,
    )
    return {
        "security_id": security_id,
        "ticker": None,
        "name": "Cash" if is_cash else name,
        "market": None,
        "sector": None,
        "theme": None,
        "country": None,
        "weight_percent": parsed_weight,
        "shares": _provider_optional_number(shares),
        "market_value_krw": _provider_optional_number(market_value_krw),
        "price_krw": _provider_optional_number(price_krw),
        "is_cash": is_cash,
        "security_classification": classification,
        _PROVIDER_WEIGHT_MISSING_FIELD: weight_missing,
    }


def _uncoded_security_id(
    *,
    source_provider_id: str,
    provider_etf_id: str,
    line_number: int,
    name: str,
) -> str:
    normalized_name = re.sub(r"\s+", " ", name.strip().upper())
    digest = hashlib.sha256(normalized_name.encode("utf-8")).hexdigest()[:12]
    return f"UNCODED:{source_provider_id}:{provider_etf_id}:{line_number}:{digest}"


def _source_failed_result(
    target: HoldingsFetchTarget,
    *,
    outcome: str,
    failure_code_class: str,
) -> HoldingsFetchResult:
    return HoldingsFetchResult(
        source_provider_id=target.source_provider_id,
        provider_etf_id=target.provider_etf_id,
        etf_id=target.etf_id,
        requested_date=target.requested_date,
        provider_query_date=target.provider_query_date,
        outcome=outcome,
        failure_code_class=failure_code_class,
    )


def _kodex_failed_result(
    target: HoldingsFetchTarget,
    *,
    outcome: str,
    failure_code_class: str,
) -> HoldingsFetchResult:
    return HoldingsFetchResult(
        source_provider_id=target.source_provider_id,
        provider_etf_id=target.provider_etf_id,
        etf_id=target.etf_id,
        requested_date=target.requested_date,
        provider_query_date=target.provider_query_date,
        outcome=outcome,
        failure_code_class=failure_code_class,
    )


def _provider_date_to_iso(value: object) -> str:
    text = _text_or_none(value)
    if text is None:
        raise SourceAcquisitionInputError("provider observed date is missing")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) != 8:
        raise SourceAcquisitionInputError("provider observed date is invalid")
    iso = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    _parse_iso_date(iso)
    return iso


def _provider_number(value: object) -> float:
    parsed = _provider_optional_number(value)
    if parsed is None:
        raise SourceAcquisitionInputError("provider numeric field is missing")
    return parsed


def _provider_weight_percent(value: object, *, is_cash: bool) -> tuple[float, bool]:
    parsed = _provider_optional_number(value)
    if parsed is not None:
        return parsed, False
    if is_cash:
        return 0.0, True
    raise SourceAcquisitionInputError("provider numeric field is missing")


def _finalize_provider_holdings_rows(
    rows: list[dict[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    finalized = _drop_zero_weight_cash_total_rows([dict(row) for row in rows])
    denominator_values = [
        float(row["market_value_krw"])
        for row in finalized
        if isinstance(row.get("market_value_krw"), int | float)
    ]
    denominator = sum(denominator_values)
    denominator_is_valid = bool(denominator_values) and denominator > 0
    fit_failure = False
    if denominator_is_valid:
        fit_errors = [
            abs(
                float(row["weight_percent"])
                - (float(row["market_value_krw"]) / denominator * 100)
            )
            for row in finalized
            if (
                row.get(_PROVIDER_WEIGHT_MISSING_FIELD) is not True
                and isinstance(row.get("weight_percent"), int | float)
                and isinstance(row.get("market_value_krw"), int | float)
            )
        ]
        fit_failure = (
            not fit_errors
            or _median(fit_errors) > _WEIGHT_FIT_TOLERANCE_PERCENT_POINTS
        )
    for row in finalized:
        weight_missing = row.pop(_PROVIDER_WEIGHT_MISSING_FIELD, False)
        if (
            weight_missing is True
            and row.get("is_cash") is True
            and denominator_is_valid
            and not fit_failure
            and isinstance(row.get("market_value_krw"), int | float)
        ):
            row["weight_percent"] = round(
                float(row["market_value_krw"]) / denominator * 100,
                6,
            )
    return finalized


def _drop_zero_weight_cash_total_rows(
    rows: list[dict[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    retained: list[dict[str, JsonValue]] = []
    for index, row in enumerate(rows):
        if (
            row.get("is_cash") is True
            and row.get("security_id") == "CASH00000001"
            and row.get("weight_percent") == 0.0
            and isinstance(row.get("market_value_krw"), int | float)
        ):
            other_total = sum(
                float(other["market_value_krw"])
                for other_index, other in enumerate(rows)
                if (
                    other_index != index
                    and isinstance(other.get("market_value_krw"), int | float)
                )
            )
            if abs(float(row["market_value_krw"]) - other_total) <= 1.0:
                continue
        retained.append(row)
    return retained


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def _provider_optional_number(value: object) -> float | None:
    text = _text_or_none(value)
    if text is None or text in {"-", "N/A"}:
        return None
    try:
        return float(text.replace(",", "").replace("%", ""))
    except ValueError as exc:
        raise SourceAcquisitionInputError("provider numeric field is invalid") from exc


def _is_cash_like(*, security_id: str | None, name: str) -> bool:
    normalized_id = (security_id or "").upper()
    normalized_name = name.upper()
    return (
        normalized_id in KODEX_CASH_EXACT_CODES
        or normalized_id.startswith(("CASH", "KRW", "USD"))
        or "DEPOSIT" in normalized_name
        or "현금" in name
        or "예금" in name
        or "CASH" in normalized_name
        or "MMF" in normalized_name
    )


def _int_or_none(value: object) -> int | None:
    text = _text_or_none(value)
    if text is None:
        return None
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def _text_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _target_outcome(value: object) -> str:
    text = _required_safe_text(value, "holdings_result.outcome")
    allowed = {"fetched", "skipped_existing", "failed", "rate_limited", "unsupported"}
    if text not in allowed:
        raise SourceAcquisitionInputError(f"invalid source target outcome: {text}")
    return text


def _retry_attempt_count(value: object) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SourceAcquisitionInputError("retry_attempt_count must be a non-negative integer")
    return value


def _validate_complete_catalog(
    catalog: SourceCatalogResult,
    *,
    expected_source_provider_id: str,
) -> tuple[SourceCatalogEntry, ...]:
    source_provider_id = _required_safe_text(
        catalog.source_provider_id,
        "source_provider_id",
    )
    if source_provider_id != expected_source_provider_id:
        raise SourceAcquisitionInputError("source catalog provider id mismatch")
    if catalog.complete is not True:
        raise SourceAcquisitionInputError("source catalog must be complete before universe update")
    if not catalog.entries:
        raise SourceAcquisitionInputError("source catalog must contain at least one ETF")

    provider_etf_ids: set[str] = set()
    etf_ids: set[str] = set()
    brand_names: dict[str, str] = {}
    entries: list[SourceCatalogEntry] = []
    for entry in catalog.entries:
        _validate_entry_provider(entry, source_provider_id=source_provider_id)
        if entry.provider_etf_id in provider_etf_ids:
            raise SourceAcquisitionInputError(
                f"duplicate provider_etf_id in source catalog: {entry.provider_etf_id}"
            )
        if entry.etf_id in etf_ids:
            raise SourceAcquisitionInputError(
                f"duplicate etf_id in source catalog: {entry.etf_id}"
            )
        previous_brand_name = brand_names.setdefault(entry.brand_id, entry.brand_name)
        if previous_brand_name != entry.brand_name:
            raise SourceAcquisitionInputError(
                f"conflicting brand_name in source catalog: {entry.brand_id}"
            )
        provider_etf_ids.add(entry.provider_etf_id)
        etf_ids.add(entry.etf_id)
        entries.append(_classified_active_strategy_entry(entry))
    return tuple(entries)


def _classified_active_strategy_entry(entry: SourceCatalogEntry) -> SourceCatalogEntry:
    _validate_active_strategy_evidence(entry)
    if _has_active_strategy_passive_keyword(entry):
        return replace(
            entry,
            is_active_strategy_etf=False,
            active_strategy_source="passive_keyword",
            active_strategy_confidence="high",
        )
    if _has_active_strategy_passive_metadata(entry):
        return replace(
            entry,
            is_active_strategy_etf=False,
            active_strategy_source="source_metadata",
            active_strategy_confidence="high",
        )
    seed = _active_strategy_seed_entry(entry)
    if seed is not None:
        is_active, confidence = seed
        return replace(
            entry,
            is_active_strategy_etf=is_active,
            active_strategy_source="reference_seed",
            active_strategy_confidence=confidence,
        )
    if (
        entry.is_active_strategy_etf is False
        and entry.active_strategy_source != "unknown"
    ):
        return entry
    if _has_active_strategy_active_metadata(entry):
        return replace(
            entry,
            is_active_strategy_etf=True,
            active_strategy_source="source_metadata",
            active_strategy_confidence="high",
        )
    if (
        entry.is_active_strategy_etf is True
        and entry.active_strategy_source != "unknown"
    ):
        return entry
    if entry.source_provider_id == TIMEFOLIO_SOURCE_PROVIDER_ID:
        return replace(
            entry,
            is_active_strategy_etf=True,
            active_strategy_source="timefolio_provider_default",
            active_strategy_confidence="medium",
        )
    if _has_active_strategy_name_token(entry.etf_name):
        return replace(
            entry,
            is_active_strategy_etf=True,
            active_strategy_source="name_token",
            active_strategy_confidence="low",
        )
    return replace(
        entry,
        is_active_strategy_etf=None,
        active_strategy_source="unknown",
        active_strategy_confidence="low",
    )


def _validate_active_strategy_evidence(entry: SourceCatalogEntry) -> None:
    if entry.active_strategy_source not in _ACTIVE_STRATEGY_SOURCES:
        raise SourceAcquisitionInputError(
            f"invalid active_strategy_source: {entry.active_strategy_source}"
        )
    if entry.active_strategy_confidence not in _ACTIVE_STRATEGY_CONFIDENCES:
        raise SourceAcquisitionInputError(
            "invalid active_strategy_confidence: "
            f"{entry.active_strategy_confidence}"
        )
    if (
        entry.is_active_strategy_etf is None
        and entry.active_strategy_source != "unknown"
    ):
        raise SourceAcquisitionInputError(
            "active strategy source requires is_active_strategy_etf"
        )


def _has_active_strategy_passive_keyword(entry: SourceCatalogEntry) -> bool:
    text = _active_strategy_text(entry.etf_name, entry.strategy_label)
    return any(keyword in text for keyword in _ACTIVE_STRATEGY_PASSIVE_KEYWORDS)


def _has_active_strategy_passive_metadata(entry: SourceCatalogEntry) -> bool:
    return _contains_active_strategy_marker(
        entry.strategy_label,
        _ACTIVE_STRATEGY_PASSIVE_MARKERS,
    )


def _has_active_strategy_active_metadata(entry: SourceCatalogEntry) -> bool:
    return _contains_active_strategy_marker(
        entry.strategy_label,
        _ACTIVE_STRATEGY_ACTIVE_MARKERS,
    )


def _has_active_strategy_name_token(etf_name: str) -> bool:
    return _contains_active_strategy_marker(
        etf_name,
        _ACTIVE_STRATEGY_ACTIVE_MARKERS,
    )


def _contains_active_strategy_marker(
    value: str | None,
    markers: tuple[str, ...],
) -> bool:
    text = _active_strategy_text(value)
    return any(marker.upper() in text for marker in markers)


def _active_strategy_text(*values: str | None) -> str:
    return " ".join(value or "" for value in values).upper()


def _active_strategy_seed_entry(entry: SourceCatalogEntry) -> tuple[bool, str] | None:
    return _active_strategy_seed_records().get(
        (entry.source_provider_id, entry.provider_etf_id)
    )


@cache
def _active_strategy_seed_records() -> dict[tuple[str, str], tuple[bool, str]]:
    if not ACTIVE_STRATEGY_SEED_PATH.is_file():
        return {}
    payload = _read_json_object(
        ACTIVE_STRATEGY_SEED_PATH,
        label="active strategy seed",
    )
    if payload.get("schema_version") != ACTIVE_STRATEGY_SEED_SCHEMA_VERSION:
        raise SourceAcquisitionInputError("invalid active strategy seed schema")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise SourceAcquisitionInputError("active strategy seed entries must be a list")
    records: dict[tuple[str, str], tuple[bool, str]] = {}
    for item in entries:
        if not isinstance(item, Mapping):
            raise SourceAcquisitionInputError("active strategy seed entry must be an object")
        source_provider_id = _required_safe_text(
            item.get("source_provider_id"),
            "active_strategy_seed.entry.source_provider_id",
        )
        provider_etf_id = _required_safe_text(
            item.get("provider_etf_id"),
            "active_strategy_seed.entry.provider_etf_id",
        )
        is_active_strategy_etf = _required_bool(
            item.get("is_active_strategy_etf"),
            "active_strategy_seed.entry.is_active_strategy_etf",
        )
        confidence = _optional_safe_text(
            item.get("active_strategy_confidence"),
            "active_strategy_seed.entry.active_strategy_confidence",
        ) or "high"
        if confidence not in _ACTIVE_STRATEGY_CONFIDENCES:
            raise SourceAcquisitionInputError(
                f"invalid active strategy seed confidence: {confidence}"
            )
        key = (source_provider_id, provider_etf_id)
        if key in records:
            raise SourceAcquisitionInputError(
                "duplicate active strategy seed entry: "
                f"source_provider_id={source_provider_id} "
                f"provider_etf_id={provider_etf_id}"
            )
        records[key] = (is_active_strategy_etf, confidence)
    return records


def _validate_entry_provider(
    entry: SourceCatalogEntry,
    *,
    source_provider_id: str,
) -> None:
    for label, value in (
        ("catalog.entry.source_provider_id", entry.source_provider_id),
        ("catalog.entry.provider_etf_id", entry.provider_etf_id),
        ("catalog.entry.etf_id", entry.etf_id),
        ("catalog.entry.etf_name", entry.etf_name),
        ("catalog.entry.brand_id", entry.brand_id),
        ("catalog.entry.brand_name", entry.brand_name),
    ):
        _required_safe_text(value, label)
    if entry.source_provider_id != source_provider_id:
        raise SourceAcquisitionInputError("source catalog entry provider id mismatch")


def _source_catalog_document(
    *,
    source_provider_id: str,
    complete: bool,
    entries: tuple[SourceCatalogEntry, ...],
    collected_at: str,
) -> dict[str, JsonValue]:
    return {
        "schema_version": SOURCE_CATALOG_SCHEMA_VERSION,
        "source_provider_id": source_provider_id,
        "complete": complete,
        "collected_at": collected_at,
        "entries": [_source_catalog_entry_document(entry) for entry in entries],
    }


def _source_catalog_entry_document(entry: SourceCatalogEntry) -> dict[str, JsonValue]:
    item: dict[str, JsonValue] = {
        "source_provider_id": entry.source_provider_id,
        "provider_etf_id": entry.provider_etf_id,
        "etf_id": entry.etf_id,
        "etf_name": entry.etf_name,
        "brand_id": entry.brand_id,
        "brand_name": entry.brand_name,
        "is_active_strategy_etf": entry.is_active_strategy_etf,
        "active_strategy_source": entry.active_strategy_source,
        "active_strategy_confidence": entry.active_strategy_confidence,
    }
    if entry.strategy_label is not None:
        item["strategy_label"] = entry.strategy_label
    if entry.locator is not None:
        item["locator"] = entry.locator
    return item


def _source_catalog_summary(
    *,
    source_provider_id: str,
    entries: tuple[SourceCatalogEntry, ...],
    collected_at: str,
) -> dict[str, JsonValue]:
    brand_ids = sorted({entry.brand_id for entry in entries})
    active_strategy_counts = _active_strategy_classification_counts(entries)
    return {
        "schema_version": SOURCE_ACQUISITION_SUMMARY_SCHEMA_VERSION,
        "source_provider_id": source_provider_id,
        "run_outcome": "succeeded",
        "collected_at": collected_at,
        "catalog_entry_count": len(entries),
        "brand_count": len(brand_ids),
        "etf_count": len(entries),
        "catalog_output": {"catalog_path": SOURCE_CATALOG_FILENAME},
        "universe_state_output": {"state_path": UNIVERSE_STATE_FILENAME},
        "catalog_entries": [
            {
                "source_provider_id": entry.source_provider_id,
                "brand_id": entry.brand_id,
                "etf_id": entry.etf_id,
                "is_active_strategy_etf": entry.is_active_strategy_etf,
                "active_strategy_source": entry.active_strategy_source,
                "active_strategy_confidence": entry.active_strategy_confidence,
            }
            for entry in entries
        ],
        "active_strategy_classification_counts": active_strategy_counts,
        "active_strategy_review_samples": _active_strategy_review_samples(entries),
        "active_strategy_evidence": {
            "source_counts": _active_strategy_source_counts(entries),
            "confidence_counts": _active_strategy_confidence_counts(entries),
            "seed_override_count": sum(
                1 for entry in entries if entry.active_strategy_source == "reference_seed"
            ),
        },
        "aggregate_counts": {
            "catalog_entry_count": len(entries),
            "brand_count": len(brand_ids),
            "etf_count": len(entries),
            **active_strategy_counts,
        },
    }


def _active_strategy_classification_counts(
    entries: tuple[SourceCatalogEntry, ...],
) -> dict[str, JsonValue]:
    return {
        "active_strategy": sum(1 for entry in entries if entry.is_active_strategy_etf is True),
        "passive_strategy": sum(
            1 for entry in entries if entry.is_active_strategy_etf is False
        ),
        "unknown_strategy": sum(
            1 for entry in entries if entry.is_active_strategy_etf is None
        ),
    }


def _active_strategy_review_samples(
    entries: tuple[SourceCatalogEntry, ...],
) -> list[dict[str, JsonValue]]:
    samples: list[dict[str, JsonValue]] = []
    for entry in entries:
        if entry.is_active_strategy_etf is not None:
            continue
        samples.append(
            {
                "source_provider_id": entry.source_provider_id,
                "brand_id": entry.brand_id,
                "etf_id": entry.etf_id,
                "active_strategy_source": entry.active_strategy_source,
                "active_strategy_confidence": entry.active_strategy_confidence,
            }
        )
        if len(samples) >= 5:
            break
    return samples


def _active_strategy_source_counts(
    entries: tuple[SourceCatalogEntry, ...],
) -> dict[str, JsonValue]:
    counts = {source: 0 for source in sorted(_ACTIVE_STRATEGY_SOURCES)}
    for entry in entries:
        counts[entry.active_strategy_source] += 1
    return counts


def _active_strategy_confidence_counts(
    entries: tuple[SourceCatalogEntry, ...],
) -> dict[str, JsonValue]:
    counts = {confidence: 0 for confidence in sorted(_ACTIVE_STRATEGY_CONFIDENCES)}
    for entry in entries:
        counts[entry.active_strategy_confidence] += 1
    return counts


def _source_provider_state_document(
    *,
    updated_at: str,
    etfs: Mapping[str, UniverseETFRecord],
    brands: Mapping[str, UniverseBrandRecord],
) -> dict[str, JsonValue]:
    return {
        "schema_version": UNIVERSE_STATE_SCHEMA_VERSION,
        "collection_source_type": "source_provider",
        "updated_at": updated_at,
        "etfs": [_etf_state_item(etfs[etf_id]) for etf_id in sorted(etfs)],
        "brands": [
            _brand_state_item(brands[brand_id])
            for brand_id in sorted(brands)
        ],
    }


def _read_source_catalog_entries(
    path: Path,
    *,
    expected_source_provider_id: str,
) -> tuple[SourceCatalogEntry, ...]:
    payload = _read_json_object(path, label="source catalog")
    if payload.get("schema_version") != SOURCE_CATALOG_SCHEMA_VERSION:
        raise SourceAcquisitionInputError("invalid source catalog schema")
    if payload.get("source_provider_id") != expected_source_provider_id:
        raise SourceAcquisitionInputError("source catalog provider id mismatch")
    if payload.get("complete") is not True:
        raise SourceAcquisitionInputError("source catalog must be complete")
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise SourceAcquisitionInputError("source catalog entries must be a list")
    return _validate_complete_catalog(
        SourceCatalogResult(
            source_provider_id=expected_source_provider_id,
            complete=True,
            entries=tuple(_source_catalog_entry_from_document(item) for item in entries),
        ),
        expected_source_provider_id=expected_source_provider_id,
    )


def _source_catalog_entry_from_document(value: object) -> SourceCatalogEntry:
    if not isinstance(value, Mapping):
        raise SourceAcquisitionInputError("source catalog entry must be an object")
    return SourceCatalogEntry(
        source_provider_id=_required_safe_text(
            value.get("source_provider_id"),
            "source_catalog.entry.source_provider_id",
        ),
        provider_etf_id=_required_safe_text(
            value.get("provider_etf_id"),
            "source_catalog.entry.provider_etf_id",
        ),
        etf_id=_required_safe_text(value.get("etf_id"), "source_catalog.entry.etf_id"),
        etf_name=_required_safe_text(
            value.get("etf_name"),
            "source_catalog.entry.etf_name",
        ),
        brand_id=_required_safe_text(
            value.get("brand_id"),
            "source_catalog.entry.brand_id",
        ),
        brand_name=_required_safe_text(
            value.get("brand_name"),
            "source_catalog.entry.brand_name",
        ),
        is_active_strategy_etf=_optional_bool(
            value.get("is_active_strategy_etf"),
            "source_catalog.entry.is_active_strategy_etf",
        ),
        active_strategy_source=_optional_safe_text(
            value.get("active_strategy_source"),
            "source_catalog.entry.active_strategy_source",
        )
        or "unknown",
        active_strategy_confidence=_optional_safe_text(
            value.get("active_strategy_confidence"),
            "source_catalog.entry.active_strategy_confidence",
        )
        or "low",
        strategy_label=_optional_safe_text(
            value.get("strategy_label"),
            "source_catalog.entry.strategy_label",
        ),
        locator=_optional_text(value.get("locator"), "source_catalog.entry.locator"),
    )


def _source_holdings_targets(
    *,
    catalog_entries: tuple[SourceCatalogEntry, ...],
    active_etf_ids: set[str],
    requested_date: str,
    provider_etf_ids: set[str] | None = None,
) -> list[HoldingsFetchTarget]:
    targets: list[HoldingsFetchTarget] = []
    selected_provider_etf_ids = set(provider_etf_ids or set())
    provider_query_date = _provider_query_date(requested_date)
    active_entries = [
        entry
        for entry in catalog_entries
        if entry.etf_id in active_etf_ids and entry.is_active_strategy_etf is True
    ]
    if selected_provider_etf_ids:
        selected_entries = _candidate_priority_entries(
            [
                entry
                for entry in active_entries
                if entry.provider_etf_id in selected_provider_etf_ids
            ]
        )
    else:
        selected_entries = _candidate_priority_entries(active_entries)
    for entry in selected_entries:
        targets.append(
            HoldingsFetchTarget(
                source_provider_id=entry.source_provider_id,
                provider_etf_id=entry.provider_etf_id,
                etf_id=entry.etf_id,
                requested_date=requested_date,
                provider_query_date=provider_query_date,
            )
        )
    return targets


def _candidate_priority_entries(
    entries: list[SourceCatalogEntry],
) -> list[SourceCatalogEntry]:
    return [
        entry
        for _, entry in sorted(
            enumerate(entries),
            key=lambda item: (_candidate_priority(item[1]), item[0]),
        )
    ]


def _candidate_priority(entry: SourceCatalogEntry) -> int:
    source = entry.active_strategy_source
    confidence = entry.active_strategy_confidence
    if source == "source_metadata" and confidence == "high":
        return 0
    if source == "reference_seed" and confidence == "high":
        return 1
    if source == "timefolio_provider_default":
        return 2
    if source == "name_token":
        return 3
    return 4


def _provider_query_date(requested_date: str) -> str:
    parsed = _parse_iso_date(requested_date)
    while parsed.weekday() >= 5:
        parsed -= timedelta(days=1)
    return parsed.isoformat()


def _apply_source_fetch_result(
    *,
    result: HoldingsFetchResult,
    target: HoldingsFetchTarget,
    entry: SourceCatalogEntry,
    stored: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
    next_stored: dict[tuple[str, str], list[dict[str, JsonValue]]],
    refresh_set: set[tuple[str, str]],
) -> tuple[str, list[dict[str, JsonValue]], str | None]:
    _validate_fetch_result_identity(result=result, target=target)
    if result.outcome != "fetched":
        return result.outcome, [], result.failure_code_class
    if result.observed_date is None:
        return "failed", [], "missing_observed_date"
    rows = _source_result_rows(result=result, entry=entry)
    if not rows:
        return "failed", [], "empty_holdings"
    key = (target.etf_id, result.observed_date)
    existing_rows = stored.get(key)
    if existing_rows is None:
        next_stored[key] = rows
        return "fetched", rows, None
    if _history_snapshot_fingerprint(existing_rows) == _history_snapshot_fingerprint(rows):
        return "skipped_existing", rows, None
    if key in refresh_set:
        next_stored[key] = rows
        return "refreshed", rows, None
    return "failed", rows, "refresh_required"


def _existing_exact_snapshot_rows(
    *,
    target: HoldingsFetchTarget,
    stored: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
    refresh_set: set[tuple[str, str]],
) -> list[dict[str, JsonValue]] | None:
    key = (target.etf_id, target.provider_query_date)
    if key in refresh_set:
        return None
    rows = stored.get(key)
    if not rows:
        return None
    return [dict(row) for row in rows]


def _validate_fetch_result_identity(
    *,
    result: HoldingsFetchResult,
    target: HoldingsFetchTarget,
) -> None:
    if result.source_provider_id != target.source_provider_id:
        raise SourceAcquisitionInputError("holdings result provider id mismatch")
    if result.provider_etf_id != target.provider_etf_id:
        raise SourceAcquisitionInputError("holdings result provider ETF id mismatch")
    if result.requested_date != target.requested_date:
        raise SourceAcquisitionInputError("holdings result requested date mismatch")
    if (
        result.provider_query_date is not None
        and result.provider_query_date != target.provider_query_date
    ):
        raise SourceAcquisitionInputError("holdings result provider query date mismatch")
    if result.etf_id and result.etf_id != target.etf_id:
        raise SourceAcquisitionInputError("holdings result ETF id mismatch")


def _source_result_rows(
    *,
    result: HoldingsFetchResult,
    entry: SourceCatalogEntry,
) -> list[dict[str, JsonValue]]:
    assert result.observed_date is not None
    _parse_iso_date(result.observed_date)
    rows: list[dict[str, JsonValue]] = []
    seen: set[str] = set()
    for line_number, holding in enumerate(result.holdings, 1):
        security_id = _required_safe_text(
            holding.get("security_id"),
            "source_holding.security_id",
        )
        if security_id in seen:
            raise SourceAcquisitionInputError(
                "duplicate source holding: "
                f"provider_etf_id={result.provider_etf_id} "
                f"observed_date={result.observed_date} security_id={security_id}"
            )
        seen.add(security_id)
        row: dict[str, JsonValue] = {
            "etf_id": entry.etf_id,
            "etf_name": entry.etf_name,
            "brand_id": entry.brand_id,
            "source_provider_id": entry.source_provider_id,
            "as_of_date": result.observed_date,
            "security_id": security_id,
            "ticker": _optional_safe_text(holding.get("ticker"), "source_holding.ticker"),
            "name": _required_safe_text(holding.get("name"), "source_holding.name"),
            "market": _optional_safe_text(holding.get("market"), "source_holding.market"),
            "sector": _optional_safe_text(holding.get("sector"), "source_holding.sector"),
            "theme": _optional_safe_text(holding.get("theme"), "source_holding.theme"),
            "country": _optional_safe_text(holding.get("country"), "source_holding.country"),
            "weight_percent": _required_number(
                holding.get("weight_percent"),
                "source_holding.weight_percent",
            ),
            "shares": _optional_number(holding.get("shares"), "source_holding.shares"),
            "market_value_krw": _optional_number(
                holding.get("market_value_krw"),
                "source_holding.market_value_krw",
            ),
            "price_krw": _optional_number(
                holding.get("price_krw"),
                "source_holding.price_krw",
            ),
            "is_cash": _required_bool(holding.get("is_cash"), "source_holding.is_cash"),
            "security_classification": _required_safe_text(
                holding.get("security_classification"),
                "source_holding.security_classification",
            ),
        }
        rows.append(
            _validate_normalized_row(
                row=row,
                partition_date=result.observed_date,
                line_number=line_number,
            )
        )
    return rows


def _target_outcome_item(
    *,
    target: HoldingsFetchTarget,
    entry: SourceCatalogEntry,
    result: HoldingsFetchResult,
    outcome: str,
    observed_date: str | None,
    row_count: int,
    failure_code_class: str | None,
    retry_plan: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    item: dict[str, JsonValue] = {
        "source_provider_id": target.source_provider_id,
        "brand_id": entry.brand_id,
        "etf_id": target.etf_id,
        "scope": "holdings_snapshot",
        "requested_date": target.requested_date,
        "observed_date": observed_date,
        "date_alignment": _date_alignment(
            requested_date=target.requested_date,
            provider_query_date=target.provider_query_date,
            observed_date=observed_date,
        ),
        "latest_upload_freshness": _latest_upload_freshness(
            requested_date=target.requested_date,
            provider_query_date=target.provider_query_date,
            observed_date=observed_date,
            outcome=outcome,
            row_count=row_count,
        ),
        "outcome": outcome,
        "row_count": row_count,
        "reason_code": failure_code_class,
        "retry_attempt_count": result.retry_attempt_count,
    }
    return item


def _cooldown_target_outcome_item(
    *,
    target: HoldingsFetchTarget,
    entry: SourceCatalogEntry,
    retry_plan: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    item: dict[str, JsonValue] = {
        "source_provider_id": target.source_provider_id,
        "brand_id": entry.brand_id,
        "etf_id": target.etf_id,
        "scope": "holdings_snapshot",
        "requested_date": target.requested_date,
        "observed_date": None,
        "date_alignment": _date_alignment(
            requested_date=target.requested_date,
            provider_query_date=target.provider_query_date,
            observed_date=None,
        ),
        "latest_upload_freshness": _latest_upload_freshness(
            requested_date=target.requested_date,
            provider_query_date=target.provider_query_date,
            observed_date=None,
            outcome="retry_cooldown",
            row_count=0,
        ),
        "outcome": "retry_cooldown",
        "row_count": 0,
        "reason_code": "cooldown_active",
        "retry_attempt_count": 0,
    }
    item.update(_target_backfill_projection(retry_plan))
    for field in ("blocked_until", "retry_after", "cooldown_remaining_seconds"):
        value = retry_plan.get(field)
        if _is_json_value(value):
            item[field] = value
    return item


def _date_alignment(
    *,
    requested_date: str,
    provider_query_date: str,
    observed_date: str | None,
) -> dict[str, JsonValue]:
    if observed_date is None:
        status = "missing_observed_date"
    elif observed_date != provider_query_date:
        status = "observed_differs_from_provider_query"
    elif provider_query_date != requested_date:
        status = "provider_query_adjusted"
    else:
        status = "matched"
    return {
        "requested_date": requested_date,
        "observed_date": observed_date,
        "status": status,
    }


def _latest_upload_freshness(
    *,
    requested_date: str,
    provider_query_date: str,
    observed_date: str | None,
    outcome: str,
    row_count: int,
) -> dict[str, JsonValue]:
    latest_acceptable = _previous_business_day(provider_query_date)
    if outcome not in {"fetched", "skipped_existing"} or row_count <= 0:
        status = "not_fetched"
    elif observed_date is None:
        status = "missing_observed_date"
    else:
        observed = _parse_iso_date(observed_date)
        requested = _parse_iso_date(requested_date)
        provider_query = _parse_iso_date(provider_query_date)
        if observed >= requested:
            status = "fresh_latest"
        elif observed > provider_query:
            status = "future_observed_date"
        elif observed < _parse_iso_date(latest_acceptable):
            status = "stale_latest"
        else:
            status = "fresh_latest"
    return {
        "status": status,
        "observed_date": observed_date,
        "latest_acceptable_observed_date": latest_acceptable,
    }


def _target_retry_plan(
    *,
    target: HoldingsFetchTarget,
    stored: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
    retry_state: Mapping[str, Mapping[str, JsonValue]],
    now: datetime,
) -> dict[str, JsonValue]:
    key = _retry_state_key(target)
    cooldown = retry_state.get(key)
    last_success = _last_successful_observed_date(target.etf_id, stored)
    missing_dates = _missing_business_dates(
        etf_id=target.etf_id,
        requested_date=target.requested_date,
        last_successful_observed_date=last_success,
        stored=stored,
    )
    plan: dict[str, JsonValue] = {
        "last_successful_observed_date": last_success,
        "observed_dates_missing": missing_dates,
        "next_backfill_date_count": len(missing_dates),
    }
    if cooldown is None:
        return plan
    blocked_until_value = cooldown.get("blocked_until")
    if not isinstance(blocked_until_value, str):
        return plan
    blocked_until = _parse_timestamp(blocked_until_value)
    remaining = int((blocked_until - now).total_seconds())
    if remaining <= 0:
        return plan
    plan.update(
        {
            "cooldown_active": True,
            "blocked_until": blocked_until_value,
            "retry_after": blocked_until_value,
            "cooldown_remaining_seconds": remaining,
            "reason_code": cooldown.get("reason_code")
            if isinstance(cooldown.get("reason_code"), str)
            else "cooldown_active",
        }
    )
    return plan


def _cooldown_retry_state_item(
    *,
    target: HoldingsFetchTarget,
    reason_code: str,
    now: datetime,
    retry_plan: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    blocked_until = (now + timedelta(seconds=DEFAULT_RETRY_COOLDOWN_SECONDS)).isoformat()
    return {
        "source_provider_id": target.source_provider_id,
        "etf_id": target.etf_id,
        "scope": "holdings_snapshot",
        "reason_code": reason_code,
        "blocked_until": blocked_until,
        "retry_after": blocked_until,
        "last_successful_observed_date": retry_plan.get("last_successful_observed_date"),
        "observed_dates_missing": retry_plan.get("observed_dates_missing", []),
        "next_backfill_date_count": retry_plan.get("next_backfill_date_count", 0),
    }


def _retry_plan_projection(
    *,
    cooldown: Mapping[str, JsonValue],
    retry_plan: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    projection = _target_backfill_projection(retry_plan)
    for field in ("blocked_until", "retry_after"):
        value = cooldown.get(field)
        if isinstance(value, str):
            projection[field] = value
    return projection


def _target_backfill_projection(
    retry_plan: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    projection: dict[str, JsonValue] = {}
    for field in (
        "last_successful_observed_date",
        "observed_dates_missing",
        "next_backfill_date_count",
    ):
        value = retry_plan.get(field)
        if _is_json_value(value):
            projection[field] = value
    return projection


def _last_successful_observed_date(
    etf_id: str,
    stored: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
) -> str | None:
    dates = [
        observed_date
        for stored_etf_id, observed_date in stored
        if stored_etf_id == etf_id
    ]
    if not dates:
        return None
    return max(dates, key=_parse_iso_date)


def _missing_business_dates(
    *,
    etf_id: str,
    requested_date: str,
    last_successful_observed_date: str | None,
    stored: Mapping[tuple[str, str], list[dict[str, JsonValue]]],
) -> list[str]:
    requested = _parse_iso_date(requested_date)
    if last_successful_observed_date is None:
        candidates = [requested]
    else:
        current = _parse_iso_date(last_successful_observed_date) + timedelta(days=1)
        candidates = []
        while current <= requested:
            if current.weekday() < 5:
                candidates.append(current)
            current += timedelta(days=1)
    return [
        candidate.isoformat()
        for candidate in candidates
        if (etf_id, candidate.isoformat()) not in stored
    ]


def _retry_state_key(target: HoldingsFetchTarget) -> str:
    return f"{target.source_provider_id}|{target.etf_id}|holdings_snapshot"


def _read_retry_state(
    path: Path,
) -> dict[str, dict[str, JsonValue]]:
    if not path.is_file():
        return {}
    payload = _read_json_object(path, label="source retry state")
    if payload.get("schema_version") != SOURCE_RETRY_STATE_SCHEMA_VERSION:
        return {}
    cooldowns = payload.get("cooldowns")
    if not isinstance(cooldowns, list):
        return {}
    state: dict[str, dict[str, JsonValue]] = {}
    for item in cooldowns:
        if not isinstance(item, Mapping):
            continue
        provider = item.get("source_provider_id")
        etf_id = item.get("etf_id")
        scope = item.get("scope")
        if (
            not isinstance(provider, str)
            or not isinstance(etf_id, str)
            or scope != "holdings_snapshot"
        ):
            continue
        state[f"{provider}|{etf_id}|{scope}"] = {
            key: value
            for key, value in item.items()
            if isinstance(key, str) and _is_json_value(value)
        }
    return state


def _write_retry_state(
    path: Path,
    *,
    updated_at: str,
    cooldowns: Mapping[str, Mapping[str, JsonValue]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SOURCE_RETRY_STATE_SCHEMA_VERSION,
        "updated_at": updated_at,
        "cooldowns": [
            dict(item)
            for _, item in sorted(cooldowns.items(), key=lambda pair: pair[0])
        ],
    }
    _write_json(path, payload)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _previous_business_day(date_value: str) -> str:
    parsed = _parse_iso_date(date_value) - timedelta(days=1)
    while parsed.weekday() >= 5:
        parsed -= timedelta(days=1)
    return parsed.isoformat()


def _previous_weekday_from_execution_date(
    now: Callable[[], datetime] | None,
) -> str:
    current = now() if now is not None else datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    execution_date = current.astimezone(KOREA_STANDARD_TIME).date()
    previous = execution_date - timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= timedelta(days=1)
    return previous.isoformat()


def _source_holdings_summary(
    *,
    source_provider_id: str,
    updated_at: str,
    requested_date: str,
    observed_dates: set[str],
    target_outcomes: list[dict[str, JsonValue]],
    written_row_count: int,
    written_snapshot_count: int,
) -> dict[str, JsonValue]:
    aggregate_counts = _target_aggregate_counts(target_outcomes)
    aggregate_counts["written_snapshot_count"] = written_snapshot_count
    aggregate_counts["row_count"] = written_row_count
    run_outcome = _run_outcome(aggregate_counts)
    return {
        "schema_version": SOURCE_ACQUISITION_SUMMARY_SCHEMA_VERSION,
        "source_provider_id": source_provider_id,
        "run_outcome": run_outcome,
        "updated_at": updated_at,
        "history_store": {"manifest_path": HOLDINGS_HISTORY_MANIFEST_FILENAME},
        "requested_dates": [requested_date],
        "observed_dates": sorted(observed_dates, key=_parse_iso_date, reverse=True),
        "target_outcomes": target_outcomes,
        "row_count": written_row_count,
        "written_snapshot_count": written_snapshot_count,
        "provider_rollout_status": _provider_rollout_status(target_outcomes),
        "warnings": _source_holdings_warnings(target_outcomes),
        "aggregate_counts": aggregate_counts,
    }


def _write_source_acquisition_summary(
    history_destination: Path,
    summary: Mapping[str, JsonValue],
) -> None:
    summary_path = history_destination / SOURCE_ACQUISITION_SUMMARY_FILENAME
    existing: Mapping[str, JsonValue] | None = None
    if summary_path.is_file():
        try:
            existing = _read_json_object(
                summary_path,
                label="source acquisition summary",
            )
        except SourceAcquisitionInputError:
            existing = None
    _write_json(summary_path, _source_acquisition_summary_for_store(existing, summary))


def _source_acquisition_summary_for_store(
    existing: Mapping[str, JsonValue] | None,
    current: Mapping[str, JsonValue],
) -> Mapping[str, JsonValue]:
    current_provider_id = current.get("source_provider_id")
    if not isinstance(current_provider_id, str):
        return current
    if not _is_source_acquisition_summary(existing):
        return current
    existing_results = _source_provider_summary_results(existing)
    if not existing_results:
        return current
    if (
        len(existing_results) == 1
        and existing_results[0].get("source_provider_id") == current_provider_id
    ):
        return current

    by_provider: dict[str, Mapping[str, JsonValue]] = {}
    for item in existing_results:
        provider_id = item.get("source_provider_id")
        if isinstance(provider_id, str):
            by_provider[provider_id] = item
    by_provider[current_provider_id] = _single_provider_summary(current)
    provider_results = list(by_provider.values())
    if len(provider_results) == 1:
        return provider_results[0]
    return _aggregate_source_provider_summaries(provider_results)


def _is_source_acquisition_summary(value: object) -> bool:
    return (
        isinstance(value, Mapping)
        and value.get("schema_version") == SOURCE_ACQUISITION_SUMMARY_SCHEMA_VERSION
    )


def _source_provider_summary_results(
    summary: Mapping[str, JsonValue] | None,
) -> list[Mapping[str, JsonValue]]:
    if not _is_source_acquisition_summary(summary):
        return []
    assert summary is not None
    provider_results = summary.get("provider_results")
    if isinstance(provider_results, list):
        results: list[Mapping[str, JsonValue]] = []
        for item in provider_results:
            if _is_source_acquisition_summary(item):
                results.append(_single_provider_summary(item))
        return results
    provider_id = summary.get("source_provider_id")
    if isinstance(provider_id, str) and provider_id != "multiple":
        return [_single_provider_summary(summary)]
    return []


def _single_provider_summary(
    summary: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    return {
        key: value
        for key, value in summary.items()
        if key not in {"provider_results", "source_provider_ids"}
        and _is_json_value(value)
    }


def _aggregate_source_provider_summaries(
    provider_results: Sequence[Mapping[str, JsonValue]],
) -> dict[str, JsonValue]:
    provider_ids = [
        str(item["source_provider_id"])
        for item in provider_results
        if isinstance(item.get("source_provider_id"), str)
    ]
    target_outcomes = [
        outcome
        for item in provider_results
        for outcome in _json_mapping_list(item.get("target_outcomes"))
    ]
    aggregate_counts = _target_aggregate_counts(target_outcomes)
    aggregate_counts["written_snapshot_count"] = sum(
        _int_value(item.get("written_snapshot_count")) for item in provider_results
    )
    aggregate_counts["row_count"] = sum(
        _int_value(item.get("row_count")) for item in provider_results
    )
    requested_dates = sorted(
        {
            date
            for item in provider_results
            for date in _json_text_list(item.get("requested_dates"))
        },
        key=_parse_iso_date,
        reverse=True,
    )
    observed_dates = sorted(
        {
            date
            for item in provider_results
            for date in _json_text_list(item.get("observed_dates"))
        },
        key=_parse_iso_date,
        reverse=True,
    )
    warnings = [
        warning
        for item in provider_results
        for warning in _json_mapping_list(item.get("warnings"))
    ]
    updated_values = [
        value for item in provider_results for value in (item.get("updated_at"),)
        if isinstance(value, str)
    ]
    return {
        "schema_version": SOURCE_ACQUISITION_SUMMARY_SCHEMA_VERSION,
        "source_provider_id": "multiple",
        "source_provider_ids": provider_ids,
        "run_outcome": _run_outcome(aggregate_counts),
        "updated_at": max(updated_values) if updated_values else None,
        "history_store": {"manifest_path": HOLDINGS_HISTORY_MANIFEST_FILENAME},
        "requested_dates": requested_dates,
        "observed_dates": observed_dates,
        "target_outcomes": target_outcomes,
        "row_count": aggregate_counts["row_count"],
        "written_snapshot_count": aggregate_counts["written_snapshot_count"],
        "provider_rollout_status": _aggregate_provider_rollout_status(provider_results),
        "warnings": warnings,
        "aggregate_counts": aggregate_counts,
        "provider_results": [dict(item) for item in provider_results],
    }


def _json_mapping_list(value: object) -> list[dict[str, JsonValue]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _json_text_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _int_value(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _aggregate_provider_rollout_status(
    provider_results: Sequence[Mapping[str, JsonValue]],
) -> str:
    statuses = {
        item.get("provider_rollout_status")
        for item in provider_results
        if isinstance(item.get("provider_rollout_status"), str)
    }
    if statuses == {"supported"}:
        return "supported"
    if "supported" in statuses:
        return "partial"
    if "active_holdings_failed" in statuses:
        return "active_holdings_failed"
    return "catalog_only"


def _provider_rollout_status(
    target_outcomes: list[dict[str, JsonValue]],
) -> str:
    if not target_outcomes:
        return "catalog_only"
    if any(_target_latest_smoke_succeeded(item) for item in target_outcomes):
        return "supported"
    if any(item.get("outcome") in {"fetched", "skipped_existing"} for item in target_outcomes):
        return "catalog_only"
    return "active_holdings_failed"


def _target_latest_smoke_succeeded(item: Mapping[str, JsonValue]) -> bool:
    freshness = item.get("latest_upload_freshness")
    return (
        item.get("outcome") in {"fetched", "skipped_existing"}
        and isinstance(freshness, Mapping)
        and freshness.get("status") == "fresh_latest"
    )


def _source_holdings_warnings(
    target_outcomes: list[dict[str, JsonValue]],
) -> list[dict[str, JsonValue]]:
    warnings: list[dict[str, JsonValue]] = []
    for item in target_outcomes:
        freshness = item.get("latest_upload_freshness")
        if not isinstance(freshness, Mapping):
            continue
        if freshness.get("status") != "stale_latest":
            continue
        warnings.append(
            {
                "code": "stale_latest_holdings",
                "severity": "warning",
                "source_provider_id": str(item["source_provider_id"]),
                "etf_id": str(item["etf_id"]),
                "scope": "holdings_snapshot",
                "observed_date": str(freshness["observed_date"]),
                "latest_acceptable_observed_date": str(
                    freshness["latest_acceptable_observed_date"]
                ),
            }
        )
    return warnings


def _target_aggregate_counts(
    target_outcomes: list[dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    counts: dict[str, JsonValue] = {
        "target_count": len(target_outcomes),
        "fetched": 0,
        "skipped_existing": 0,
        "failed": 0,
        "rate_limited": 0,
        "unsupported": 0,
    }
    for item in target_outcomes:
        outcome = str(item["outcome"])
        if outcome == "retry_cooldown":
            counts[outcome] = int(counts.get(outcome, 0)) + 1
        elif outcome in counts:
            counts[outcome] = int(counts[outcome]) + 1
    return counts


def _run_outcome(aggregate_counts: Mapping[str, JsonValue]) -> str:
    fetched = int(aggregate_counts.get("fetched", 0))
    skipped = int(aggregate_counts.get("skipped_existing", 0))
    failed_like = (
        int(aggregate_counts.get("failed", 0))
        + int(aggregate_counts.get("rate_limited", 0))
        + int(aggregate_counts.get("unsupported", 0))
        + int(aggregate_counts.get("retry_cooldown", 0))
    )
    if failed_like == 0:
        return "succeeded"
    if fetched > 0 or skipped > 0:
        return "partial"
    return "failed"


def _required_number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise SourceAcquisitionInputError(f"source acquisition field must be numeric: {label}")
    if not isinstance(value, int | float | str):
        raise SourceAcquisitionInputError(f"source acquisition field must be numeric: {label}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SourceAcquisitionInputError(
            f"source acquisition field must be numeric: {label}"
        ) from exc


def _optional_number(value: object, label: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _required_number(value, label)


def _required_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise SourceAcquisitionInputError(f"source acquisition field must be boolean: {label}")
    return value


def _optional_bool(value: object, label: str) -> bool | None:
    if value is None:
        return None
    return _required_bool(value, label)


def _required_safe_text(value: object, label: str) -> str:
    text = _optional_safe_text(value, label)
    if text is None:
        raise SourceAcquisitionInputError(f"source acquisition field is required: {label}")
    return text


def _optional_safe_text(value: object, label: str) -> str | None:
    text = _optional_text(value, label)
    if text is None:
        return None
    _ensure_path_safe_summary_text(text, label=label)
    return text


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceAcquisitionInputError(f"source acquisition field must be text: {label}")
    text = value.strip()
    return text or None


def _ensure_path_safe_summary_text(value: str, *, label: str) -> None:
    if "://" in value or "\\" in value or value.startswith("/"):
        raise SourceAcquisitionInputError(
            f"source acquisition field is not path-safe for summary: {label}"
        )


def _timestamp(now: Callable[[], datetime] | None) -> str:
    current = now() if now is not None else datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def _read_json_object(path: Path, *, label: str) -> dict[str, JsonValue]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SourceAcquisitionInputError(
            f"invalid JSON input for {label}: {path}: {exc.msg}"
        ) from exc
    except OSError as exc:
        raise SourceAcquisitionInputError(
            f"JSON input for {label} could not be read: {path}"
        ) from exc
    if not isinstance(data, dict):
        raise SourceAcquisitionInputError(f"JSON input for {label} must be an object")
    return data


def _write_json(path: Path, payload: Mapping[str, JsonValue]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False
