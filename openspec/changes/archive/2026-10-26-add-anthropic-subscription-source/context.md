# Design

One Anthropic model source owns an ordered pool. Its existing encrypted credential
blob contains a versioned typed pool document instead of an OpenAI API key. Public
schemas expose account metadata only. Atomic ciphertext compare-and-set writes
coordinate startup claims and state across workers without holding a transaction
across inference. The source URL is fixed to https://api.anthropic.com/v1.

The Responses boundary stays unchanged. For example, an Opus function call is
returned as a Responses function_call, Codex executes it locally, and the next
request maps its function_call_output to a Messages tool_result. Namespace and
custom tools need reversible name/schema mappings because the two APIs have
different declaration formats. Provider-owned encrypted reasoning is dropped;
server-owned IDs with no transcript cannot be recovered.

## Native subscription runtime

Inference uses the official Python Claude Agent SDK, pinned to 0.2.174 and its
bundled Claude Code 2.1.310. The SDK runs one generation in a private temporary
directory with a private credential file. It clears API-key and alternative
provider environment settings, ignores host settings, disables native tools,
and exposes Codex tool definitions through inert MCP adapters. A PreToolUse
hook denies every server-side tool execution. Generation stops at the first
native message_stop, so Codex executes requested tools and submits their results.

The SDK context and subprocess are owned by one worker task for their complete
lifetime. A bounded queue connects that task to the HTTP response task. Closing
the transport cancels and joins the worker, saves rotating credentials, releases
its database claim, and deletes temporary files. Cancellation received during
token-save cleanup is re-raised after persistence, including when the output
queue is full.

The native CLI restores JSONL conversation records rather than flattened text.
For completed tool calls, the transcript includes both the assistant call and
the client's actual tool result before continuation begins. The transcript codec
uses the pinned SDK's CLI-version and project-directory helpers. Dependency
upgrades must repeat the real Codex tool round trip and image/history checks.

Every native authentication startup acquires a 60-second database lease. The
first-frame deadline is 30 seconds. Rotated credentials are saved before the
first model frame is exposed, then the lease is released. Token writes compare
the original credentials and lease ownership so a stale worker cannot overwrite
an operator replacement. A second callback saves any final native rotation.

## Supported boundary and operations

The native adapter supports auto/none tool choice and parallel client tools.
Forced/required choice, serial-tool enforcement, explicit sampling parameters,
structured output, and provider-owned response/file references fail explicitly.
Reasoning effort is passed to the SDK; none/minimal disable thinking. OpenAI
hosted tools are omitted by the existing source-routing projection.

Importing a claude-lb file transfers the active credential pool to the hosted
source. Stop local consumers of those same refresh tokens before activation;
the hosted database owns subsequent rotations. The local qualification harness
writes rotations back to the original file so an ephemeral test does not lose
a single-use refresh token. Operators can replace accounts in source settings
when sign-in is required. No new hosted browser login flow is added.

Sources:
- https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers
- https://platform.claude.com/docs/en/build-with-claude/streaming
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
- https://code.claude.com/docs/en/agent-sdk/python
- https://code.claude.com/docs/en/agent-sdk/session-storage

## Qualification

On 2026-10-26, real Opus 5.5 inference passed through the new native runtime.
A Linux container also passed a complete function-call/result round trip with
forced token refresh and durable rotation back to the owning pool. An actual
Codex CLI session passed through the isolated database-backed LB HTTP route:
Opus requested a local shell tool, Codex executed it, and Opus used the returned
marker. Canonical non-streaming /v1/responses passed as well. The native CLI
default request uses auto tool choice and parallel tools, matching the adapter.

A temporary isolated SDK install on the actual Railway x86_64 host also passed
the native tool/result round trip with vision under the non-root app user.
That probe used an unexpired access credential without the owning refresh token;
the deployed application, service configuration, and production database were
not changed. Local x86 emulation crashed during inference, so qualification uses
the physical host result rather than the emulator.

The earlier direct Python Messages experiment returned HTTP 429 despite available
quota and was replaced. Native CLI 2.1.220 could use Opus 4.8 but was too old for
Opus 5.5; the pinned bundled runtime satisfies its minimum 2.1.280 requirement.
No credential values are recorded in verification artifacts.
