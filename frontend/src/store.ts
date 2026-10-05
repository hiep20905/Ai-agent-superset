// Conversation state lives outside the panel: the host unmounts the panel when
// the chat is closed, and an answer may still be streaming in after that. The
// state is also mirrored to sessionStorage so a page reload keeps the conversation.

import type { ChartSpec } from "./Chart";

export type Step = { text: string; ok?: boolean };

export type Msg = {
  role: "user" | "assistant";
  content: string;
  charts?: ChartSpec[];
  // Progress of the request ("Đang truy vấn dữ liệu", "Đã lấy 5 dòng"...).
  steps?: Step[];
  // True while the answer is still streaming.
  pending?: boolean;
};

type State = {
  conversationId: string;
  msgs: Msg[];
  loading: boolean;
  // Dashboard the backend answered for, shown in the panel header.
  dashboard: string | null;
};

const STORAGE_KEY = "hospital-chat:conversation";

export const GREETING: Msg = {
  role: "assistant",
  content:
    "Xin chào! Hỏi tôi về giường bệnh, bệnh nhân, đợt điều trị hoặc thống kê " +
    'ghi chú lâm sàng, ví dụ: "Công suất giường của khoa Tim mạch là bao nhiêu?" ' +
    'hoặc "Vẽ biểu đồ công suất giường theo khoa".',
};

function newId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 12);
}

function fresh(): State {
  return { conversationId: newId(), msgs: [GREETING], loading: false, dashboard: null };
}

function load(): State {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) {
      const saved = JSON.parse(raw);
      if (saved?.conversationId && Array.isArray(saved.msgs)) {
        // A reload cuts any answer that was still streaming.
        const msgs = saved.msgs.map((m: Msg) =>
          m.pending ? { ...m, pending: false, content: m.content || "(Đã bị gián đoạn)" } : m,
        );
        return { conversationId: saved.conversationId, msgs, loading: false, dashboard: null };
      }
    }
  } catch {
    // Storage unavailable (private mode, blocked): keep the conversation in memory.
  }
  return fresh();
}

let state: State = load();
const listeners = new Set<() => void>();

function set(next: State) {
  state = next;
  try {
    sessionStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ conversationId: state.conversationId, msgs: state.msgs }),
    );
  } catch {
    // ignore, see load()
  }
  listeners.forEach((l) => l());
}

export function getState(): State {
  return state;
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function addMsg(msg: Msg, conversationId: string) {
  // Drop answers that belong to a conversation the user already reset.
  if (conversationId !== state.conversationId) return;
  set({ ...state, msgs: [...state.msgs, msg] });
}

/** Update the newest message (the answer being streamed). */
export function updateLast(fn: (m: Msg) => Msg, conversationId: string) {
  if (conversationId !== state.conversationId || !state.msgs.length) return;
  const msgs = state.msgs.slice();
  msgs[msgs.length - 1] = fn(msgs[msgs.length - 1]);
  set({ ...state, msgs });
}

export function setLoading(loading: boolean, conversationId: string) {
  if (conversationId !== state.conversationId) return;
  set({ ...state, loading });
}

export function setDashboard(dashboard: string | null) {
  set({ ...state, dashboard });
}

export function reset() {
  set({ ...fresh(), dashboard: state.dashboard });
}
