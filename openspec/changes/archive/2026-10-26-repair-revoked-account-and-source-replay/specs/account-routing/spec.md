## ADDED Requirements

### Requirement: Explicitly revoked access tokens stop routing

An upstream `token_revoked` error MUST mark the affected account as requiring
reauthentication and MUST make its stored access token ineligible even when the
JWT expiration is in the future. Fresh credential import MUST restore normal
eligibility. Refresh-token-only warnings MUST retain their existing behavior.

#### Scenario: Rejected token is not retried
- **WHEN** an unanchored HTTP Responses request receives `token_revoked` before visible output
- **AND** its input is self-contained and not bound to an input file or explicit continuation
- **THEN** the proxy retries it on a different eligible account within its original budget
- **AND** later requests do not select the rejected credentials
- **AND** usage reservation settlement precedes account-health persistence

#### Scenario: Native Codex namespace history remains replayable
- **GIVEN** a complete unanchored transcript includes Codex namespace tool declarations and completed local tool calls
- **WHEN** the access token is explicitly rejected before visible output
- **THEN** the replay retains the namespace tool declarations and visible transcript
- **AND** omits old reasoning ciphertext, response IDs and message telemetry
- **AND** rejects unknown namespace fields or hosted account-owned state

#### Scenario: Stored objects remain owned
- **WHEN** a request requires a stored response, explicit turn state, or file owned by the revoked account
- **THEN** the proxy does not move the stored object to another account
- **AND** it does not replay any request after visible output
