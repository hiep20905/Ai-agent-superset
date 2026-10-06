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
from . import prompt as prompts
from .text import fold
from .tools import TOOL_LABELS, TOOLS, RequestState, execute

logger = logging.getLogger(__name__)

_MAX_ROUNDS = 8  # safety cap on tool-call rounds
_NUDGE = (
    "Lệnh vừa rồi bị lỗi. Hãy sửa theo thông báo lỗi và gọi lại công cụ ngay, "
    "chưa trả lời người dùng."
)
_NO_QUERY_NUDGE = (
    "Bạn chưa gọi công cụ nào trong lượt này nên chưa thể kết luận là không có dữ "
    "liệu hay không có quyền. Hãy gọi công cụ phù hợp (query_dataset, "
    "search_datasets...) rồi mới trả lời."
)
# Wording of a "no data / no permission" answer (accent-folded).
_DENIAL = re.compile(r"khong co du lieu|pham vi duoc phep xem|quyen truy cap|khong co quyen")
# model id -> time until which it is skipped after a quota (429) or overload
# (503) error; the next configured model is used meanwhile.
_exhausted: dict[str, float] = {}
_DAILY_QUOTA_TTL = 60 * 60  # seconds
_RATE_LIMIT_TTL = 60  # seconds, per-minute quotas when the error gives no delay
_OVERLOADED_TTL = 5 * 60  # seconds


def _is_quota(ex: Exception) -> bool:
    return "429" in str(ex) or "RESOURCE_EXHAUSTED" in str(ex)


def _quota_wait(message: str) -> float:
    """How long a model stays unusable after a 429: the retryDelay Google sends
    when there is one, an hour for a daily quota, a minute otherwise (per-minute
    limits, which clear quickly)."""
    delay = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", message)
    if delay:
        return float(delay[1])
    return _DAILY_QUOTA_TTL if "PerDay" in message else _RATE_LIMIT_TTL


def _skip_unavailable(model: str, ex: Exception) -> bool:
    """Put a model aside after a quota/overload error; False for other errors."""
    if _is_quota(ex):
        wait = _quota_wait(str(ex))
        _exhausted[model] = time.time() + wait
        logger.warning("hospital-chat: quota exhausted for %s (%.0f s)", model, wait)
        return True
    if "503" in str(ex) or "UNAVAILABLE" in str(ex):
        _exhausted[model] = time.time() + _OVERLOADED_TTL
        logger.warning("hospital-chat: %s overloaded, using the next model", model)
        return True
    return False


class ModelsUnavailable(RuntimeError):
    """The model provider is unreachable, overloaded or out of quota."""


def _run_tool(name: str, args: dict, state: RequestState) -> Iterator[dict]:
    """Yield the progress events of one tool call; the result is the last item."""
    yield {"type": "tool_start", "tool": name, "label": TOOL_LABELS.get(name, name)}
    result, event = execute(name, args, state)
    yield event
    yield {"type": "_result", "result": result, "failed": not event["ok"]}


def _unfounded_denial(content: str, state: RequestState) -> bool:
    """A "no data / no permission" answer given without running any tool this
    turn: small models copy such answers from earlier turns of the conversation."""
    return not state.calls and bool(_DENIAL.search(fold(content)))


def stream(prompt: str, question: str, history: list[dict], state: RequestState) -> Iterator[dict]:
    events = (_ollama if config.provider() == "ollama" else _gemini)(
        prompt, question, history, state
    )
    answer = ""
    for event in events:
        if event["type"] == "token":
            answer += event["text"]
        elif event["type"] == "discard":
            answer = ""
        yield event
    if prompts.leaks(answer):
        logger.warning("hospital-chat: answer quoted the system prompt; replaced")
        yield {"type": "discard"}
        yield {"type": "token", "text": prompts.LEAK_REPLY}
    elif not answer.strip():
        yield {"type": "token",
               "text": "Xin lỗi, tôi chưa trả lời được câu này. Bạn thử hỏi lại hoặc diễn đạt "
               "cụ thể hơn."}


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
        if nudges < 2 and _unfounded_denial(content, state):
            nudges += 1
            yield {"type": "discard"}
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": _NO_QUERY_NUDGE})
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

    client = genai.Client(
        api_key=config.gemini_api_key(),
        # Fail fast on 429/503 and let open_stream move to the next model, rather
        # than the SDK backing off on an overloaded one; cap each call at 2 min.
        http_options=types.HttpOptions(
            retry_options=types.HttpRetryOptions(attempts=1), timeout=120_000
        ),
    )
    level = config.gemini_thinking()
    gen_config = types.GenerateContentConfig(
        system_instruction=prompt,
        tools=[types.Tool(function_declarations=TOOLS)],
        temperature=0,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        # Thinking dominates latency for tool calls; "low" keeps them right here.
        thinking_config=types.ThinkingConfig(thinking_level=level.upper()) if level else None,
    )
    contents = [
        types.Content(role=t["role"], parts=[types.Part(text=t["text"])]) for t in history
    ]
    contents.append(types.Content(role="user", parts=[types.Part(text=question)]))

    def open_stream():
        """Start a stream on the first usable model: models out of quota (429) or
        overloaded (503) are skipped for a while instead of retried each round."""
        models = config.gemini_models()
        now = time.time()
        available = [m for m in models if _exhausted.get(m, 0) <= now] or models
        overloaded = False
        for model in available:
            try:
                it = client.models.generate_content_stream(
                    model=model, contents=contents, config=gen_config
                )
                first = next(it, None)  # errors usually surface on the first chunk
                return model, first, it
            except Exception as ex:  # pylint: disable=broad-except
                if not _skip_unavailable(model, ex):
                    raise
                overloaded = overloaded or not _is_quota(ex)
        raise ModelsUnavailable(
            "Máy chủ AI (Google Gemini) đang quá tải, vui lòng thử lại sau ít phút."
            if overloaded
            else "Đã hết hạn mức sử dụng Gemini hôm nay cho tất cả các model đã cấu hình. "
            "Vui lòng thử lại sau, hoặc nâng cấp gói trả phí / thêm model vào "
            "HOSPITAL_CHAT_FALLBACK_MODELS."
        )

    nudges = 0
    for _ in range(_MAX_ROUNDS):
        model, first, rest = open_stream()
        parts, calls, streamed, text = [], [], False, ""
        try:
            for chunk in [first, *rest] if first is not None else []:
                candidate = (chunk.candidates or [None])[0]
                for part in (candidate.content.parts if candidate and candidate.content else None) or []:
                    parts.append(part)  # keeps thought signatures for the next round
                    if part.function_call:
                        calls.append(part.function_call)
                    elif part.text and not part.thought:
                        streamed = True
                        text += part.text
                        yield {"type": "token", "text": part.text}
        except Exception as ex:  # pylint: disable=broad-except
            # Overload/quota in the middle of a stream: redo this round elsewhere.
            if not _skip_unavailable(model, ex):
                raise
            if streamed:
                yield {"type": "discard"}
            continue
        if not calls and nudges < 2 and _unfounded_denial(text, state):
            nudges += 1
            yield {"type": "discard"}
            contents.append(types.Content(role="model", parts=parts))
            contents.append(types.Content(role="user", parts=[types.Part(text=_NO_QUERY_NUDGE)]))
            continue
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
