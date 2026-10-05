// Client for the extension backend (backend/src/demo/hospital_chat/api.py).
// The @api decorator mounts RestApi classes under /extensions/{publisher}/{name}/.

const ASK_URL = "/extensions/demo/hospital-chat/ask";
const CSRF_URL = "/api/v1/security/csrf_token/";

export type ChatEvent =
  | { type: "context"; dashboard: { id: number; title: string } | null; datasets: string[] }
  | { type: "tool_start"; tool: string; label: string }
  | { type: "tool_result"; tool: string; summary: string; ok: boolean; chart?: unknown }
  | { type: "token"; text: string }
  | { type: "discard" }
  | { type: "error"; message: string }
  | { type: "done" };

let csrfToken: string | null = null;

async function getCsrfToken(refresh = false): Promise<string> {
  if (csrfToken && !refresh) return csrfToken;
  const res = await fetch(CSRF_URL, { credentials: "same-origin" });
  if (!res.ok) throw new Error(`Không lấy được CSRF token (${res.status})`);
  csrfToken = (await res.json()).result as string;
  return csrfToken;
}

/** Id or slug of the dashboard in the current URL, if any. */
export function currentDashboard(): string | null {
  const m = window.location.pathname.match(/\/dashboard\/([^/?#]+)/);
  return m && m[1] !== "list" ? decodeURIComponent(m[1]) : null;
}

/** POST a question and call onEvent for every server-sent event. */
export async function streamAsk(
  body: { question: string; conversation_id: string; dashboard: string | null },
  onEvent: (event: ChatEvent) => void,
): Promise<void> {
  const post = async (token: string) =>
    fetch(ASK_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        "X-CSRFToken": token,
      },
      body: JSON.stringify(body),
    });

  let res = await post(await getCsrfToken());
  if (res.status === 400 || res.status === 403) {
    // The session's CSRF token may have rotated; retry once with a fresh one.
    const retry = await post(await getCsrfToken(true));
    if (retry.ok) res = retry;
  }
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data?.message || data?.msg || `Máy chủ trả lỗi ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const dispatch = (frame: string) => {
    const data = frame
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim())
      .join("\n");
    if (data) onEvent(JSON.parse(data) as ChatEvent);
  };
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    frames.forEach(dispatch);
    if (done) break;
  }
  if (buffer.trim()) dispatch(buffer);
}
