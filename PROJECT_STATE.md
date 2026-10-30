# O!Travel Agent - public portfolio snapshot

Updated: 30 October 2026.

## Scope

This repository contains the complete application, tests and supporting evidence imported from the team's official hackathon submission (`0d8d193`). The original source is `ostrovok-hackathon-2026/aegml`; the `submission` tag identifies that historical release. The public `main` adds portfolio documentation and does not claim a new runtime qualification.

Vladimir Ivanov owned backend/agent-harness development, integrations and automated testing. The frontend was developed by the team. This is a hackathon prototype; no commercial deployment, real bookings or competition victory is claimed.

## Verified engineering

- Canonical trip state, typed model operations, schema validation and bounded repair.
- Read-only answers based on canonical facts, with limits on model-proposed state changes.
- PostgreSQL revisions, durable runs/events, optimistic concurrency and idempotent simulated orders.
- Independent LLM and travel-provider adapters, explicit DEMO/SANDBOX/indicative source types.
- A frozen backend checkpoint with 7,968 successful checks; clean Compose and external-wire checks are separate evidence.
- 52 frontend tests and production build at the last frontend checkpoint.

These results are scoped: the 40-dialogue and 106-scenario suites are not represented as a complete semantic PASS. External availability and quotas are outside a local health check.

## Public setup

Follow README: copy `.env.example` to `.env`, provide your own supported LLM/travel API credentials and run `docker compose up --build`. The interface is at http://localhost:8080. Forms and explicit DEMO workflows are available without external API keys.

Working credentials are not included in the public tree. The judge-only encrypted envelope is excluded from public `main`. Original historical Git objects retain the encrypted artifact; the publication check found no plaintext working credentials. The public recipient key alone cannot decrypt the envelope.

The old demonstration video has been removed by its owner; screenshots remain as examples. No future execution, service startup or automation is requested by this state file.
