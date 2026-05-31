from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from agent_treport.signal_report.domain.payload import SignalReportPayload, TickerDossier

REPORT_LEVEL_EVIDENCE_LABEL = "\ubcf4\uace0\uc11c \uc804\uccb4 \uadfc\uac70"
UNKNOWN_SIGNAL_LABEL = "\uad00\ub828 \uc2e0\ud638 \ubbf8\ud655\uc778"
UNKNOWN_USED_IN_LABEL = "\uad00\ub828 \uc704\uce58 \ubbf8\ud655\uc778"
UNUSED_EVIDENCE_LABEL = "\ubbf8\uc0ac\uc6a9"
UNKNOWN_TICKER_LABEL = "\ubbf8\ud655\uc778"

_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


@dataclass(frozen=True)
class EvidenceDisplayReference:
    claim_scope_display: Mapping[str, str]
    dossier_display: Mapping[str, str]

    @classmethod
    def from_payload(cls, payload: SignalReportPayload) -> EvidenceDisplayReference:
        return cls(
            claim_scope_display={
                signal.claim_scope: signal.display.claim_scope for signal in payload.signal_board
            },
            dossier_display={
                f"ticker_dossier:{_dossier_ref(dossier)}": (
                    f"Ticker Dossier: {display_ticker(dossier.ticker)}"
                )
                for dossier in payload.ticker_dossiers
            },
        )

    def claim_scope(self, claim_scope: str | None) -> str:
        if claim_scope is None:
            return REPORT_LEVEL_EVIDENCE_LABEL
        return self.claim_scope_display.get(claim_scope, UNKNOWN_SIGNAL_LABEL)

    def used_in(self, used_in: tuple[str, ...]) -> str:
        if not used_in:
            return UNUSED_EVIDENCE_LABEL
        rendered = [
            self.claim_scope_display.get(
                reference,
                self.dossier_display.get(reference, UNKNOWN_USED_IN_LABEL),
            )
            for reference in used_in
        ]
        return ", ".join(rendered)


def display_ticker(ticker: str | None) -> str:
    return ticker or UNKNOWN_TICKER_LABEL


def _dossier_ref(dossier: TickerDossier) -> str:
    if dossier.security_group_id is not None:
        return f"security_group:{dossier.security_group_id}"
    return f"security:{dossier.aggregation_key}"


def safe_ascii_slug(value: str | None, *, fallback: str) -> str:
    text = value or ""
    slug = _SLUG_UNSAFE.sub("-", text).strip("-").lower()
    return slug or fallback
