"""Anthropic Messages ⇄ OpenAI Chat Completions translation (WF-DESIGN-0011).

Pure, offline format translation so an Anthropic-Messages-native client (notably
**Claude Code**, via ``ANTHROPIC_BASE_URL``) can use the gateway. The adapter scores
nothing and calls no model (WF-ADR-0001): the gateway's ``/v1/chat/completions`` handler
remains the single router, and the endpoint in :mod:`gateway` delegates to it. This module
only reshapes the request going in and the reply coming out — both directions, buffered and
streamed.

Everything here is a pure function of dicts / iterables, so it is fully unit-testable with no
network and no keys. The streaming side is a small state machine (:class:`MessagesStreamTranslator`)
that turns OpenAI SSE chunks into the Anthropic event sequence
(``message_start`` → ``content_block_*`` → ``message_delta`` → ``message_stop``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Mapping

from .pricing import estimate_tokens

# OpenAI ``finish_reason`` → Anthropic ``stop_reason``.
_STOP_REASONS = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}

# HTTP status → Anthropic error ``type`` (best-effort nearest match).
_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    402: "invalid_request_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    422: "invalid_request_error",
    429: "rate_limit_error",
    500: "api_error",
    503: "overloaded_error",
}


# --- shared helpers ---------------------------------------------------------
def _flatten_text(value: object) -> str:
    """Text of an Anthropic content value — a string, or a list of text blocks."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if isinstance(block, Mapping) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


def _stop_reason(finish_reason: object) -> str:
    return _STOP_REASONS.get(finish_reason, "end_turn") if isinstance(finish_reason, str) else "end_turn"


# --- request: Anthropic Messages → OpenAI Chat Completions ------------------
def _translate_tool(tool: Mapping) -> dict:
    """An Anthropic tool (``name``/``description``/``input_schema``) → OpenAI function tool."""
    fn: dict = {"name": tool.get("name", ""), "parameters": tool.get("input_schema") or {}}
    if isinstance(tool.get("description"), str) and tool["description"]:
        fn["description"] = tool["description"]
    return {"type": "function", "function": fn}


def _translate_tool_choice(choice: object) -> object:
    """Anthropic ``tool_choice`` → OpenAI ``tool_choice`` (tolerates an already-OpenAI value)."""
    if isinstance(choice, str):
        return choice
    if isinstance(choice, Mapping):
        kind = choice.get("type")
        if kind == "auto":
            return "auto"
        if kind == "any":
            return "required"
        if kind == "none":
            return "none"
        if kind == "tool" and choice.get("name"):
            return {"type": "function", "function": {"name": choice["name"]}}
    return "auto"


def _translate_in_message(message: Mapping) -> list[dict]:
    """One Anthropic message → the OpenAI message(s) it becomes.

    A user turn carrying ``tool_result`` blocks expands into ``role:"tool"`` messages (which
    must precede any remaining user text, mirroring OpenAI's expected ordering); an assistant
    turn with ``tool_use`` blocks becomes an assistant message with ``tool_calls``.
    """
    role = message.get("role")
    content = message.get("content")
    if isinstance(content, str):
        return [{"role": role, "content": content}]
    if not isinstance(content, list):
        return []

    text_parts: list[str] = []
    tool_calls: list[dict] = []
    tool_msgs: list[dict] = []
    for block in content:
        if not isinstance(block, Mapping):
            continue
        btype = block.get("type")
        if btype == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif btype == "tool_use":  # assistant invoking a tool
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input") or {}, separators=(",", ":")),
                },
            })
        elif btype == "tool_result":  # user returning a tool's output
            tool_msgs.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": _flatten_text(block.get("content")),
            })
        # image / unknown blocks are skipped (vision deferred, WF-DESIGN-0011)

    msgs: list[dict] = list(tool_msgs)  # tool results answer the prior assistant call first
    if role == "assistant":
        text = "\n".join(text_parts) if text_parts else None
        if text is not None or tool_calls:
            amsg: dict = {"role": "assistant", "content": text}
            if tool_calls:
                amsg["tool_calls"] = tool_calls
            msgs.append(amsg)
    elif text_parts:
        msgs.append({"role": role, "content": "\n".join(text_parts)})
    return msgs


def anthropic_to_openai_request(body: Mapping) -> dict:
    """Translate an Anthropic ``/v1/messages`` request body into an OpenAI chat body.

    The ``model`` is passed through so the gateway's existing ``resolve_pin`` decides (an
    unconfigured Anthropic id like ``claude-opus-4-…`` falls through to score-and-route).
    """
    out: dict = {"model": body.get("model", "auto")}

    messages: list[dict] = []
    system = _flatten_text(body.get("system"))
    if system:
        messages.append({"role": "system", "content": system})
    for message in body.get("messages") or []:
        if isinstance(message, Mapping):
            messages.extend(_translate_in_message(message))
    out["messages"] = messages

    if "max_tokens" in body:
        out["max_tokens"] = body["max_tokens"]
    for key in ("temperature", "top_p"):
        if key in body:
            out[key] = body[key]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]
    if body.get("stream"):
        out["stream"] = True
    if body.get("tools"):
        out["tools"] = [_translate_tool(t) for t in body["tools"] if isinstance(t, Mapping)]
    if body.get("tool_choice") is not None:
        out["tool_choice"] = _translate_tool_choice(body["tool_choice"])
    return out


# --- response: OpenAI Chat Completions → Anthropic Messages (buffered) ------
def _usage(response: object, *, prompt_text: str = "", completion_text: str = "") -> tuple[int, int]:
    """``(input_tokens, output_tokens)`` — the upstream ``usage`` if present, else estimated."""
    if isinstance(response, Mapping):
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if isinstance(pt, int) and isinstance(ct, int):
                return pt, ct
    return estimate_tokens(prompt_text), estimate_tokens(completion_text)


def openai_to_anthropic_response(
    response: Mapping, *, model: str, message_id: str, prompt_text: str = ""
) -> dict:
    """Translate a non-streaming OpenAI chat completion into an Anthropic ``message`` object."""
    choice = (response.get("choices") or [{}])[0]
    msg = choice.get("message") or {}

    content: list[dict] = []
    text = msg.get("content")
    completion_text = text if isinstance(text, str) else ""
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        completion_text += fn.get("arguments") or ""
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {}
        content.append({
            "type": "tool_use",
            "id": tc.get("id") or f"toolu_{i}",
            "name": fn.get("name", ""),
            "input": args,
        })
    if not content:  # Anthropic messages always carry at least one block
        content.append({"type": "text", "text": ""})

    input_tokens, output_tokens = _usage(
        response, prompt_text=prompt_text, completion_text=completion_text
    )
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": _stop_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def anthropic_error(status: int, message: str) -> dict:
    """An Anthropic error envelope for a non-2xx (or untranslatable) upstream reply."""
    return {"type": "error", "error": {"type": _ERROR_TYPES.get(status, "api_error"), "message": message}}


# --- response: OpenAI SSE → Anthropic SSE (streaming) -----------------------
def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def _parse_sse_data(line: str) -> object | None:
    """Parse one ``data:`` line into a chunk dict, the ``"[DONE]"`` sentinel, or ``None``."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if payload == "[DONE]":
        return "[DONE]"
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


class MessagesStreamTranslator:
    """Turn a stream of OpenAI chat chunks into the Anthropic SSE event sequence.

    A small state machine: it opens a content block on the first text/tool delta, emits deltas,
    and closes blocks in order (Anthropic blocks are sequential — switching block type closes the
    current one first). ``start`` / ``feed`` / ``finish`` each return a list of encoded SSE events,
    so the whole thing is drivable synchronously in a test.
    """

    def __init__(self, *, model: str, message_id: str, input_tokens: int = 0) -> None:
        self.model = model
        self.message_id = message_id
        self.input_tokens = input_tokens
        self._next_index = 0
        self._current: tuple | None = None  # ("text", idx) | ("tool", openai_idx, idx)
        self._finish_reason: object = None
        self._output_tokens = 0
        self._usage_seen = False
        self._completion: list[str] = []  # for the output-token estimate when usage is absent

    def start(self) -> list[bytes]:
        message: dict = {
            "id": self.message_id,
            "type": "message",
            "role": "assistant",
            "model": self.model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": self.input_tokens, "output_tokens": 0},
        }
        return [_sse("message_start", {"type": "message_start", "message": message})]

    def _close_current(self) -> list[bytes]:
        if self._current is None:
            return []
        index = self._current[-1]
        self._current = None
        return [_sse("content_block_stop", {"type": "content_block_stop", "index": index})]

    def feed(self, chunk: Mapping) -> list[bytes]:
        out: list[bytes] = []
        usage = chunk.get("usage")
        if isinstance(usage, Mapping):
            if isinstance(usage.get("completion_tokens"), int):
                self._output_tokens = usage["completion_tokens"]
                self._usage_seen = True
            if isinstance(usage.get("prompt_tokens"), int):
                self.input_tokens = usage["prompt_tokens"]

        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if isinstance(text, str) and text:
                if self._current is None or self._current[0] != "text":
                    out += self._close_current()
                    index = self._next_index
                    self._next_index += 1
                    self._current = ("text", index)
                    out.append(_sse("content_block_start", {
                        "type": "content_block_start", "index": index,
                        "content_block": {"type": "text", "text": ""},
                    }))
                self._completion.append(text)
                out.append(_sse("content_block_delta", {
                    "type": "content_block_delta", "index": self._current[-1],
                    "delta": {"type": "text_delta", "text": text},
                }))

            for tc in delta.get("tool_calls") or []:
                if not isinstance(tc, Mapping):
                    continue
                oidx = tc.get("index", 0)
                fn = tc.get("function") or {}
                if not (self._current and self._current[0] == "tool" and self._current[1] == oidx):
                    out += self._close_current()
                    index = self._next_index
                    self._next_index += 1
                    self._current = ("tool", oidx, index)
                    out.append(_sse("content_block_start", {
                        "type": "content_block_start", "index": index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tc.get("id") or f"toolu_{index}",
                            "name": fn.get("name") or "",
                            "input": {},
                        },
                    }))
                args = fn.get("arguments")
                if isinstance(args, str) and args:
                    self._completion.append(args)
                    out.append(_sse("content_block_delta", {
                        "type": "content_block_delta", "index": self._current[-1],
                        "delta": {"type": "input_json_delta", "partial_json": args},
                    }))

            if choice.get("finish_reason"):
                self._finish_reason = choice["finish_reason"]
        return out

    def finish(self) -> list[bytes]:
        out: list[bytes] = []
        if self._next_index == 0:  # nothing streamed — emit one empty text block
            out.append(_sse("content_block_start", {
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""},
            }))
            out.append(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
        else:
            out += self._close_current()
        if not self._usage_seen and self._output_tokens == 0:
            self._output_tokens = estimate_tokens("".join(self._completion))
        out.append(_sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": _stop_reason(self._finish_reason), "stop_sequence": None},
            "usage": {"output_tokens": self._output_tokens},
        }))
        out.append(_sse("message_stop", {"type": "message_stop"}))
        return out


def translate_sse_chunks(
    chunks: Iterable[object], *, model: str, message_id: str, input_tokens: int = 0
) -> list[bytes]:
    """Drive :class:`MessagesStreamTranslator` over OpenAI chunk dicts — sync, for tests."""
    translator = MessagesStreamTranslator(
        model=model, message_id=message_id, input_tokens=input_tokens
    )
    out = list(translator.start())
    for chunk in chunks:
        if chunk == "[DONE]":
            return out + translator.finish()
        if isinstance(chunk, Mapping):
            out += translator.feed(chunk)
    return out + translator.finish()


async def messages_stream(
    byte_iter: AsyncIterator[bytes], *, model: str, message_id: str, input_tokens: int = 0
) -> AsyncIterator[bytes]:
    """Async wrapper: buffer upstream OpenAI SSE bytes, emit translated Anthropic SSE bytes."""
    translator = MessagesStreamTranslator(
        model=model, message_id=message_id, input_tokens=input_tokens
    )
    for event in translator.start():
        yield event
    buffer = ""
    async for chunk in byte_iter:
        buffer += chunk.decode("utf-8", "ignore") if isinstance(chunk, bytes) else chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            obj = _parse_sse_data(line)
            if obj is None:
                continue
            if obj == "[DONE]":
                for event in translator.finish():
                    yield event
                return
            if isinstance(obj, Mapping):
                for event in translator.feed(obj):
                    yield event
    for event in translator.finish():
        yield event
