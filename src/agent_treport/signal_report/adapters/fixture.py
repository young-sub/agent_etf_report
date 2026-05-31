from __future__ import annotations

import json
from pathlib import Path

from agent_treport.signal_report.domain.evidence import EvidenceItemInput
from agent_treport.signal_report.domain.inputs import SignalReportInputs
from agent_treport.signal_report.domain.snapshots import MultiETFHoldingsSnapshots

_FIXTURE_ROOT = Path(__file__).parents[2] / "fixtures" / "signal_report"


def load_fixture_signal_report_inputs(
    *,
    holdings_path: str | Path | None = None,
    evidence_path: str | Path | None = None,
    focus_etf_id: str | None = None,
) -> SignalReportInputs:
    holdings_data = json.loads(
        Path(holdings_path or _FIXTURE_ROOT / "holdings.json").read_text(encoding="utf-8")
    )
    evidence_data = json.loads(
        Path(evidence_path or _FIXTURE_ROOT / "evidence.json").read_text(encoding="utf-8")
    )
    return SignalReportInputs(
        snapshots=MultiETFHoldingsSnapshots.model_validate(holdings_data["snapshots"]),
        focus_etf_id=(
            focus_etf_id if focus_etf_id is not None else holdings_data.get("focus_etf_id")
        ),
        evidence=tuple(EvidenceItemInput.model_validate(item) for item in evidence_data),
    )
