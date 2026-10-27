# Model Source Routing — Context

## Purpose

Capability-based routing and accounting for OpenAI-compatible model sources
and native Claude subscription pools, including field-preserving embeddings
forwarding for compatible sources.

This capability keeps source selection separate from subscription-account
routing: embeddings traffic is served only by sources that declare the
embeddings capability, while Responses/chat/audio continue to use their own
capability gates. Field presence (including explicit nulls) is preserved on
embeddings forwards so compatible sources see the same payload shape the
client sent.

## Abliteration historical images

The vision-capable Abliteration model accepts images in user messages but
rejects the same image data inside a tool result. Historical images therefore
move to labeled user messages after contiguous tool results; the original
results retain their text and a reference to the images. Keeping the result
group intact preserves parallel call pairing. Text-only models retain their
historical-image placeholders, and current user image validation stays intact.

## Claude subscription pools

An Anthropic source imports the existing claude-lb account file and stores its
typed pool in the existing encrypted credential field. Public settings expose
account metadata. The source participates in the existing HTTP Responses model
catalog, source assignment, admission, and usage settlement paths.

The pinned official Claude Agent SDK runs each generation in a private temporary
configuration. Native server tools and host settings are disabled; a server hook
denies tool execution, while inert MCP adapters carry the client's declarations.
Codex receives tool calls and executes them locally. Full conversation records,
including completed client tool results, are replayed through the version-pinned
native transcript codec. SDK upgrades must repeat the real Codex tool round trip.

Startup leases serialize refresh-token use. Rotated tokens are saved before
model output is exposed, with guarded updates against newer credentials. Auth,
rate-limit, and transient failures may select another Claude account before
content is delivered. Exhaustion never falls through to GPT or API billing.
Cancellation joins the native worker and deletes private temporary state.

Stop local consumers of imported refresh tokens before hosted activation: the
hosted pool owns subsequent rotations. Replace accounts through source settings
if a new sign-in is needed. Forced tool choice, serial-tool enforcement, explicit
sampling, structured output, and server-owned response/file references are not
supported and fail explicitly.
