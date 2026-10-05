"""Tool-calling loops for the model providers, streamed as events.

Each provider is a generator of event dicts:
  {"type": "token", "text": ...}          part of the answer
  {"type": "discard"}                     drop the answer text streamed so far
                                          (it was a preamble to a tool call)
  {"type": "tool_start", "tool", "label"} a tool is about to run
  {"type": "tool_result", ...}            see tools.execute
"""

import json
import logging
import re
import time
from collections.abc import Iterator

from . import config
from .tools import TOOL_LABELS, TOOLS, RequestState, execute

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 8  # safety cap on tool-call rounds
_NUDGE = (
    "Lệnh vừa rồi bị lỗi. Hãy sửa theo thông báo lỗi và gọi lại công cụ ngay, "
    "chưa trả lời người dùng."
)
# model id -> time until which it is skipped after a quota (429) error.
_exhausted: dict[str, float] = {}
_EXHAUSTED_TTL = 60 * 60  # seconds


class ModelsUnavailable(RuntimeError):
    """The model provider is unreachable, overloaded or out of quota."""


def _run_tool(name: str, args: dict, state: RequestState) -> Iterator[dict]:
    """Yield the progress events of one tool call; the result is the last item."""
    yield {"type": "tool_start", "tool": name, "label": TOOL_LABELS.get(name, name)}
    result, event = execute(name, args, state)
    yield event
    yield {"type": "_result", "result": result, "failed": not event["ok"]}


def stream(prompt: str, question: str, history: list[dict], state: RequestState) -> Iterator[dict]:
    if config.provider() == "ollama":
        yield from _ollama(prompt, question, history, state)
    else:
        yield from _gemini(prompt, question, history, state)


# --------------------------------------------------------------------------
# Ollama (local / internal AI)
# --------------------------------------------------------------------------


def _ollama(prompt: str, question: str, history: list[dict], state: RequestState) -> Iterator[dict]:
    import requests

    messages = [{"role": "system", "content": prompt}]
    messages += [
        {"role": "assistant" if t["role"] == "model" else "user", "content": t["text"]}
        for t in history
    ]
    messages.append({"role": "user", "content": question})
    tools = [{"type": "function", "function": t} for t in TOOLS]
    timeout = config.ollama_timeout()
    last_failed, nudges = False, 0

    for _ in range(_MAX_ROUNDS):
        try:
            resp = requests.post(
                f"{config.ollama_url()}/api/chat",
                json={
                    "model": config.ollama_model(),
                    "messages": messages,
                    "tools": tools,
                    "stream": True,
                    # Reasoning mode multiplies the wait on CPU-only machines.
                    "think": False,
                    "keep_alive": "30m",
                    "options": {"temperature": 0, "num_ctx": config.ollama_ctx()},
                },
                stream=True,
                timeout=timeout,
            )
        except requests.ConnectionError as ex:
            raise ModelsUnavailable(
                f"Không kết nối được AI nội bộ (Ollama) tại {config.ollama_url()}. "
                "Kiểm tra Ollama đã chạy chưa."
            ) from ex
        if resp.status_code != 200:
            raise ModelsUnavailable(f"AI nội bộ báo lỗi: {resp.text[:300]}")

        content, calls = "", []
        try:
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                msg = chunk.get("message") or {}
                if msg.get("content"):
                    content += msg["content"]
                    yield {"type": "token", "text": msg["content"]}
                calls += msg.get("tool_calls") or []
                if chunk.get("error"):
                    raise ModelsUnavailable(f"AI nội bộ báo lỗi: {chunk['error']}")
        except requests.Timeout as ex:
            raise ModelsUnavailable(
                f"AI nội bộ không trả lời trong {timeout} giây. Thử câu hỏi ngắn hơn "
                "hoặc dùng máy có GPU."
            ) from ex

        if calls:
            if content:
                yield {"type": "discard"}
            messages.append({"role": "assistant", "content": content, "tool_calls": calls})
            for call in calls:
                fn = call.get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                for event in _run_tool(fn.get("name", ""), args, state):
                    if event["type"] == "_result":
                        last_failed = event["failed"]
                        messages.append(
                            {"role": "tool", "tool_name": fn.get("name", ""),
                             "content": event["result"]}
                        )
                    else:
                        yield event
            continue

        if last_failed and nudges < 2:
            # Small models often reply "I'll fix it" and stop; push them to retry.
            nudges += 1
            last_failed = False
            yield {"type": "discard"}
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": _NUDGE})
            continue
        if "<think>" in content:
            yield {"type": "discard"}
            yield {"type": "token",
                   "text": re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()}
        return

    yield {"type": "token", "text": "\n\nXin lỗi, tôi không hoàn tất được yêu cầu (quá nhiều bước)."}


# --------------------------------------------------------------------------
# Gemini (Google)
# --------------------------------------------------------------------------


def _gemini(prompt: str, question: str, history: list[dict], state: RequestState) -> Iterator[dict]:
    from google import genai
    from google.genai import types

    if not config.gemini_api_key():
        raise ModelsUnavailable("Chưa cấu hình GEMINI_API_KEY trên máy chủ Superset.")

    client = genai.Client(api_key=config.gemini_api_key())
    gen_config = types.GenerateContentConfig(
        system_instruction=prompt,
        tools=[types.Tool(function_declarations=TOOLS)],
        temperature=0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents = [
        types.Content(role=t["role"], parts=[types.Part(text=t["text"])]) for t in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part(text=question)]))

    def open_stream():
        """Start a stream, retrying brief overloads and skipping models out of quota."""
        models = config.gemini_models()
        now = time.time()
        available = [m for m in models if _exhausted.get(m, 0) <= now] or models
        overloaded = False
        for model in available:
            for attempt in range(2):
                try:
                    it = client.models.generate_content_stream(
                        model=model, contents=contents, config=gen_config
                    )
                    first = next(it, None)  # errors surface on the first chunk
                    return first, it
                except Exception as ex:  # pylint: disable=broad-except
                    msg = str(ex)
                    if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
                        _exhausted[model] = time.time() + _EXHAUSTED_TTL
                        logger.warning("hospital-chat: quota exhausted for %s", model)
                        break
                    if "503" in msg or "UNAVAILABLE" in msg:
                        overloaded = True
                        if attempt == 0:
                            time.sleep(3)
                            continue
                        break
                    raise
        raise ModelsUnavailable(
            "Máy chủ AI (Google Gemini) đang quá tải, vui lòng thử lại sau ít phút."
            if overloaded
            else "Đã hết hạn mức sử dụng Gemini hôm nay cho tất cả các model đã cấu hình. "
            "Vui lòng thử lại sau, hoặc nâng cấp gói trả phí / thêm model vào "
            "HOSPITAL_CHAT_FALLBACK_MODELS."
        )

    for _ in range(_MAX_ROUNDS):
        first, rest = open_stream()
        parts, calls, streamed = [], [], False
        for chunk in [first, *rest] if first is not None else []:
            candidate = (chunk.candidates or [None])[0]
            for part in (candidate.content.parts if candidate and candidate.content else None) or []:
                parts.append(part)  # keeps thought signatures for the next round
                if part.function_call:
                    calls.append(part.function_call)
                elif part.text and not part.thought:
                    streamed = True
                    yield {"type": "token", "text": part.text}
        if not calls:
            return
        if streamed:
            yield {"type": "discard"}
        contents.append(types.Content(role="model", parts=parts))
        responses = []
        for fc in calls:
            for event in _run_tool(fc.name, dict(fc.args or {}), state):
                if event["type"] == "_result":
                    responses.append(
                        types.Part.from_function_response(
                            name=fc.name, response={"result": event["result"]}
                        )
                    )
                else:
                    yield event
        contents.append(types.Content(role="user", parts=responses))

    yield {"type": "token", "text": "\n\nXin lỗi, tôi không hoàn tất được yêu cầu (quá nhiều bước)."}
