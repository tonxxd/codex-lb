## 1. Implementation

- [x] 1.1 Add validated Claude credentials and source selection/catalog support.
- [x] 1.2 Translate Responses history, function/custom/namespace tools, and images.
- [x] 1.3 Translate Messages streaming and non-streaming output and usage.
- [x] 1.4 Implement refresh claims, guarded token persistence, cooldowns, and failover.
- [x] 1.5 Add the Claude pool import action to existing source settings.

## 2. Verification

- [x] 2.1 Verify canonical/backend routes, catalogs, source assignment, and secrets.
- [x] 2.2 Verify tool round trips, concurrent refresh, failure, and cancellation.
- [x] 2.3 Run 537 backend and 20 frontend tests, build, lint, typing, and architecture checks.
- [x] 2.4 Validate the OpenSpec change.
- [x] 2.5 Qualify real Opus 5.5 through Codex CLI and the database-backed HTTP routes.
- [x] 2.6 Qualify Linux token rotation, tool round trips, and tool-result vision in the runtime image.
- [x] 2.7 Sync stable context and archive the verified change.
