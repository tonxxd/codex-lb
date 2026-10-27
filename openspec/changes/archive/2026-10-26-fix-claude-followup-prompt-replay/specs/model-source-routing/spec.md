## ADDED Requirements

### Requirement: Claude follow-up instructions remain the active prompt

When the final projected user message contains completed tool results followed
by additional user content, the native Claude runtime SHALL seed history through
the last tool result and send all subsequent content as the active prompt.
It SHALL preserve content order and supported text and image blocks. When no
content follows the last tool result, it SHALL retain result-only continuation.

#### Scenario: Codex compacts after reading a chat
- **WHEN** a completed chat-read tool result is followed by a compaction instruction
- **THEN** the compaction instruction is the active native prompt
- **AND** the completed tool result remains paired with its original call

#### Scenario: User attaches an image after a tool result
- **WHEN** text and an image follow the last completed tool result
- **THEN** both blocks remain in the active prompt in their original order

#### Scenario: Only completed tool results are supplied
- **WHEN** the final user message ends at a completed tool result
- **THEN** the runtime resumes using its existing result-only continuation prompt

### Requirement: Claude compaction has a bounded summary generation

When Codex turn metadata explicitly marks a Claude Responses request as
compaction, the proxy SHALL disable thinking for that summary utility if the
API key has no explicit reasoning policy. It SHALL preserve explicit output
limits, normal-turn reasoning selections, other providers, and API-key enforced
reasoning policies. Client telemetry SHALL remain stripped before forwarding.

#### Scenario: Maximum reasoning is selected for the investigation
- **WHEN** an unpinned Claude request is tagged as compaction with max reasoning
- **THEN** its summary generation has thinking disabled
- **AND** subsequent normal requests retain the selected max reasoning

#### Scenario: The API key enforces a reasoning policy
- **WHEN** a Claude compaction request uses an API key with an explicit reasoning policy
- **THEN** the existing reasoning policy remains effective
