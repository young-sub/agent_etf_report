# Use Two Reference Roles

Agent TReport will use `ETF_tracker-main` as the breadth and operations reference, and `Agent_TReport-main` as the depth and product-quality reference. This avoids blindly porting either legacy codebase: ETF Tracker contributes ETF universe management, cumulative data, security normalization, delivery hardening, and eval discipline, while Agent TReport contributes high-density analysis, product surfaces, personas, social workflows, and reporting quality.

The rejected alternatives were making ETF Tracker the primary architecture and losing report density, or making Agent TReport the primary architecture and delaying broad ETF operations and production hardening.
