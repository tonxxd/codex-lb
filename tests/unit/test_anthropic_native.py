from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from claude_agent_sdk import StreamEvent
from cryptography.fernet import Fernet

import app.modules.model_sources.anthropic_native as native
from app.core.clock import REAL_CLOCK, REAL_SCHEDULER
from app.core.crypto import TokenEncryptor
from app.db.models import ModelSource
from app.modules.model_sources.anthropic_credentials import ClaudeAccount
from app.modules.model_sources.anthropic_protocol import ClaudeEvents, project_claude_request

pytestmark = pytest.mark.unit


def _account():
    return ClaudeAccount(
        id="one",
        name="Claude",
        access_token="private-access",
        refresh_token="private-refresh",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _request(encryptor, *, replay=False):
    history = [{"role": "user", "content": "Use the tool"}]
    if replay:
        history.extend(
            [
                {"type": "function_call", "name": "get_marker", "call_id": "toolu_previous", "arguments": "{}"},
                {
                    "type": "function_call_output",
                    "call_id": "toolu_previous",
                    "output": [
                        {"type": "input_text", "text": "MARKER"},
                        {"type": "input_image", "image_url": "data:image/png;base64,aGVsbG8="},
                    ],
                },
            ]
        )
    return project_claude_request(
        {
            "model": "claude-opus-5-5",
            "input": history,
            "tools": [
                {
                    "type": "function",
                    "name": "get_marker",
                    "description": "Get a marker",
                    "parameters": {"type": "object", "properties": {}},
                }
            ],
        },
        source_id="src_test",
        encryptor=encryptor,
    )


def _event(event):
    return StreamEvent(uuid="test", session_id="test", parent_tool_use_id=None, event=event)


@pytest.mark.parametrize("cancel", [False, True])
async def test_native_runtime_isolates_auth_replays_history_and_cleans_up(monkeypatch, cancel):
    captured = {}
    exited = asyncio.Event()
    rotated = []
    closed = []

    class Client:
        def __init__(self, *, options):
            self.options = options
            captured["options"] = options

        async def __aenter__(self):
            self.owner = asyncio.current_task()
            config = Path(self.options.env["CLAUDE_CONFIG_DIR"])
            captured["config"] = config
            credential_file = config / ".credentials.json"
            assert credential_file.stat().st_mode & 0o777 == 0o600
            data = json.loads(credential_file.read_text())
            assert data["claudeAiOauth"]["accessToken"] == "private-access"
            data["claudeAiOauth"].update(accessToken="rotated-access", refreshToken="rotated-refresh")
            credential_file.write_text(json.dumps(data))
            transcript = next((config / "projects").rglob("*.jsonl"))
            captured["history"] = [json.loads(line)["message"] for line in transcript.read_text().splitlines()]
            return self

        async def __aexit__(self, *_args):
            assert asyncio.current_task() is self.owner
            exited.set()

        async def query(self, messages):
            captured["prompts"] = [message async for message in messages]

        async def receive_messages(self):
            yield _event({"type": "message_start", "message": {"usage": {"input_tokens": 2}}})
            if cancel:
                await asyncio.Event().wait()
            for event in [
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_new",
                        "name": "mcp__codex__get_marker",
                        "input": {},
                    },
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": "{}"},
                },
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 3}},
                {"type": "message_stop"},
            ]:
                yield _event(event)

    monkeypatch.setattr(native, "ClaudeSDKClient", Client)
    encryptor = TokenEncryptor(key=Fernet.generate_key())
    request = _request(encryptor, replay=True)

    async def credentials(tokens, final):
        rotated.append((tokens.access_token, tokens.refresh_token, final))

    async def close():
        closed.append(True)

    source = ModelSource(id="src_test", timeout_seconds=30)
    stream = await native.open_native_stream(
        source,
        request,
        _account(),
        seconds=30,
        on_credentials=credentials,
        on_close=close,
        scheduler=REAL_SCHEDULER,
        clock=REAL_CLOCK,
    )
    if not cancel:
        events = ClaudeEvents(request, source_id="src_test", encryptor=encryptor, created_at=123)
        async for chunk in stream.body:
            events.feed(chunk)
        assert events.response["output"][0]["name"] == "get_marker"
        assert events.terminal
    await stream.transport.aclose()
    await stream.transport.aclose()
    assert exited.is_set()
    assert len(closed) == 1
    assert not captured["config"].exists()
    assert all(access == "rotated-access" and refresh == "rotated-refresh" for access, refresh, _ in rotated)
    options = captured["options"]
    assert options.tools == []
    assert options.setting_sources == []
    assert options.strict_mcp_config is True
    assert options.max_turns == 1
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    ):
        assert options.env[key] == ""
    assert [message["role"] for message in captured["history"]] == ["user", "assistant", "user"]
    assert captured["history"][1]["content"][0]["name"] == "mcp__codex__get_marker"
    result = captured["history"][2]["content"][0]
    assert result["tool_use_id"] == "toolu_previous"
    assert result["content"][1]["type"] == "image"
    hook = options.hooks["PreToolUse"][0].hooks[0]
    decision = await hook({"tool_name": "Bash"}, "toolu_unexpected", {})
    assert decision["hookSpecificOutput"]["permissionDecision"] == "deny"


async def test_native_close_cancels_a_full_error_queue(monkeypatch):
    failing = asyncio.Event()
    closed = asyncio.Event()

    class Client:
        def __init__(self, *, options):
            self.options = options

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def query(self, messages):
            pass

        async def receive_messages(self):
            # open_native_stream removes one frame. Sixteen fill the queue,
            # leaving the worker blocked while reporting its terminal error.
            for _ in range(17):
                yield _event({"type": "ping"})
            failing.set()
            raise native.native_error(429)

    async def credentials(_tokens, _final):
        pass

    async def close():
        closed.set()

    monkeypatch.setattr(native, "ClaudeSDKClient", Client)
    stream = await native.open_native_stream(
        ModelSource(id="src_test", timeout_seconds=30),
        _request(TokenEncryptor(key=Fernet.generate_key())),
        _account(),
        seconds=30,
        on_credentials=credentials,
        on_close=close,
        scheduler=REAL_SCHEDULER,
        clock=REAL_CLOCK,
    )
    await asyncio.wait_for(failing.wait(), 2)
    await asyncio.wait_for(stream.transport.aclose(), 2)
    assert closed.is_set()


@pytest.mark.parametrize("effort,expected", [("none", "disabled"), ("minimal", "disabled"), ("high", "adaptive")])
def test_native_options_preserve_reasoning_and_reject_sampling(tmp_path, effort, expected):
    encryptor = TokenEncryptor(key=Fernet.generate_key())
    payload = {"model": "claude-opus-5-5", "input": "Hello", "reasoning": {"effort": effort}}
    request = project_claude_request(payload, source_id="src_test", encryptor=encryptor)
    options, _ = native._options(request, _account(), tmp_path, tmp_path, seconds=30, clock=REAL_CLOCK)
    assert options.thinking["type"] == expected
    request = project_claude_request({**payload, "temperature": 0.5}, source_id="src_test", encryptor=encryptor)
    with pytest.raises(native.ModelSourceForwardingError) as exc:
        native._options(request, _account(), tmp_path, tmp_path, seconds=30, clock=REAL_CLOCK)
    assert exc.value.status_code == 400
