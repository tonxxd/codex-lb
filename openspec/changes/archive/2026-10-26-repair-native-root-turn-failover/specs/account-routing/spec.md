## ADDED Requirements

### Requirement: Native root-turn telemetry permits auth recovery

The proxy SHALL treat `client_metadata.root_turn_id` as client-only telemetry
when evaluating an otherwise self-contained unanchored HTTP request for
pre-visible recovery from an unavailable account.

#### Scenario: Current native Codex conversation recovers from a revoked account
- **GIVEN** a complete native Responses-Lite transcript omits top-level instructions and includes root-turn telemetry
- **WHEN** its selected account returns `token_revoked` before visible output
- **THEN** the proxy SHALL replay the visible transcript on another eligible account
- **AND** subsequent requests SHALL bypass the unavailable account
- **AND** unknown account-owned state, file ownership and explicit response anchors SHALL retain their existing restrictions
