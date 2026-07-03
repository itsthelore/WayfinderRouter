"""Anthropic Messages ⇄ OpenAI Chat Completions translation (WF-DESIGN-0011).

A pure, offline shim so an Anthropic-Messages-native client (notably **Claude Code**,
pointed here via ``ANTHROPIC_BASE_URL``) can talk to the gateway. Nothing here scores or
calls a model (WF-ADR-0001): the gateway's ``/v1/chat/completions`` handler stays the one
router, and the ``/v1/messages`` endpoint delegates to it. This module only reshapes the
request on the way in and the reply on the way out — in both the buffered and streamed
directions.

Every function is a pure transform over dicts / iterables, so it is fully unit-testable
with no network and no keys. The streaming half is a small state machine
(:class:`MessagesStreamTranslator`) that rewrites OpenAI SSE chunks into the Anthropic
event order (``message_start`` → ``content_block_*`` → ``message_delta`` → ``message_stop``).

Wire-format note that recurs throughout: every ``data:`` payload and every embedded JSON
string (tool-call ``arguments``, ``partial_json``) is serialized with
``separators=(",", ":")``. The compact form is contract — golden tests pin the exact bytes
(e.g. ``{"q":"cats"}``, not ``{"q": "cats"}``) at both the unit and gateway layers.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Mapping

from .pricing import estimate_tokens

# OpenAI ``finish_reason`` → Anthropic ``stop_reason``. Anything unmapped, and any
# non-string (including ``None``), falls back to ``end_turn``.
_STOP_REASONS = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "end_turn",
}

# HTTP status → Anthropic error ``type``. This is a per-status *lookup*, not a 4xx/5xx
# range rule: the default for any code absent from the table (e.g. 418, 502) is
# ``api_error``. Reproduce the entries verbatim — 402 mapping to invalid_request_error is
# explicit, not derived.
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
    """Collapse an Anthropic content value to plain text.

    A value is either a bare string, or a list of blocks. From a list we keep each block
    that is a string, or a mapping carrying a string ``text``, and join the pieces with
    newlines. Anything else (or ``None``) flattens to the empty string.
    """
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
    if isinstance(finish_reason, str):
        return _STOP_REASONS.get(finish_reason, "end_turn")
    return "end_turn"


# --- request: Anthropic Messages → OpenAI Chat Completions ------------------
def _translate_tool(tool: Mapping) -> dict:
    """An Anthropic tool spec (``name`` / ``input_schema`` / ``description``) → OpenAI function tool."""
    function: dict = {"name": tool.get("name", ""), "parameters": tool.get("input_schema") or {}}
    description = tool.get("description")
    if isinstance(description, str) and description:
        function["description"] = description
    return {"type": "function", "function": function}


def _translate_tool_choice(choice: object) -> object:
    """Anthropic ``tool_choice`` → OpenAI ``tool_choice``.

    A bare string is assumed to already be an OpenAI value and passed through. A mapping is
    switched on its ``type``: auto→"auto", any→"required", none→"none", and a named tool→a
    function selector. Anything unrecognized defaults to "auto".
    """
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
    """Expand one inbound Anthropic message into the OpenAI message(s) it becomes.

    String content maps straight to one message (role passed through as-is). List content
    is split by block type into text parts, ``tool_use`` calls, and ``tool_result`` returns.
    The critical ordering: ``tool`` messages (a user turn answering the prior assistant
    call) come *first*, before any trailing text — mirroring what OpenAI expects. An
    assistant turn folds its text and tool_calls into a single message whose ``content`` may
    be ``None`` when it only made tool calls.
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
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            text_parts.append(block["text"])
        elif block_type == "tool_use":  # assistant invoking a tool
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    # Compact separators are contract: the golden pins ``{"q":"cats"}``.
                    "arguments": json.dumps(block.get("input") or {}, separators=(",", ":")),
                },
            })
        elif block_type == "tool_result":  # user returning a tool's output
            tool_msgs.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": _flatten_text(block.get("content")),
            })
        # image / unknown blocks are silently dropped (vision deferred, WF-DESIGN-0011).

    # Tool results lead, answering the prior assistant call before any fresh text.
    messages: list[dict] = list(tool_msgs)
    if role == "assistant":
        text = "\n".join(text_parts) if text_parts else None
        if text is not None or tool_calls:
            assistant: dict = {"role": "assistant", "content": text}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            messages.append(assistant)
    elif text_parts:
        messages.append({"role": role, "content": "\n".join(text_parts)})
    return messages


def anthropic_to_openai_request(body: Mapping) -> dict:
    """Translate an Anthropic ``/v1/messages`` body into an OpenAI chat-completions body.

    ``model`` is passed through untouched so the gateway's ``resolve_pin`` still decides
    routing (an unconfigured Anthropic id falls through to score-and-route); absent, it
    defaults to ``"auto"``. Sampling knobs are carried by key presence, ``stop_sequences``
    and ``tools`` / ``tool_choice`` by truthiness, ``stream`` is normalized to literal
    ``True``, and ``top_k`` (plus any other Anthropic-only field) is dropped.
    """
    out: dict = {"model": body.get("model", "auto")}

    messages: list[dict] = []
    system = _flatten_text(body.get("system"))
    if system:  # only prepend a system message when there is actual system text
        messages.append({"role": "system", "content": system})
    for message in body.get("messages") or []:
        if isinstance(message, Mapping):
            messages.extend(_translate_in_message(message))
    out["messages"] = messages

    # max_tokens carries by key presence — even a present 0 / None is forwarded verbatim.
    if "max_tokens" in body:
        out["max_tokens"] = body["max_tokens"]
    for key in ("temperature", "top_p"):
        if key in body:
            out[key] = body[key]
    if body.get("stop_sequences"):
        out["stop"] = body["stop_sequences"]
    if body.get("stream"):
        out["stream"] = True  # literal True, not the passthrough truthy value
    if body.get("tools"):
        out["tools"] = [_translate_tool(t) for t in body["tools"] if isinstance(t, Mapping)]
    if body.get("tool_choice") is not None:
        out["tool_choice"] = _translate_tool_choice(body["tool_choice"])
    return out


# --- response: OpenAI Chat Completions → Anthropic Messages (buffered) ------
def _usage(response: object, *, prompt_text: str = "", completion_text: str = "") -> tuple[int, int]:
    """Return ``(input_tokens, output_tokens)`` — upstream usage when trustworthy, else estimated.

    Upstream usage is only trusted when *both* ``prompt_tokens`` and ``completion_tokens``
    are ints; a partial/missing usage falls back to a full estimate of both sides (never a
    mix). Estimation is ``pricing.estimate_tokens`` (~4 chars/token, min 1 for non-empty).
    """
    if isinstance(response, Mapping):
        usage = response.get("usage")
        if isinstance(usage, Mapping):
            prompt_tokens, completion_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
                return prompt_tokens, completion_tokens
    return estimate_tokens(prompt_text), estimate_tokens(completion_text)


def openai_to_anthropic_response(
    response: Mapping, *, model: str, message_id: str, prompt_text: str = ""
) -> dict:
    """Translate a non-streaming OpenAI chat completion into an Anthropic ``message`` object."""
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}

    content: list[dict] = []
    text = message.get("content")
    completion_text = text if isinstance(text, str) else ""
    if isinstance(text, str) and text:
        content.append({"type": "text", "text": text})
    for i, tool_call in enumerate(message.get("tool_calls") or []):
        function = tool_call.get("function") or {}
        # Accumulate the raw argument text so the token estimate reflects tool output too.
        completion_text += function.get("arguments") or ""
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            arguments = {}  # malformed arguments degrade to empty input, not a crash
        content.append({
            "type": "tool_use",
            "id": tool_call.get("id") or f"toolu_{i}",  # synthesize an id only when absent
            "name": function.get("name", ""),
            "input": arguments,
        })
    if not content:  # an Anthropic message always carries at least one block
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
    """Wrap an upstream failure in an Anthropic error envelope (per-status ``type`` lookup)."""
    return {"type": "error", "error": {"type": _ERROR_TYPES.get(status, "api_error"), "message": message}}


# --- response: OpenAI SSE → Anthropic SSE (streaming) -----------------------
def _sse(event: str, data: dict) -> bytes:
    """Encode one SSE event. The ``event: ``/``data: `` prefixes, compact JSON, and the
    ``\\n\\n`` terminator are all byte-level contract — tests split on those exact tokens."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


def _parse_sse_data(line: str) -> object | None:
    """Parse one line into a chunk mapping, the ``"[DONE]"`` sentinel, or ``None`` to skip.

    Only ``data:`` lines carry payload; blank lines, ``event:`` lines, and unparseable JSON
    all return ``None`` and are ignored by the caller.
    """
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
    """Rewrite a stream of OpenAI chat chunks into the Anthropic SSE event sequence.

    Text streams live as a single incremental content block. Tool calls, by contrast, are
    *buffered* by their OpenAI index (id / name / argument fragments) and only flushed as
    whole ``tool_use`` blocks in :meth:`finish`. OpenAI may interleave parallel tool-call
    deltas across indices, which Anthropic's strictly sequential, one-block-at-a-time stream
    cannot represent — a closed block cannot be reopened — so buffering is the only faithful
    mapping. ``start`` / ``feed`` / ``finish`` each return a list of encoded events, which
    keeps the machine drivable synchronously in tests.
    """

    def __init__(self, *, model: str, message_id: str, input_tokens: int = 0) -> None:
        self.model = model
        self.message_id = message_id
        self.input_tokens = input_tokens
        self._next_index = 0
        # The currently open text block as ``("text", index)``, or None when none is open.
        self._current: tuple | None = None
        # Buffered tool calls keyed by OpenAI index; insertion order is the emit order.
        # Each slot: {"id": str|None, "name": str, "args": [fragment, ...]}.
        self._tools: dict[int, dict] = {}
        self._finish_reason: object = None
        self._output_tokens = 0
        self._usage_seen = False
        # Streamed text + tool-arg fragments, kept for the output-token estimate fallback.
        self._completion: list[str] = []

    def start(self) -> list[bytes]:
        """Emit the opening ``message_start`` event (content empty, output_tokens 0)."""
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
        """Close the open block (if any) with a ``content_block_stop``."""
        if self._current is None:
            return []
        index = self._current[-1]
        self._current = None
        return [_sse("content_block_stop", {"type": "content_block_stop", "index": index})]

    def feed(self, chunk: Mapping) -> list[bytes]:
        """Consume one OpenAI chunk, emitting text-block events; tool deltas are buffered silently."""
        out: list[bytes] = []
        usage = chunk.get("usage")
        if isinstance(usage, Mapping):
            # completion_tokens sets the authoritative output count; prompt_tokens revises
            # input_tokens for the eventual message_delta (no re-emit of message_start).
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
                    # Open a fresh text block (closing any stale one first).
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

            for tool_call in delta.get("tool_calls") or []:
                if not isinstance(tool_call, Mapping):
                    continue
                oindex = tool_call.get("index", 0)
                function = tool_call.get("function") or {}
                slot = self._tools.get(oindex)
                if slot is None:  # first delta for this index fixes its emit position
                    slot = {"id": None, "name": "", "args": []}
                    self._tools[oindex] = slot
                # id / name arrive on the first delta; argument fragments stream across deltas.
                call_id = tool_call.get("id")
                if isinstance(call_id, str) and call_id:
                    slot["id"] = call_id
                name = function.get("name")
                if isinstance(name, str) and name:
                    slot["name"] = name
                arguments = function.get("arguments")
                if isinstance(arguments, str) and arguments:
                    slot["args"].append(arguments)
                    self._completion.append(arguments)

            if choice.get("finish_reason"):  # truthy guard: a None/"" won't clobber a real one
                self._finish_reason = choice["finish_reason"]
        return out

    def finish(self) -> list[bytes]:
        """Close any open text block, flush buffered tools, then emit message_delta/message_stop."""
        out: list[bytes] = []
        out += self._close_current()  # text block closes before any tool block opens
        # Flush buffered tool calls as whole blocks, in first-seen order — each its own
        # start / single concatenated input_json_delta / stop. Concatenating all fragments
        # into ONE delta (not one per fragment) and keeping per-index ids/names is what lets
        # parallel/interleaved calls survive without merging or losing a synthetic block.
        for slot in self._tools.values():
            index = self._next_index
            self._next_index += 1
            out.append(_sse("content_block_start", {
                "type": "content_block_start", "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": slot["id"] or f"toolu_{index}",  # synthesize an id only when absent
                    "name": slot["name"],
                    "input": {},
                },
            }))
            joined = "".join(slot["args"])
            if joined:
                out.append(_sse("content_block_delta", {
                    "type": "content_block_delta", "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": joined},
                }))
            out.append(_sse("content_block_stop", {"type": "content_block_stop", "index": index}))
        if self._next_index == 0:  # nothing streamed at all — emit one empty text block
            out.append(_sse("content_block_start", {
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""},
            }))
            out.append(_sse("content_block_stop", {"type": "content_block_stop", "index": 0}))
        if not self._usage_seen and self._output_tokens == 0:
            # No upstream usage: estimate from the accumulated text + argument fragments.
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
    """Drive :class:`MessagesStreamTranslator` over already-parsed OpenAI chunks (sync, for tests).

    A ``"[DONE]"`` sentinel finishes and returns immediately; running off the end of the
    iterable without one still finishes (closing everything). Non-mapping objects are ignored.
    """
    translator = MessagesStreamTranslator(model=model, message_id=message_id, input_tokens=input_tokens)
    out = list(translator.start())
    for chunk in chunks:
        if chunk == "[DONE]":
            return out + translator.finish()
        if isinstance(chunk, Mapping):
            out += translator.feed(chunk)
    return out + translator.finish()


async def messages_stream(
    byte_iter: AsyncIterable[str | bytes | memoryview],
    *,
    model: str,
    message_id: str,
    input_tokens: int = 0,
) -> AsyncIterator[bytes]:
    """Async wrapper: buffer raw upstream OpenAI SSE bytes, yield translated Anthropic SSE bytes.

    Accepts whatever a Starlette ``StreamingResponse.body_iterator`` yields (``str``,
    ``bytes``, or ``memoryview``), normalizing each piece to text. A ``data:`` line split
    across byte-chunk boundaries is held in ``buffer`` until its terminating ``\\n`` arrives,
    so it parses exactly once.
    """
    translator = MessagesStreamTranslator(model=model, message_id=message_id, input_tokens=input_tokens)
    for event in translator.start():
        yield event
    buffer = ""
    async for chunk in byte_iter:
        buffer += chunk if isinstance(chunk, str) else bytes(chunk).decode("utf-8", "ignore")
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
    # Stream ended without an explicit [DONE] — close everything out.
    for event in translator.finish():
        yield event
