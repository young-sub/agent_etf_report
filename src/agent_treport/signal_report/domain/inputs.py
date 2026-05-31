from __future__ import annotations

from agent_pack.models import RuntimeModel

from agent_treport.signal_report.domain.evidence import EvidenceItemInput
from agent_treport.signal_report.domain.snapshots import MultiETFHoldingsSnapshots


class SignalReportInputs(RuntimeModel):
    snapshots: MultiETFHoldingsSnapshots
    focus_etf_id: str | None
    focus_etf_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceItemInput, ...]
