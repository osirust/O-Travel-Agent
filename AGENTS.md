# O!Travel Agent

- Read README, PROJECT_STATE.md and docs/PRODUCT_DECISIONS.md before changing the application.
- Keep model-provider adapters independent from domain rules and travel-provider adapters.
- PostgreSQL stores canonical state, revisions, runs and idempotency. Keep external HTTP calls outside long database transactions.
- Validate typed model operations against the server schema and current trip version before applying them.
- Preserve explicit user pauses until an explicit resume command.
- Distinguish DEMO, SANDBOX, LIVE_INDICATIVE and LIVE_BOOKABLE. Do not label test data as confirmed inventory.
- Do not create real supplier bookings or payments. Checkout is a local simulation.
- Store credentials only in ignored local configuration. Do not commit keys, tokens or judge-only delivery envelopes.
- Use scoped, meaningful checks for changes and preserve previous failed evidence. Do not infer full semantic acceptance from metadata or a single successful example.
- Historical hackathon receipts describe their original commits and environments. Do not rewrite them to imply a later test run.
