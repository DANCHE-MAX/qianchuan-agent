# Two-hour balance comparison image implementation plan

**Goal:** Deploy the approved three-bar balance comparison, preserving the original scheduler, configuration, financial state and feedback pause.

**Architecture:** Derive three two-hour rates from the same account's stored balance samples: current interval, immediately preceding interval, and previous calendar day's matching interval in Asia/Shanghai. Reuse validation for gaps, funding changes and frozen balance. Persist the view in the original event; send an image card with original mentions and a durable delivery receipt. No second scheduler.

**Steps:**
1. Back up runtime source/configuration and SQLite databases.
2. Add failing tests for exact interval selection, midnight, missing/zero/recharge, comparison denominators, send/readback retries and schedule integration.
3. Implement a pure comparison model, local PNG renderer and existing queue integration. Preserve unknown data and native configured mentions; no budget actions.
4. Run full suites and real local rendering. Deploy allowlisted sources and verify configuration hashes, running service and unchanged existing ledgers.
5. Document rules; scan known sensitive values and Git history, then push source/tests/docs only.
