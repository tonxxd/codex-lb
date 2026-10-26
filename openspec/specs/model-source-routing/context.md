# Model Source Routing — Context

## Purpose

Capability-based routing and accounting for OpenAI-compatible model sources,
including field-preserving embeddings forwarding.

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
