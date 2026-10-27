"""Hosted Claude account pool, with guarded persistence and pre-content failover."""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select, update

from app.core.clock import REAL_CLOCK, REAL_SCHEDULER, Clock, Scheduler
from app.core.crypto import TokenEncryptor
from app.core.types import JsonValue
from app.core.utils.shared_future import _await_cleanup_deferring_cancellation
from app.db.models import ModelSource
from app.db.session import get_background_session
from app.modules.model_sources.anthropic_credentials import ANTHROPIC_BASE_URL, ClaudeAccount, ClaudePool
from app.modules.model_sources.anthropic_native import NativeTokens, open_native_stream
from app.modules.model_sources.anthropic_protocol import ClaudeEvents, ClaudeProtocolError, project_claude_request
from app.modules.model_sources.forwarding import (
    ModelSourceForwardingError,
    OnFirstContent,
    SourceResponsesCompletion,
    SourceResponsesStream,
    SourceStreamTransport,
    SourceStreamUsageParser,
    SourceUsageHolder,
    _source_client_timeout,
)


def _error(status: int, code: str, message: str, *, retry_after: str | None = None) -> ModelSourceForwardingError:
    return ModelSourceForwardingError(
        status_code=status,
        payload={
            "error": {
                "code": code,
                "type": "rate_limit_error"
                if status == 429
                else "invalid_request_error"
                if status == 400
                else "upstream_error",
                "message": message,
            }
        },
        retry_after=retry_after,
    )


class ClaudePoolStore:
    def __init__(self, source_id: str, encryptor: TokenEncryptor) -> None:
        self.source_id = source_id
        self.encryptor = encryptor

    async def load(self) -> tuple[ClaudePool, bytes]:
        async with get_background_session() as session:
            row = (
                await session.execute(
                    select(ModelSource.api_key_encrypted, ModelSource.is_enabled).where(
                        ModelSource.id == self.source_id
                    )
                )
            ).one_or_none()
        if row is None or not row.is_enabled or row.api_key_encrypted is None:
            raise _error(503, "claude_pool_unavailable", "The Claude source is disabled or unavailable")
        try:
            return ClaudePool.model_validate_json(self.encryptor.decrypt(row.api_key_encrypted)), row.api_key_encrypted
        except Exception as exc:
            raise _error(502, "model_source_credentials_error", "Claude credentials could not be read") from exc

    async def mutate(self, change: Callable[[ClaudePool], bool]) -> ClaudePool | None:
        for _ in range(8):
            pool, expected = await self.load()
            if not change(pool):
                return None
            encrypted = self.encryptor.encrypt(pool.model_dump_json())
            async with get_background_session() as session:
                result = await session.execute(
                    update(ModelSource)
                    .where(
                        ModelSource.id == self.source_id,
                        ModelSource.is_enabled.is_(True),
                        ModelSource.api_key_encrypted == expected,
                    )
                    .values(api_key_encrypted=encrypted)
                    .returning(ModelSource.id)
                )
                changed = result.scalar_one_or_none() is not None
                await session.commit()
            if changed:
                return pool
        raise _error(
            503, "claude_pool_busy", "Claude credentials are being updated; retry the request", retry_after="1"
        )

    async def update_account(
        self, account: ClaudeAccount, change: Callable[[ClaudeAccount], None], *, claim: str | None = None
    ) -> ClaudePool | None:
        def apply(pool: ClaudePool) -> bool:
            current = next((a for a in pool.accounts if a.id == account.id), None)
            if (
                current is None
                or current.refresh_token != account.refresh_token
                or current.access_token != account.access_token
            ):
                return False
            if claim is not None and current.refresh_claim != claim:
                return False
            change(current)
            return True

        return await self.mutate(apply)


async def _ready_account(
    store: ClaudePoolStore, account: ClaudeAccount, *, clock: Clock, scheduler: Scheduler
) -> ClaudeAccount | None:
    # Serialize native authentication startup, including any single-use refresh.
    # The claim is released after the first model frame and durable token write.
    now = clock.now()
    claim = uuid4().hex

    def claim_startup(pool: ClaudePool) -> bool:
        current = next((a for a in pool.accounts if a.id == account.id), None)
        if current is None or current.refresh_token != account.refresh_token or current.needs_reauthentication:
            return False
        if current.refresh_claim_until is not None and current.refresh_claim_until > now:
            return False
        current.refresh_claim = claim
        current.refresh_claim_until = now + timedelta(seconds=60)
        return True

    claimed = await store.mutate(claim_startup)
    return next((a for a in claimed.accounts if a.id == account.id), None) if claimed is not None else None


async def stream_claude_responses(
    source: ModelSource,
    payload: dict[str, JsonValue],
    *,
    encryptor: TokenEncryptor | None = None,
    on_first_content: OnFirstContent | None = None,
    scheduler: Scheduler = REAL_SCHEDULER,
    clock: Clock = REAL_CLOCK,
) -> SourceResponsesStream:
    if source.base_url.rstrip("/") != ANTHROPIC_BASE_URL:
        raise _error(502, "claude_source_invalid", "Claude credentials require the official Anthropic endpoint")
    active_encryptor = encryptor or TokenEncryptor()
    try:
        request = project_claude_request(payload, source_id=source.id, encryptor=active_encryptor)
    except ClaudeProtocolError as exc:
        raise _error(400, "claude_unsupported_request", str(exc)) from exc
    store = ClaudePoolStore(source.id, active_encryptor)
    initial, _ = await store.load()
    deadline = clock.monotonic() + (_source_client_timeout(source).total or 600)
    for candidate in initial.accounts:
        pool, _ = await store.load()
        account = next((a for a in pool.accounts if a.id == candidate.id), None)
        if (
            account is None
            or account.needs_reauthentication
            or (account.cooldown_until and account.cooldown_until > clock.now())
        ):
            continue
        try:
            with scheduler.fail_after(max(0, deadline - clock.monotonic())):
                account = await _ready_account(store, account, clock=clock, scheduler=scheduler)
        except TimeoutError as exc:
            raise _error(504, "model_source_timeout", "The Claude source request timed out") from exc
        if account is None:
            continue

        async def save_native_tokens(tokens: NativeTokens, final: bool) -> None:
            nonlocal account
            assert account is not None
            unchanged = (
                account.access_token == tokens.access_token
                and account.refresh_token == tokens.refresh_token
                and int(account.expires_at.timestamp() * 1000) == int(tokens.expires_at.timestamp() * 1000)
            )
            if unchanged and account.refresh_claim is None:
                return

            def persist(current: ClaudeAccount) -> None:
                current.access_token = tokens.access_token
                current.refresh_token = tokens.refresh_token
                current.expires_at = tokens.expires_at
                current.refresh_claim = None
                current.refresh_claim_until = None

            saved = await store.update_account(account, persist, claim=account.refresh_claim)
            if saved is None:
                raise _error(
                    503,
                    "claude_credentials_changed",
                    "Claude credentials changed during authentication; retry",
                    retry_after="1",
                )
            account = next(a for a in saved.accounts if a.id == account.id)

        async def release_native_claim() -> None:
            assert account is not None
            if account.refresh_claim is not None:

                def release(current: ClaudeAccount) -> None:
                    current.refresh_claim = None
                    current.refresh_claim_until = None

                await store.update_account(account, release, claim=account.refresh_claim)

        try:
            opened = await open_native_stream(
                source,
                request,
                account,
                seconds=max(0.001, deadline - clock.monotonic()),
                on_credentials=save_native_tokens,
                on_close=release_native_claim,
                scheduler=scheduler,
                clock=clock,
            )
        except ModelSourceForwardingError as exc:
            status = exc.upstream_status_code or exc.status_code
            if status == 400:
                raise
            if status not in {401, 403, 429, 500, 502, 503, 504, 529}:
                raise _error(502, "claude_request_rejected", "Claude rejected the translated request") from exc
            delay = 60.0
            if exc.retry_after:
                try:
                    delay = min(86400, max(1, float(exc.retry_after)))
                    if not math.isfinite(delay):
                        delay = 60
                except ValueError:
                    pass

            def cool(current: ClaudeAccount) -> None:
                current.needs_reauthentication = status in {401, 403}
                current.last_error = "auth" if status in {401, 403} else "rate_limit" if status == 429 else "upstream"
                current.cooldown_until = clock.now() + timedelta(seconds=delay)

            await store.update_account(account, cool)
            continue
        transport = opened.transport
        raw_body = opened.body
        events = ClaudeEvents(request, source_id=source.id, encryptor=active_encryptor, created_at=int(clock.time()))
        buffered: list[bytes] = []
        buffered_bytes = 0
        try:
            # Hold bookkeeping until a real output item. An SSE rejection before
            # that point can still move to another account without replaying output.
            async for chunk in raw_body:
                converted = events.feed(chunk)
                buffered.extend(converted)
                buffered_bytes += sum(map(len, converted))
                if buffered_bytes > 2_097_152:
                    raise ClaudeProtocolError("Claude sent excessive bookkeeping before output")
                if any(b"event: response.output_item.added\n" in frame for frame in converted) or events.terminal:
                    break
            if not buffered or not events.output_indices:
                raise ClaudeProtocolError("Claude ended without output")
        except (ClaudeProtocolError, ModelSourceForwardingError):
            await _await_cleanup_deferring_cancellation(transport.aclose())

            def cool_stream(current: ClaudeAccount) -> None:
                current.cooldown_until = clock.now() + timedelta(seconds=30)
                current.last_error = "upstream"

            await store.update_account(account, cool_stream)
            continue
        except BaseException:
            await _await_cleanup_deferring_cancellation(transport.aclose())
            raise
        holder = SourceUsageHolder(first_frame_at=clock.monotonic())
        body = _translated_body(raw_body, buffered, events, holder, transport, on_first_content)
        return SourceResponsesStream(
            body=body, usage_holder=holder, upstream_status_code=opened.upstream_status_code, transport=transport
        )
    current, _ = await store.load()
    eligible = [a for a in current.accounts if not a.needs_reauthentication]
    if not eligible:
        raise _error(502, "claude_reauthentication_required", "The Claude accounts require sign-in again")
    resets = [
        max(1, math.ceil((a.cooldown_until - clock.now()).total_seconds()))
        for a in eligible
        if a.cooldown_until is not None
    ]
    limited = any(account.last_error == "rate_limit" for account in eligible)
    raise _error(
        429 if limited else 503,
        "claude_pool_exhausted" if limited else "claude_pool_unavailable",
        "All Claude accounts are cooling down or refreshing",
        retry_after=str(min(resets, default=1)),
    )


async def _translated_body(
    raw_body: AsyncIterator[bytes],
    buffered: list[bytes],
    events: ClaudeEvents,
    holder: SourceUsageHolder,
    transport: SourceStreamTransport,
    hook: OnFirstContent | None,
) -> AsyncIterator[bytes]:
    parser = SourceStreamUsageParser(holder, response_shape="responses")
    pinned = False

    async def observe(frame: bytes) -> None:
        nonlocal pinned
        parser.feed(frame)
        if holder.first_content_seen and not pinned:
            if hook is not None:
                await hook(holder)
            pinned = True
            holder.content_delivered = True

    try:
        for frame in buffered:
            await observe(frame)
            yield frame
        if events.terminal:
            return
        async for chunk in raw_body:
            for frame in events.feed(chunk):
                await observe(frame)
                yield frame
            if events.terminal:
                return
        if events.buffer.strip():
            for frame in events.feed(b"\n\n"):
                await observe(frame)
                yield frame
        if not events.terminal:
            raise ClaudeProtocolError("Claude stream ended before the turn completed")
    except (ClaudeProtocolError, ModelSourceForwardingError, TimeoutError):
        events.response["status"] = "failed"
        events.response["error"] = {"code": "claude_stream_failed", "message": "Claude did not complete the response"}
        frame = events.event("response.failed", response=events.response)
        parser.feed(frame)
        yield frame
    finally:
        parser.finish()
        await transport.aclose()


async def forward_claude_responses(
    source: ModelSource, payload: dict[str, JsonValue], *, encryptor: TokenEncryptor | None = None
) -> SourceResponsesCompletion:
    stream = await stream_claude_responses(source, payload, encryptor=encryptor)
    final: dict[str, JsonValue] | None = None
    try:
        async for frame in stream.body:
            if frame.startswith((b"event: response.completed\n", b"event: response.incomplete\n")):
                event = json.loads(frame.split(b"data: ", 1)[1])
                final = event["response"]
    finally:
        await stream.aclose()
    if final is None:
        raise _error(502, "claude_stream_failed", "Claude did not complete the response")
    return SourceResponsesCompletion(
        payload=final, usage=stream.usage_holder.usage, timings=None, upstream_status_code=stream.upstream_status_code
    )
