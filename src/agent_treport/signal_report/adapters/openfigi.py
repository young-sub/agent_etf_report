from __future__ import annotations

import importlib
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent_pack.models import JsonValue

OPENFIGI_DOCS_URL = "https://www.openfigi.com/api/documentation"
OPENFIGI_DOCS_REVIEWED_DATE = "2026-05-14"
OPENFIGI_MAPPING_URL = "https://api.openfigi.com/v3/mapping"
DEFAULT_OPENFIGI_BATCH_SIZE = 50
UNAUTHENTICATED_OPENFIGI_BATCH_SIZE = 10
DEFAULT_OPENFIGI_MIN_INTERVAL_SECONDS = 1.0
DEFAULT_OPENFIGI_MAX_REQUESTS = 20


class OpenFigiRateLimitError(RuntimeError):
    """Raised when OpenFIGI returns HTTP 429."""


class OpenFigiRequestError(RuntimeError):
    """Raised when OpenFIGI I/O or response parsing fails safely."""


class OpenFigiClient(Protocol):
    def post_mapping(self, jobs: list[dict[str, JsonValue]]) -> object:
        """Submit OpenFIGI mapping jobs and return decoded response data."""


@dataclass(frozen=True)
class OpenFigiLookupResult:
    mappings: dict[str, dict[str, JsonValue]]
    warnings: list[dict[str, JsonValue]]
    request_count: int


class RequestsOpenFigiClient:
    def __init__(self, *, api_key: str | None, session: Any | None = None) -> None:
        requests = importlib.import_module("requests")
        self._session = session or requests.Session()
        self._api_key = api_key
        self.max_jobs_per_request = (
            DEFAULT_OPENFIGI_BATCH_SIZE
            if api_key
            else UNAUTHENTICATED_OPENFIGI_BATCH_SIZE
        )

    def post_mapping(self, jobs: list[dict[str, JsonValue]]) -> object:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["X-OPENFIGI-APIKEY"] = self._api_key
        try:
            response = self._session.post(
                OPENFIGI_MAPPING_URL,
                json=jobs,
                headers=headers,
                timeout=30,
            )
        except Exception as exc:
            raise OpenFigiRequestError("OpenFIGI request failed") from exc
        status_code = int(getattr(response, "status_code", 0))
        if status_code == 429:
            raise OpenFigiRateLimitError("OpenFIGI rate limit reached")
        if status_code < 200 or status_code >= 300:
            raise OpenFigiRequestError(f"OpenFIGI request failed with status {status_code}")
        try:
            return response.json()
        except Exception as exc:
            raise OpenFigiRequestError("OpenFIGI response was not valid JSON") from exc


def create_openfigi_client_from_env() -> RequestsOpenFigiClient:
    dotenv = importlib.import_module("dotenv")
    project_env = Path.cwd() / ".env"
    dotenv.load_dotenv(project_env)
    api_key = os.environ.get("OPENFIGI_API_KEY") or None
    return RequestsOpenFigiClient(api_key=api_key)


def lookup_openfigi_tickers(
    *,
    security_ids: Sequence[str],
    client: OpenFigiClient,
    batch_size: int = DEFAULT_OPENFIGI_BATCH_SIZE,
    min_interval_seconds: float = DEFAULT_OPENFIGI_MIN_INTERVAL_SECONDS,
    max_requests: int = DEFAULT_OPENFIGI_MAX_REQUESTS,
    sleep: Callable[[float], object] = time.sleep,
) -> OpenFigiLookupResult:
    mappings: dict[str, dict[str, JsonValue]] = {}
    warnings: list[dict[str, JsonValue]] = []
    request_count = 0
    unique_ids = list(dict.fromkeys(security_ids))
    for batch_start in range(0, len(unique_ids), batch_size):
        if request_count >= max_requests:
            warnings.append(
                {
                    "code": "openfigi_request_limit_reached",
                    "message": "OpenFIGI request limit reached; lookup stopped for this run.",
                }
            )
            break
        batch_ids = unique_ids[batch_start : batch_start + batch_size]
        jobs = [{"idType": "ID_ISIN", "idValue": security_id} for security_id in batch_ids]
        if request_count > 0 and min_interval_seconds > 0:
            sleep(min_interval_seconds)
        try:
            response = client.post_mapping(jobs)
        except OpenFigiRateLimitError:
            warnings.append(
                {
                    "code": "openfigi_rate_limited",
                    "message": "OpenFIGI rate limit reached; lookup stopped for this run.",
                }
            )
            break
        except OpenFigiRequestError:
            warnings.append(
                {
                    "code": "openfigi_request_failed",
                    "message": "OpenFIGI lookup failed; lookup stopped for this run.",
                }
            )
            break
        request_count += 1
        if not isinstance(response, list):
            warnings.append(
                {
                    "code": "openfigi_invalid_response",
                    "message": "OpenFIGI response shape was invalid; lookup stopped for this run.",
                }
            )
            break
        for security_id, item in zip(batch_ids, response, strict=False):
            mapping = _unambiguous_equity_mapping(item, security_id=security_id)
            if mapping is not None:
                mappings[security_id] = mapping
    return OpenFigiLookupResult(
        mappings=mappings,
        warnings=warnings,
        request_count=request_count,
    )


def _unambiguous_equity_mapping(
    item: object, *, security_id: str
) -> dict[str, JsonValue] | None:
    if not isinstance(item, Mapping):
        return None
    data = item.get("data")
    if not isinstance(data, list):
        return None
    candidates = [
        mapping
        for candidate in data
        if (mapping := _equity_candidate_mapping(candidate)) is not None
    ]
    if len(candidates) == 1:
        return candidates[0]
    preferred_exchange = _preferred_exchange_for_isin(security_id)
    if preferred_exchange is not None:
        preferred_candidates = [
            candidate
            for candidate in candidates
            if str(candidate.get("exchange")).upper() == preferred_exchange
        ]
        if len(preferred_candidates) == 1:
            return preferred_candidates[0]
    return None


def _equity_candidate_mapping(candidate: object) -> dict[str, JsonValue] | None:
    if not isinstance(candidate, Mapping):
        return None
    ticker = candidate.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        return None
    market_sector = _text(candidate.get("marketSector"))
    security_type2 = _text(candidate.get("securityType2"))
    security_type = _text(candidate.get("securityType"))
    if market_sector != "equity":
        return None
    if security_type2 in {"option", "warrant"} or security_type in {"equity option"}:
        return None
    return {
        "ticker": ticker.strip(),
        "name": _nullable_text(candidate.get("name")),
        "exchange": _nullable_text(candidate.get("exchCode")),
        "source": "openfigi",
    }


def _preferred_exchange_for_isin(security_id: str) -> str | None:
    normalized = security_id.strip().upper()
    if normalized.startswith("US") and len(normalized) == 12:
        return "US"
    return None


def _text(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _nullable_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
