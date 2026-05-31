from __future__ import annotations

import re
from typing import Literal

from agent_pack.models import RuntimeModel

from agent_treport.signal_report.domain.investment_language_policy import (
    find_prohibited_investment_language,
)

CommentaryPolicyStatus = Literal["included", "omitted"]
CommentaryPolicyReason = Literal["safe", "empty", "prohibited_or_canonical_conflict"]

OMITTED_MODEL_COMMENTARY_MESSAGE = (
    "모델 코멘터리는 report commentary policy 위반으로 생략되었습니다."
)

_CANONICAL_CONFLICT_PATTERNS = (
    re.compile(
        r"\b(score|review_label|label|evidence[_ -]?grade|data[- ]quality)\b"
        r"[^.]{0,80}\b(should|must|needs?\s+to)\b"
        r"[^.]{0,80}\b(change\w*|adjust\w*|override\w*|replace\w*|raise|lower)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(score|review_label|label|evidence[_ -]?grade|data[- ]quality)\b"
        r"[^.]{0,80}\b(change\w*|adjust\w*|override\w*|replace\w*)\b",
        re.IGNORECASE,
    ),
    re.compile(r"(점수|review_label|라벨|근거\s*등급|데이터\s*품질).{0,40}(수정|변경|조정|덮어쓰기|무시)"),
)


class CommentaryPolicyResult(RuntimeModel):
    status: CommentaryPolicyStatus
    reason: CommentaryPolicyReason
    text: str | None


def evaluate_model_commentary(commentary: str | None) -> CommentaryPolicyResult:
    text = (commentary or "").strip()
    if not text:
        return CommentaryPolicyResult(status="included", reason="empty", text=None)
    if _violates_policy(text):
        return CommentaryPolicyResult(
            status="omitted",
            reason="prohibited_or_canonical_conflict",
            text=None,
        )
    return CommentaryPolicyResult(status="included", reason="safe", text=text)


def commentary_policy_summary(result: CommentaryPolicyResult) -> dict[str, str]:
    return {"status": result.status, "reason": result.reason}


def _violates_policy(text: str) -> bool:
    return bool(find_prohibited_investment_language(text)) or any(
        pattern.search(text) for pattern in _CANONICAL_CONFLICT_PATTERNS
    )
