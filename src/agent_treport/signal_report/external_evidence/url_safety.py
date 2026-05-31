from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_API_HOSTS = {
    "www.alphavantage.co",
    "alphavantage.co",
    "newsapi.org",
    "openapi.naver.com",
    "finnhub.io",
    "api.finnhub.io",
    "data.sec.gov",
    "engopendart.fss.or.kr",
    "opendart.fss.or.kr",
}
_SENSITIVE_QUERY_PARTS = (
    "api",
    "apikey",
    "api_key",
    "authorization",
    "client_secret",
    "crtfc_key",
    "key",
    "secret",
    "signature",
    "token",
)


def safe_public_url(url: str | None, *, provider_id: str) -> str | None:
    _ = provider_id
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.netloc.lower()
    if host in _API_HOSTS and not _is_allowed_public_provider_url(parsed):
        return None
    if "openapi.naver.com/l" in url:
        return None
    if not parsed.query:
        return urlunparse(parsed._replace(fragment=""))

    query = parse_qsl(parsed.query, keep_blank_values=True)
    if any(_is_sensitive_query_key(key) for key, _value in query):
        return None
    if any(key.lower().startswith("utm_") for key, _value in query):
        return None
    if _is_dart_viewer(parsed):
        allowed = [(key, value) for key, value in query if key == "rcpNo" and value.isdigit()]
        if len(allowed) == 1:
            return urlunparse(parsed._replace(query=urlencode(allowed), fragment=""))
    return None


def _is_sensitive_query_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_QUERY_PARTS)


def _is_allowed_public_provider_url(parsed) -> bool:
    return _is_dart_viewer(parsed) or _is_sec_archive(parsed)


def _is_dart_viewer(parsed) -> bool:
    host = parsed.netloc.lower()
    return host in {"dart.fss.or.kr", "englishdart.fss.or.kr"} and parsed.path.endswith(
        "/main.do"
    )


def _is_sec_archive(parsed) -> bool:
    return parsed.netloc.lower() == "www.sec.gov" and parsed.path.startswith(
        "/Archives/edgar/"
    )
