# Provider replay repair

An explicit upstream `token_revoked` rejection proves that the access token is
unusable, unlike a refresh-token-only warning where the stored access token may
still serve requests. The existing guarded status write and failure reason are
used to retain the reauthentication action without adding a schema field.

Pre-visible HTTP recovery projects a self-contained transcript, omits opaque
reasoning and response IDs, and preserves the visible messages and paired tool
results. Namespace tool declarations and Codex's client-only metadata are
recognized without accepting unknown hosted state. Recovery clears the failed
request's required owner and avoids legacy session defaults; it does not delete
another anchored request's durable ownership. A separate recovered-account set
bounds recovery even when a preceding retry already excluded the same account.
API-key usage settles for the successful attempt before deferred health writes.

Abliteration's source-specific projection translates historical custom calls to
function-call history and replaces only historical tool images for text models.
User image validation and other source payloads are preserved. This does not add
vision support to the large text-only models.

Verification includes canonical and Codex backend Responses routing, keyed and
unkeyed requests, successful usage settlement, native Responses-Lite history,
stored-response/file-owner refusal, unchanged other-source forwarding, and live
synthetic Abliteration continuation. Production is backed up before rollout;
the operator's key uses HTTP egress to keep complete client history available
for recovery rather than upgrading it to a server-retained WebSocket bridge.
