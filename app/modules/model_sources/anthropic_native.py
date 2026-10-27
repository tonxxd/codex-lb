"""One native Claude generation, isolated from the LB host and client tools.

The SDK owns its subprocess in one task for its entire lifetime. A bounded queue
bridges that task to the HTTP response owner; closing the response cancels and
joins the task before deleting its private credentials and transcript directory.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    ResultMessage,
    SdkMcpTool,
    StreamEvent,
    create_sdk_mcp_server,
)
from claude_agent_sdk._cli_version import __cli_version__
from claude_agent_sdk._internal.sessions import project_key_for_directory
from claude_agent_sdk.types import SyncHookJSONOutput
from pydantic import BaseModel, Field

from app.core.clock import Clock, Scheduler
from app.core.types import JsonValue
from app.core.utils.shared_future import _await_task_deferring_cancellation
from app.db.models import ModelSource
from app.modules.model_sources.anthropic_credentials import ClaudeAccount
from app.modules.model_sources.anthropic_protocol import ClaudeRequest
from app.modules.model_sources.forwarding import (
    ModelSourceForwardingError,
    SourceStreamTransport,
    source_stream_idle_seconds,
)

TOOL_PREFIX = "mcp__codex__"
_CONTINUE = "Continue the current request using the completed tool results in the conversation above."


@dataclass(frozen=True)
class NativeTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime


class _OAuthFile(BaseModel):
    accessToken: str = Field(min_length=1, repr=False)
    refreshToken: str = Field(min_length=1, repr=False)
    expiresAt: int = Field(gt=0)


@dataclass(frozen=True)
class NativeStream:
    body: AsyncIterator[bytes]
    transport: SourceStreamTransport
    upstream_status_code: int = 200


OnNativeCredentials = Callable[[NativeTokens, bool], Awaitable[None]]


def native_error(status: int, code: str = "claude_native_failed") -> ModelSourceForwardingError:
    return ModelSourceForwardingError(
        status_code=status if status in {400, 401, 403, 429} else 502,
        upstream_status_code=status,
        payload={"error": {"type": "upstream_error", "code": code, "message": "The native Claude request failed"}},
    )


def _write_credentials(directory: Path, account: ClaudeAccount) -> Path:
    path = directory / ".credentials.json"
    data = {
        "claudeAiOauth": {
            "accessToken": account.access_token,
            "refreshToken": account.refresh_token,
            "expiresAt": int(account.expires_at.timestamp() * 1000),
            "scopes": [
                "org:create_api_key",
                "user:profile",
                "user:inference",
                "user:sessions:claude_code",
                "user:mcp_servers",
                "user:file_upload",
            ],
        }
    }
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return path


def _read_credentials(path: Path) -> NativeTokens:
    try:
        values = _OAuthFile.model_validate(json.loads(path.read_text())["claudeAiOauth"])
        return NativeTokens(
            values.accessToken, values.refreshToken, datetime.fromtimestamp(values.expiresAt / 1000, UTC)
        )
    except Exception:
        raise native_error(502, "claude_native_credentials_error") from None


def _native_history(
    request: ClaudeRequest, cwd: Path, config: Path, *, clock: Clock
) -> tuple[str | None, list[JsonValue]]:
    # File codec is pinned to the bundled CLI version. Full native message roles,
    # images and tool IDs survive; conversation history is never flattened to text.
    messages = cast(list[dict[str, JsonValue]], json.loads(json.dumps(request.payload["messages"])))
    if not messages or messages[-1].get("role") != "user":
        raise native_error(400, "claude_requires_user_input")
    for message in messages:
        for block in cast(list[dict[str, JsonValue]], message["content"]):
            if block.get("type") == "tool_use":
                block["name"] = TOOL_PREFIX + cast(str, block["name"])
    last = cast(list[JsonValue], messages[-1]["content"])
    # Native resume repairs unfinished calls before accepting a new prompt.
    # Seed completed external results with their calls, then trigger continuation.
    last_result = max(
        (index for index, part in enumerate(last) if isinstance(part, dict) and part.get("type") == "tool_result"),
        default=-1,
    )
    if last_result >= 0:
        # Codex appends compaction and other follow-up instructions after tool
        # outputs. Keep those instructions as the real prompt, rather than
        # replacing them with a generic request to continue the original task.
        prompt = last[last_result + 1 :] or [{"type": "text", "text": _CONTINUE}]
        messages[-1]["content"] = last[: last_result + 1]
        history = messages
    else:
        history, prompt = messages[:-1], last
    if not history:
        return None, prompt
    session_id = str(uuid4())
    parent: str | None = None
    entries = []
    for message in history:
        role = cast(str, message["role"])
        if role == "assistant":
            blocks = cast(list[dict[str, JsonValue]], message["content"])
            message = {
                **message,
                "id": f"msg_{uuid4().hex}",
                "type": "message",
                "model": request.payload["model"],
                "stop_reason": "tool_use" if any(block.get("type") == "tool_use" for block in blocks) else "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        identifier = str(uuid4())
        entries.append(
            {
                "type": role,
                "uuid": identifier,
                "parentUuid": parent,
                "sessionId": session_id,
                "isSidechain": False,
                "userType": "external",
                "cwd": str(cwd),
                "version": __cli_version__,
                "gitBranch": "",
                "timestamp": clock.now().isoformat(),
                "message": message,
            }
        )
        parent = identifier
    folder = config / "projects" / project_key_for_directory(str(cwd))
    folder.mkdir(parents=True)
    path = folder / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    path.chmod(0o600)
    return session_id, prompt


async def _inert_tool(_arguments: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": "Execution is delegated to the Codex client."}], "isError": True}


async def _deny_server_execution(_data: Any, _tool_id: str | None, _context: Any) -> SyncHookJSONOutput:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Execution is delegated to the Codex client.",
        }
    }


def _options(
    request: ClaudeRequest, account: ClaudeAccount, cwd: Path, config: Path, *, seconds: float, clock: Clock
) -> tuple[ClaudeAgentOptions, list[JsonValue]]:
    resume, prompt = _native_history(request, cwd, config, clock=clock)
    declarations = cast(list[dict[str, Any]], request.payload.get("tools", []))
    choice = request.payload.get("tool_choice")
    if isinstance(choice, dict) and choice.get("type") == "none":
        declarations = []
    elif isinstance(choice, dict) and choice.get("type") != "auto":
        raise native_error(400, "claude_native_tool_choice_unsupported")
    if declarations and isinstance(choice, dict) and choice.get("disable_parallel_tool_use"):
        raise native_error(400, "claude_native_serial_tools_unsupported")
    if "temperature" in request.original or "top_p" in request.original:
        raise native_error(400, "claude_native_sampling_unsupported")
    server = create_sdk_mcp_server(
        "codex",
        tools=[
            SdkMcpTool(tool["name"], tool.get("description", ""), tool["input_schema"], _inert_tool)
            for tool in declarations
        ],
    )
    system = cast(list[dict[str, Any]], request.payload.get("system", []))
    output_config = cast(dict[str, str], request.payload.get("output_config", {}))
    reasoning = cast(dict[str, Any], request.original.get("reasoning") or {})
    thinking = request.payload.get("thinking")
    if reasoning.get("effort") in {"none", "minimal"}:
        thinking = {"type": "disabled"}
    env = {
        "CLAUDE_CONFIG_DIR": str(config),
        "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": "",
        "CLAUDE_CODE_OAUTH_TOKEN": "",
        "CLAUDE_CODE_USE_BEDROCK": "",
        "CLAUDE_CODE_USE_VERTEX": "",
        "CLAUDE_CODE_USE_FOUNDRY": "",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "ENABLE_TOOL_SEARCH": "false",
        "CLAUDE_CODE_MAX_RETRIES": "0",
        "CLAUDE_CODE_RETRY_WATCHDOG": "",
        "API_TIMEOUT_MS": str(max(1, int(seconds * 1000))),
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(request.payload["max_tokens"]),
    }
    options = ClaudeAgentOptions(
        model=cast(str, request.payload["model"]),
        tools=[],
        mcp_servers={"codex": server} if declarations else {},
        strict_mcp_config=True,
        setting_sources=[],
        skills=[],
        agents={},
        permission_mode="default",
        max_turns=1,
        cwd=str(cwd),
        system_prompt="\n\n".join(str(part["text"]) for part in system),
        resume=resume,
        include_partial_messages=True,
        effort=cast(Any, output_config.get("effort", "medium")),
        thinking=cast(Any, thinking),
        hooks={"PreToolUse": [HookMatcher(hooks=[_deny_server_execution])]},
        env=env,
        stderr=lambda _line: None,
    )
    return options, prompt


def _message_error(message: AssistantMessage | ResultMessage) -> ModelSourceForwardingError:
    if isinstance(message, AssistantMessage):
        status = {"authentication_failed": 401, "billing_error": 403, "rate_limit": 429, "invalid_request": 400}.get(
            str(message.error), 502
        )
    else:
        match = re.search(r"\bAPI Error:\s*(\d{3})\b", message.result or "")
        status = int(match[1]) if match else 502
    return native_error(status)


async def open_native_stream(
    source: ModelSource,
    request: ClaudeRequest,
    account: ClaudeAccount,
    *,
    seconds: float,
    on_credentials: OnNativeCredentials,
    on_close: Callable[[], Awaitable[None]],
    scheduler: Scheduler,
    clock: Clock,
) -> NativeStream:
    queue: asyncio.Queue[bytes | ModelSourceForwardingError | None] = asyncio.Queue(maxsize=16)

    async def worker() -> None:
        failure: ModelSourceForwardingError | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="codex-lb-claude-") as name:
                directory = Path(name).resolve()
                config = directory / "config"
                config.mkdir(mode=0o700)
                credentials = _write_credentials(config, account)
                try:
                    options, prompt = _options(request, account, directory, config, seconds=seconds, clock=clock)

                    async def user_message() -> AsyncIterator[dict[str, Any]]:
                        yield {"type": "user", "message": {"role": "user", "content": prompt}}

                    with scheduler.fail_after(seconds):
                        async with ClaudeSDKClient(options=options) as client:
                            await client.query(user_message())
                            async for message in client.receive_messages():
                                if isinstance(message, StreamEvent):
                                    event = message.event
                                    if event.get("type") == "message_start":
                                        await on_credentials(_read_credentials(credentials), False)
                                    block = event.get("content_block")
                                    if isinstance(block, dict) and block.get("type") == "tool_use":
                                        block = dict(block)
                                        block["name"] = str(block.get("name", "")).removeprefix(TOOL_PREFIX)
                                        event = {**event, "content_block": block}
                                    await queue.put(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode())
                                    if event.get("type") == "message_stop":
                                        break
                                elif isinstance(message, AssistantMessage) and message.error is not None:
                                    raise _message_error(message)
                                elif isinstance(message, ResultMessage):
                                    if message.is_error:
                                        raise _message_error(message)
                                    break
                finally:
                    # SDK contexts have closed in their owning task before a
                    # cancellation-deferring DB callback can run in another task.
                    from app.core.utils.shared_future import _await_cleanup_deferring_cancellation

                    cancellation = await _await_cleanup_deferring_cancellation(
                        on_credentials(_read_credentials(credentials), True), scheduler=scheduler
                    )
                    if cancellation is not None:
                        raise cancellation
        except asyncio.CancelledError:
            raise
        except ModelSourceForwardingError as exc:
            failure = exc
        except TimeoutError:
            failure = native_error(504, "model_source_timeout")
        except Exception:
            failure = native_error(502)
        # A cancelled consumer may leave this bounded queue full. Do not await
        # another write from a finally block after cancellation interrupts one.
        if failure is not None:
            await queue.put(failure)
        await queue.put(None)

    stack = AsyncExitStack()
    stack.push_async_callback(on_close)
    task = scheduler.create_task(worker())

    async def stop() -> None:
        if not task.done():
            task.cancel()
        try:
            await _await_task_deferring_cancellation(task)
        except asyncio.CancelledError:
            pass

    stack.push_async_callback(stop)
    transport = SourceStreamTransport(stack, scheduler=scheduler)

    async def next_frame() -> bytes | None:
        item = await scheduler.wait_for(queue.get(), source_stream_idle_seconds())
        if isinstance(item, ModelSourceForwardingError):
            raise item
        return item

    try:
        with scheduler.fail_after(min(30.0, seconds)):
            first = await next_frame()
        if first is None:
            raise native_error(502, "claude_native_empty_stream")
    except BaseException:
        await transport.aclose()
        raise

    async def body() -> AsyncIterator[bytes]:
        try:
            yield first
            while (chunk := await next_frame()) is not None:
                yield chunk
        finally:
            await transport.aclose()

    return NativeStream(body(), transport)
