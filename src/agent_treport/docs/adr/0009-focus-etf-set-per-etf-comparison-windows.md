# Operational readiness uses FocusETFSet with per-ETF comparison windows

Accepted. Operational readiness uses a user-selected FocusETFSet and allows each included ETF to use its own latest current and previous holdings snapshots, rather than requiring one single focus ETF or one shared current/prior date across every ETF. This prevents provider-specific publication timing, temporary blocked providers, pre-listing no-data dates, or one ETF window gap from stalling the whole live operational handoff when at least three focus ETFs remain eligible.

Considered options were keeping the single focus ETF contract, forcing one shared current/prior comparison window, and producing separate provider-specific reports. The FocusETFSet contract keeps one multi-ETF SignalIntelligenceReport while making provider availability and mixed comparison windows explicit readiness and data-quality evidence.
