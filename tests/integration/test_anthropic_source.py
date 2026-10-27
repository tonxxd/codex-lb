from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import web

import app.modules.model_sources.anthropic as claude
import app.modules.model_sources.service as sources_service
from app.core.crypto import TokenEncryptor
from app.db.models import ModelSource
from app.db.session import detach_session_objects, get_background_session
from app.modules.model_sources.anthropic_credentials import ClaudePool
from app.modules.model_sources.anthropic_native import NativeStream, NativeTokens, native_error
from app.modules.model_sources.forwarding import (
    ModelSourceForwardingError,
    SourceStreamTransport,
    SourceStreamUsageParser,
    SourceUsageHolder,
    _open_source_stream,
    _source_stream_body,
    source_stream_idle_seconds,
)
from app.modules.model_sources.repository import ModelSourcesRepository
from tests.integration.model_source_helpers import _enable_api_key_auth, stub_source_upstreams

pytestmark = pytest.mark.integration


def _frames(*, tool_name=None, tool_input=None, stop="end_turn", text="OK 🧪") -> bytes:
    block = (
        {"type": "text", "text": ""}
        if tool_name is None
        else {"type": "tool_use", "id": "toolu_test", "name": tool_name, "input": {}}
    )
    delta = (
        {"type": "text_delta", "text": text}
        if tool_name is None
        else {"type": "input_json_delta", "partial_json": json.dumps(tool_input)}
    )
    events = [
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 10, "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2}},
        },
        {"type": "content_block_start", "index": 0, "content_block": block},
        {"type": "content_block_delta", "index": 0, "delta": delta},
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use" if tool_name else stop},
            "usage": {"output_tokens": 4},
        },
        {"type": "message_stop"},
    ]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events).encode()


async def _source(client, base_url, monkeypatch, *, expired=False):
    monkeypatch.setattr(sources_service, "ANTHROPIC_BASE_URL", base_url)
    monkeypatch.setattr(claude, "ANTHROPIC_BASE_URL", base_url)

    # Stub only the native engine boundary. Route ownership, encryption, pool
    # recovery and Responses translation run through their production paths.
    async def engine(source, request, account, *, seconds, on_credentials, on_close, scheduler, clock):
        encryptor = TokenEncryptor()
        stub = ModelSource(
            id=source.id,
            kind="openai_compatible",
            base_url=source.base_url,
            timeout_seconds=source.timeout_seconds,
            api_key_encrypted=encryptor.encrypt(account.access_token),
        )
        try:
            stack, response, first = await _open_source_stream(
                stub,
                "/messages",
                request.payload,
                encryptor=encryptor,
                scheduler=scheduler,
                clock=clock,
            )
        except ModelSourceForwardingError as exc:
            await on_close()
            raise native_error(exc.upstream_status_code or exc.status_code) from None
        except BaseException:
            await on_close()
            raise
        stack.push_async_callback(on_close)
        transport = SourceStreamTransport(stack, scheduler=scheduler)
        holder = SourceUsageHolder()
        try:
            await on_credentials(NativeTokens(account.access_token, account.refresh_token, account.expires_at), False)
        except BaseException:
            await transport.aclose()
            raise
        body = _source_stream_body(
            response,
            first,
            SourceStreamUsageParser(holder, response_shape="anthropic"),
            holder,
            transport,
            on_first_content=None,
            idle_seconds=source_stream_idle_seconds(),
            scheduler=scheduler,
            clock=clock,
        )
        return NativeStream(body, transport, response.status)

    monkeypatch.setattr(claude, "open_native_stream", engine)
    expiry = datetime.now(UTC) + timedelta(hours=-1 if expired else 2)
    result = await client.post(
        "/api/model-sources/",
        json={
            "name": "Claude test",
            "kind": "anthropic",
            "baseUrl": base_url,
            "supportsChatCompletions": False,
            "supportsResponses": True,
            "anthropicAccounts": [
                {
                    "id": str(i),
                    "name": f"Account {i}",
                    "accessToken": f"access-{i}",
                    "refreshToken": f"refresh-{i}",
                    "expiresAt": expiry.isoformat(),
                }
                for i in range(2)
            ],
            "models": [
                {"model": "claude-opus-5-5", "supportsStreaming": True, "supportsTools": True, "supportsVision": True}
            ],
        },
    )
    assert result.status_code == 200, result.text
    public = result.json()
    assert len(public["anthropicAccounts"]) == 2
    assert "access-" not in result.text and "refresh-" not in result.text
    return public["id"]


def _response_json(response, streaming):
    if not streaming:
        return response.json()
    events = [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    assert events[-1]["type"] == "response.completed", response.text
    sequences = [event["sequence_number"] for event in events if "sequence_number" in event]
    assert sequences == sorted(set(sequences))
    return events[-1]["response"]


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_claude_source_routes_failover_and_usage(async_client, monkeypatch, path, streaming):
    attempts = []

    async def upstream(request):
        attempts.append((request.headers.get("Authorization"), await request.json()))
        if len(attempts) == 1:
            return web.json_response(
                {"error": {"type": "rate_limit_error", "message": "limited"}}, status=429, headers={"Retry-After": "37"}
            )
        return web.Response(body=_frames(), content_type="text/event-stream")

    async with stub_source_upstreams() as start:
        base = await start(upstream)
        source_id = await _source(async_client, base, monkeypatch)
        response = await async_client.post(
            path,
            json={"model": "claude-opus-5-5", "input": "Hello", "stream": streaming, "tools": [{"type": "web_search"}]},
        )
        assert response.status_code == 200, response.text
        body = _response_json(response, streaming)
        assert body["output"][0]["content"][0]["text"] == "OK 🧪"
        assert body["usage"]["input_tokens"] == 15
        assert body["usage"]["output_tokens"] == 4
        assert [auth for auth, _ in attempts] == ["Bearer access-0", "Bearer access-1"]
        assert "tools" not in attempts[-1][1]
        assert attempts[-1][1]["messages"] == [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]
        listed = await async_client.get("/api/model-sources/")
        assert listed.json()["sources"][0]["id"] == source_id
        assert listed.json()["sources"][0]["anthropicAccounts"][0]["cooldownUntil"] is not None
        assert "access-" not in listed.text and "refresh-" not in listed.text
        catalog = await async_client.get("/v1/models")
        assert "claude-opus-5-5" in catalog.text
        native_catalog = await async_client.get("/backend-api/codex/models")
        assert native_catalog.status_code == 200
        assert "claude-opus-5-5" in native_catalog.text


@pytest.mark.parametrize("custom", [False, True])
async def test_claude_tools_round_trip_with_images(async_client, monkeypatch, custom):
    captured = []

    async def upstream(request):
        body = await request.json()
        captured.append(body)
        if len(captured) == 1:
            return web.Response(
                body=_frames(
                    tool_name=body["tools"][0]["name"],
                    tool_input={"input": "echo hello"} if custom else {"cmd": "echo hello"},
                ),
                content_type="text/event-stream",
            )
        return web.Response(body=_frames(), content_type="text/event-stream")

    async with stub_source_upstreams() as start:
        await _source(async_client, await start(upstream), monkeypatch)
        declaration = (
            {"type": "custom", "name": "apply", "format": {"type": "text"}}
            if custom
            else {
                "type": "namespace",
                "name": "functions",
                "tools": [
                    {
                        "type": "function",
                        "name": "exec",
                        "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}},
                    }
                ],
            }
        )
        base = {"model": "claude-opus-5-5", "tools": [declaration], "stream": True}
        first = await async_client.post("/backend-api/codex/responses", json={**base, "input": "Use the tool"})
        assert first.status_code == 200, first.text
        output = _response_json(first, True)["output"]
        assert output[0]["type"] == ("custom_tool_call" if custom else "function_call")
        result = {
            "type": "custom_tool_call_output" if custom else "function_call_output",
            "call_id": "toolu_test",
            "output": [
                {"type": "input_text", "text": "hello"},
                {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
            ],
        }
        second = await async_client.post(
            "/backend-api/codex/responses",
            json={**base, "input": [{"role": "user", "content": "Use the tool"}, *output, result]},
        )
        assert second.status_code == 200, second.text
        assert _response_json(second, True)["status"] == "completed"
        history = captured[-1]["messages"]
        assert history[-2]["content"][0]["type"] == "tool_use"
        assert history[-2]["content"][0]["name"] == captured[0]["tools"][0]["name"]
        assert history[-1]["content"][0]["tool_use_id"] == "toolu_test"
        assert history[-1]["content"][0]["content"][1]["type"] == "image"


@pytest.mark.parametrize("status,expected", [(401, 502), (403, 502), (429, 429), (529, 503)])
async def test_claude_pool_exhaustion_never_uses_gpt(async_client, monkeypatch, status, expected):
    attempts = []

    async def upstream(request):
        attempts.append(request.headers.get("Authorization"))
        return web.json_response({"error": {"message": "access-0 refresh-0", "type": "upstream_error"}}, status=status)

    async with stub_source_upstreams() as start:
        await _source(async_client, await start(upstream), monkeypatch)
        response = await async_client.post("/v1/responses", json={"model": "claude-opus-5-5", "input": "Hello"})
        assert response.status_code == expected, response.text
        assert len(attempts) == 2
        assert "access-" not in response.text and "refresh-" not in response.text
        if status == 429:
            assert int(response.headers["Retry-After"]) > 0


@pytest.mark.parametrize("expired", [False, True])
async def test_claude_native_startup_serializes_token_rotation(async_client, monkeypatch, expired):
    async def upstream(request):
        return web.Response(body=_frames(), content_type="text/event-stream")

    async with stub_source_upstreams() as start:
        source_id = await _source(async_client, await start(upstream), monkeypatch, expired=expired)
        store = claude.ClaudePoolStore(source_id, TokenEncryptor())
        pool, _ = await store.load()
        result = await asyncio.gather(
            *(
                claude._ready_account(store, pool.accounts[0], clock=claude.REAL_CLOCK, scheduler=claude.REAL_SCHEDULER)
                for _ in range(2)
            )
        )
        assert sum(account is not None for account in result) == 1
        owner = next(account for account in result if account is not None)

        def rotated(current):
            current.access_token = "rotated-access"
            current.refresh_token = "rotated-refresh"
            current.expires_at = datetime.now(UTC) + timedelta(hours=2)
            current.refresh_claim = None
            current.refresh_claim_until = None

        assert await store.update_account(owner, rotated, claim=owner.refresh_claim) is not None
        assert await store.update_account(owner, rotated, claim=owner.refresh_claim) is None
        saved, encrypted = await store.load()
        assert saved.accounts[0].access_token == "rotated-access"
        assert saved.accounts[0].refresh_token == "rotated-refresh"
        assert saved.accounts[0].refresh_claim is None
        assert b"rotated-access" not in encrypted
        assert isinstance(saved, ClaudePool)


async def test_claude_stream_closes_before_consumer_starts(async_client, monkeypatch):
    closed = asyncio.Event()

    async def upstream(request):
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(
            b'data: {"type":"message_start","message":{"usage":{"input_tokens":1}}}\n\n'
            b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
        )
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            closed.set()
            raise

    async with stub_source_upstreams() as start:
        source_id = await _source(async_client, await start(upstream, handler_cancellation=True), monkeypatch)
        async with get_background_session() as session:
            source = await ModelSourcesRepository(session).get_by_id(source_id)
            detach_session_objects(session)
        stream = await claude.stream_claude_responses(source, {"model": "claude-opus-5-5", "input": "hi"})
        await stream.aclose()
        await asyncio.wait_for(closed.wait(), timeout=2)


async def test_claude_does_not_retry_after_stream_content(async_client, monkeypatch):
    attempts = 0

    async def upstream(request):
        nonlocal attempts
        attempts += 1
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(
            b'data: {"type":"message_start","message":{"usage":{"input_tokens":1}}}\n\n'
            b'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}\n\n'
            b'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Started"}}\n\n'
        )
        await asyncio.sleep(0.03)
        await response.write(
            b'data: {"type":"error","error":{"type":"overloaded_error","message":"private details"}}\n\n'
        )
        return response

    async with stub_source_upstreams() as start:
        await _source(async_client, await start(upstream), monkeypatch)
        response = await async_client.post(
            "/v1/responses", json={"model": "claude-opus-5-5", "input": "hi", "stream": True}
        )
        assert response.status_code == 200
        assert '"type":"response.failed"' in response.text.replace(" ", "")
        assert "Started" in response.text
        assert "private details" not in response.text
        assert attempts == 1


async def test_claude_native_exception_after_content_is_a_sanitized_failure(async_client, monkeypatch):
    await _source(async_client, claude.ANTHROPIC_BASE_URL, monkeypatch)
    attempts = 0
    closed = []

    async def engine(source, request, account, *, on_credentials, on_close, scheduler, **_kwargs):
        nonlocal attempts
        attempts += 1
        await on_credentials(NativeTokens(account.access_token, account.refresh_token, account.expires_at), False)
        stack = AsyncExitStack()
        stack.push_async_callback(on_close)

        async def record_close():
            closed.append(True)

        stack.push_async_callback(record_close)

        async def body():
            yield _frames(text="Started").split(b"event: message_delta", 1)[0]
            raise native_error(429)

        return NativeStream(body(), SourceStreamTransport(stack, scheduler=scheduler))

    monkeypatch.setattr(claude, "open_native_stream", engine)
    response = await async_client.post(
        "/backend-api/codex/responses",
        json={"model": "claude-opus-5-5", "input": "hi", "stream": True},
    )
    assert response.status_code == 200
    assert '"type":"response.failed"' in response.text.replace(" ", "")
    assert "Started" in response.text
    assert "claude_native_failed" not in response.text
    assert attempts == 1 and closed == [True]


async def test_claude_invalid_account_input_does_not_reflect_secrets(async_client):
    response = await async_client.post(
        "/api/model-sources/",
        json={
            "name": "Claude",
            "kind": "anthropic",
            "baseUrl": "https://api.anthropic.com/v1",
            "anthropicAccounts": [
                {"id": "one", "name": "one", "accessToken": "private-access", "refreshToken": "private-refresh"}
            ],
        },
    )
    assert response.status_code in {400, 422}
    assert "private-access" not in response.text
    assert "private-refresh" not in response.text


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize(
    "metadata,thinking",
    [('{"request_kind":"compaction"}', False), ('{"request_kind":"turn"}', True), ("[]", True), ("invalid-json", True)],
)
async def test_claude_compaction_reserves_output_for_summary(async_client, monkeypatch, path, metadata, thinking):
    captured = []

    async def upstream(request):
        captured.append(await request.json())
        return web.Response(body=_frames(), content_type="text/event-stream")

    async with stub_source_upstreams() as start:
        await _source(async_client, await start(upstream), monkeypatch)
        response = await async_client.post(
            path,
            headers={"x-codex-turn-metadata": metadata},
            json={
                "model": "claude-opus-5-5",
                "input": "Summarize the conversation",
                "stream": True,
                "reasoning": {"effort": "max"},
                "max_output_tokens": 1234,
                "client_metadata": {"private_telemetry": "must-not-forward"},
            },
        )
        assert response.status_code == 200, response.text
        assert ("thinking" in captured[0]) is thinking
        assert captured[0]["max_tokens"] == 1234
        assert "must-not-forward" not in json.dumps(captured[0])


@pytest.mark.parametrize("policy", [{"enforcedReasoningEffort": "max"}, {"allowedReasoningEfforts": ["max"]}])
async def test_claude_compaction_preserves_explicit_key_reasoning_policy(async_client, monkeypatch, policy):
    captured = []

    async def upstream(request):
        captured.append(await request.json())
        return web.Response(body=_frames(), content_type="text/event-stream")

    async with stub_source_upstreams() as start:
        await _source(async_client, await start(upstream), monkeypatch)
        created = await async_client.post("/api/api-keys/", json={"name": "Reasoning policy", **policy})
        assert created.status_code == 200
        await _enable_api_key_auth(async_client)
        response = await async_client.post(
            "/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer " + created.json()["key"],
                "x-codex-turn-metadata": '{"request_kind":"compaction"}',
            },
            json={"model": "claude-opus-5-5", "input": "Summarize", "stream": True, "reasoning": {"effort": "max"}},
        )
        assert response.status_code == 200
        assert captured[0]["thinking"] == {"type": "adaptive"}
        assert captured[0]["output_config"]["effort"] == "max"
