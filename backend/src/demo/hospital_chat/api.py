"""REST endpoint of the Hospital Chat extension.

  ChatPanel (browser)  --POST /extensions/demo/hospital-chat/ask-->  this file
      body: {"question", "conversation_id", "dashboard"}   (+ X-CSRFToken header)
      response: Server-Sent Events, one JSON object per `data:` line:
        context     dashboard being viewed and the datasets in use
        tool_start  a tool is running ("Đang truy vấn dữ liệu")
        tool_result its outcome, plus a chart when one was drawn
        token       part of the answer (rendered as it arrives)
        discard     drop the answer text received so far
        error       the request failed; message is shown to the user
        done        end of the answer

The model only sees Superset datasets (see catalog.py) through structured
tools (see tools.py); every query runs as the logged-in user, so dataset
permissions and Row Level Security apply as on dashboards. Settings are listed
in config.py.
"""

import json
import logging
import time
from collections.abc import Iterator

from flask import request, Response, stream_with_context
from flask_appbuilder.api import expose, permission_name, protect, safe
from superset_core.rest_api.api import RestApi
from superset_core.rest_api.decorators import api

from . import history, prompt, questionlog
from .catalog import can_search, ContextError, load_context
from .providers import ModelsUnavailable, stream
from .tools import RequestState

logger = logging.getLogger(__name__)

_MAX_QUESTION_CHARS = 2000


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def _answer(question: str, conversation_id: str, dashboard_ref: object) -> Iterator[str]:
    try:
        context = load_context(dashboard_ref)
    except ContextError as ex:
        yield _sse({"type": "error", "message": str(ex)})
        return
    dashboard = context["dashboard"]
    yield _sse(
        {
            "type": "context",
            "dashboard": dashboard and {"id": dashboard["id"], "title": dashboard["title"]},
            "datasets": [ds["name"] for ds in context["datasets"].values()],
        }
    )
    searchable = can_search(context)
    if not context["datasets"] and not searchable:
        yield _sse(
            {
                "type": "error",
                "message": "Bạn chưa có quyền xem dataset nào mà trợ lý được dùng. "
                "Liên hệ quản trị viên.",
            }
        )
        return

    key = history.key_for(conversation_id)
    turns = history.load(key)
    previous = next((t["text"] for t in reversed(turns) if t["role"] == "user"), "")
    state = RequestState(question=question, context=context, recent=previous)
    answer = ""
    started = time.time()
    try:
        for event in stream(prompt.build(context, searchable), question, turns, state):
            if event["type"] == "token":
                answer += event["text"]
            elif event["type"] == "discard":
                answer = ""
            yield _sse(event)
    except ModelsUnavailable as ex:
        questionlog.write(question, context, state, answer, str(ex), started)
        yield _sse({"type": "error", "message": str(ex)})
        return
    except Exception as ex:  # pylint: disable=broad-except
        logger.exception("hospital-chat: answer failed")
        questionlog.write(question, context, state, answer, repr(ex), started)
        yield _sse({"type": "error", "message": f"Lỗi máy chủ: {ex}"})
        return
    questionlog.write(question, context, state, answer.strip(), None, started)
    history.append(key, turns, question, answer.strip())
    yield _sse({"type": "done"})


@api(
    id="hospital_chat_api",
    name="Hospital Chat API",
    description="Trả lời câu hỏi về dữ liệu từ dataset Superset bằng AI.",
)
class HospitalChatAPI(RestApi):
    openapi_spec_tag = "Hospital Chat"
    class_permission_name = "hospital_chat_api"

    @expose("/ask", methods=("POST",))
    @protect()
    @safe
    @permission_name("read")
    def ask(self) -> Response:
        # FAB REST APIs are exempt from Superset's CSRF protection, but this
        # endpoint is called with the session cookie, so check the token here.
        from flask_wtf.csrf import validate_csrf
        from wtforms import ValidationError

        try:
            validate_csrf(request.headers.get("X-CSRFToken"))
        except ValidationError:
            return self.response(403, message="CSRF token không hợp lệ.")
        body = request.get_json(silent=True) or {}
        question = str(body.get("question") or "").strip()
        if not question:
            return self.response(400, message="Thiếu câu hỏi.")
        if len(question) > _MAX_QUESTION_CHARS:
            return self.response(400, message="Câu hỏi quá dài.")
        return Response(
            stream_with_context(
                _answer(question, str(body.get("conversation_id") or ""), body.get("dashboard"))
            ),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
