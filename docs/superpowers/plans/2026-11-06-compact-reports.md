# Compact daily interpretation and balance notices

**Goal:** Implement the approved mobile image summary and one/two sentence balance notices without changing account configuration or trading behavior.

**Architecture:** Use the exact dated profit report as the source of deterministic summary amounts, headline, coverage and two focus rows. Render PNG with the existing locally configured Python/Pillow/font; reply in the originating thread with durable per-message image delivery state. Keep detailed follow-up analysis available as text. Change balance presentation only, retaining the existing two-hour sampling/scheduling and deduplication.

**Tasks:**
- [x] Add failing tests for image summary/unknown data, original-thread delivery/retry safety and concise normal/low/unknown balance.
- [x] Implement summary model, image renderer and reply integration. Retain missing-date response and conversation date boundaries.
- [x] Simplify balance templates without changing calculations, schedule or thresholds; warn for estimated runway below the next two-hour check.
- [x] Run the complete suites, real synthetic PNG rendering and production-data local preview without sending test messages.
- [x] Back up before replacing allowlisted runtime source; preserve config/database/feedback pause; restart and verify.
- [ ] Document rules, scan sensitive values and full Git history, then push to the existing private repository.

**Validation:** Synthetic fixtures only in Git. Local previews and receipts remain ignored. Confirm unknown amounts never become zero and ambiguous image sends are not blindly retried.
