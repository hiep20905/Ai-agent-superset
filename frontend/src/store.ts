// Conversation state lives outside the panel: the host unmounts the panel when
// the chat is closed, and an answer may still arrive after that. The state is
// also mirrored to sessionStorage so a page reload keeps the conversation.

import type { ChartSpec } from "./Chart";

export type Msg = {
  role: "user" | "assistant";
  content: string;
  charts?: ChartSpec[];
};

type State = { conversationId: string; msgs: Msg[]; loading: boolean };

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
  return { conversationId: newId(), msgs: [GREETING], loading: false };
}

function load(): State {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (raw) {
      const saved = JSON.parse(raw);
      if (saved?.conversationId && Array.isArray(saved.msgs)) {
        return { conversationId: saved.conversationId, msgs: saved.msgs, loading: false };
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

export function setLoading(loading: boolean) {
  set({ ...state, loading });
}

export function reset() {
  set(fresh());
}
