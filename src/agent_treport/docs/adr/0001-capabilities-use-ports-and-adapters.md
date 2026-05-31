# Capabilities Use Ports And Adapters

Agent TReport domain capabilities must depend on capability-owned ports instead of concrete provider clients, while integration adapters implement those ports and are injected by a thin composition layer. This keeps finance and reporting capabilities reusable across workflows and future projects without dragging along yfinance, Google Finance, Telegram, Threads, cache, or workflow-specific dependencies.

Rejected alternatives were embedding provider clients directly inside capabilities or letting workflows perform all provider calls. Direct imports are faster initially but create provider lock-in, while workflow-owned calls make workflows thick and prevent capability reuse.
