## ADDED Requirements

### Requirement: Claude subscription runtime
The system SHALL use the native Claude Agent SDK with the selected subscription
credential and isolated temporary configuration. It SHALL disable API-key and
other billed-provider fallbacks, host plugins, and native built-in tools. A
server-controlled hook SHALL deny all server tool execution. Codex client tool
definitions SHALL be exposed through inert MCP adapters, and their calls SHALL
be returned to Codex for client-side execution.

#### Scenario: Opus requests a coding tool
- **WHEN** the native Claude runtime generates a tool call
- **THEN** the call is translated into a Responses client tool call
- **AND** no command or client tool is executed by the hosted runtime

#### Scenario: A tool result continues the conversation
- **WHEN** Codex supplies prior conversation history and completed tool results
- **THEN** the native runtime receives their original message roles and call IDs
- **AND** temporary native configuration and transcript files are removed

#### Scenario: Client disconnects while the worker reports an error
- **WHEN** a client closes the transport while the bounded output queue is full
- **THEN** token-save cleanup completes and the worker terminates
- **AND** the native subprocess and temporary files are released

#### Scenario: Unsupported native request controls
- **WHEN** a client requests forced/required tool choice, serial tools, explicit
  sampling parameters, or structured output
- **THEN** the request fails explicitly instead of silently changing the control

### Requirement: Native Claude subscription source
The system SHALL expose configured Anthropic source models through the existing
model catalogs and HTTP Responses routes, with the same API-key source assignment,
admission, reservation settlement, and enablement rules as other sources. Claude
credentials SHALL only be sent to the configured official Anthropic endpoints and
SHALL never appear in dashboard reads, model catalogs, logs, or public errors.

#### Scenario: Operator imports a Claude account pool
- **WHEN** an authorized operator creates an Anthropic source with valid accounts
- **THEN** credentials are encrypted and only account metadata is returned
- **AND** enabled models are selectable through the existing model catalog

### Requirement: Claude protocol translation
The system SHALL translate self-contained Codex Responses history and declarations
to Anthropic Messages while preserving function/custom tool identity, parallel
tool outputs, text, and supported image inputs. Opaque foreign reasoning SHALL
not be forwarded. Unsupported server-owned response/file references SHALL fail
explicitly.

#### Scenario: Codex continues after a Claude tool call
- **WHEN** Codex submits a tool result for a prior translated tool call
- **THEN** Claude receives the matching tool-use and tool-result identifiers
- **AND** the next response can finish normally

### Requirement: Claude response lifecycle
The system SHALL translate Claude text, tools, reasoning summaries, usage,
incomplete output, and errors to valid Responses events. The server SHALL NOT
execute client tools or replay a generation after client-visible content.

#### Scenario: Claude fails after output begins
- **WHEN** Claude emits an upstream error after output reached the client
- **THEN** the stream ends with a sanitized failure event
- **AND** the generation is not retried on another account

### Requirement: Claude pool recovery
The system SHALL refresh expiring Claude credentials under an exclusive bounded
database claim, persist rotating tokens without overwriting newer credentials,
exclude accounts requiring reauthentication, and respect account cooldowns.
Authentication rejection, rate limiting, or transient upstream failure before
content SHALL permit another eligible Claude account. Exhaustion SHALL return
an explicit source error and SHALL NOT route the request to GPT accounts.

#### Scenario: First account is rate limited
- **WHEN** the first eligible Claude account returns HTTP 429 before content
- **THEN** it enters cooldown and the next eligible Claude account is attempted
- **AND** exhaustion returns an explicit rate-limit response with a retry delay

#### Scenario: Refresh overlaps with another worker
- **WHEN** a worker holds an unexpired refresh claim
- **THEN** another worker does not exchange that account's refresh token
- **AND** stale refresh results cannot overwrite an operator's replacement tokens
