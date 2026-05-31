from __future__ import annotations

import re

_KOREAN_INVESTMENT_RECOMMENDATION_PATTERN = re.compile(
    r"(매수|매도|목표가|투자\s*추천|진입\s*구간)"
)
_KOREAN_HOLD_RATING_OR_ALLOCATION_PATTERN = re.compile(
    r"(포트폴리오\s*비중|비중\s*확대\s*추천|투자\s*의견\s*보유|보유\s*(의견|등급|추천|유지))"
)

_PROHIBITED_INVESTMENT_LANGUAGE_PATTERNS = (
    (
        "buy_hold_sell",
        re.compile(r"(?<![A-Za-z])(buy|hold|sell)(?![A-Za-z])", re.IGNORECASE),
    ),
    (
        "price_target",
        re.compile(r"(price\s+target|target\s+price)", re.IGNORECASE),
    ),
    ("korean_investment_recommendation", _KOREAN_INVESTMENT_RECOMMENDATION_PATTERN),
    ("korean_hold_rating_or_allocation", _KOREAN_HOLD_RATING_OR_ALLOCATION_PATTERN),
)


def find_prohibited_investment_language(text: str) -> tuple[str, ...]:
    found: list[str] = []
    for category, pattern in _PROHIBITED_INVESTMENT_LANGUAGE_PATTERNS:
        if pattern.search(text) and category not in found:
            found.append(category)
    return tuple(found)
