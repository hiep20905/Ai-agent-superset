import React, { useEffect, useRef, useState } from "react";
import { chat } from "@apache-superset/core";
import Chart from "./Chart";
import Markdown from "./Markdown";

import {
  addMsg,
  getState,
  reset,
  setLoading,
  subscribe,
} from "./store";

// Backend REST endpoint registered by the extension (see backend/src/.../api.py).
// The @api decorator mounts RestApi classes under /extensions/{publisher}/{name}/.
const ASK_URL = "/extensions/demo/hospital-chat/ask";

async function ask(question: string) {
  const { conversationId } = getState();
  addMsg({ role: "user", content: question }, conversationId);
  setLoading(true);
  try {
    const params = new URLSearchParams({
      question,
      conversation_id: conversationId,
    });
    const res = await fetch(`${ASK_URL}?${params}`, {
      method: "GET",
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
    const data = await res.json();
    const answer =
      data?.result?.answer ??
      data?.message ??
      "Lỗi: không nhận được phản hồi từ máy chủ.";
    const charts = Array.isArray(data?.result?.charts) ? data.result.charts : [];
    addMsg({ role: "assistant", content: answer, charts }, conversationId);
  } catch (e) {
    addMsg({ role: "assistant", content: "Lỗi kết nối: " + String(e) }, conversationId);
  } finally {
    if (getState().conversationId === conversationId) setLoading(false);
  }
}

// Local models on CPU can take minutes; show that the request is alive.
function Elapsed() {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = setInterval(
      () => setSeconds(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, []);
  return <>Đang suy nghĩ… {seconds > 0 ? `(${seconds} giây)` : ""}</>;
}

export default function ChatPanel() {
  const [{ msgs, loading }, setView] = useState(getState);
  const [input, setInput] = useState("");
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => subscribe(() => setView(getState())), []);

  // Keep the newest message in view.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs, loading]);

  function send() {
    const q = input.trim();
    if (!q || loading) return;
    setInput("");
    ask(q);
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        // The host mounts the floating panel without sizing or a background,
        // so the panel provides its own compact, opaque card.
        width: "min(380px, calc(100vw - 48px))",
        height: "min(560px, calc(100vh - 140px))",
        background: "#fff",
        borderRadius: 12,
        boxShadow: "0 8px 28px rgba(0,0,0,0.2)",
        border: "1px solid #e5e5e5",
        overflow: "hidden",
        fontSize: 14,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 12px",
          borderBottom: "1px solid #eee",
          fontWeight: 600,
        }}
      >
        <span>Trợ lý bệnh viện</span>
        <span style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <button
            type="button"
            onClick={reset}
            title="Cuộc trò chuyện mới"
            style={{
              border: "1px solid #ddd",
              background: "#fff",
              borderRadius: 6,
              cursor: "pointer",
              fontSize: 12,
              color: "#555",
              padding: "3px 8px",
            }}
          >
            + Cuộc trò chuyện mới
          </button>
          <button
            type="button"
            onClick={() => chat.close()}
            title="Đóng"
            aria-label="Đóng"
            style={{
              border: "none",
              background: "transparent",
              cursor: "pointer",
              fontSize: 18,
              lineHeight: 1,
              color: "#888",
              padding: 4,
            }}
          >
            ✕
          </button>
        </span>
      </div>

      <div
        ref={listRef}
        style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: 12 }}
      >
        {msgs.map((m, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              justifyContent: m.role === "user" ? "flex-end" : "flex-start",
              marginBottom: 8,
            }}
          >
            <div
              style={{
                maxWidth: "85%",
                // Charts size to the bubble, so give it a fixed width.
                width: m.charts?.length ? "85%" : undefined,
                overflowWrap: "anywhere",
                padding: "8px 10px",
                borderRadius: 10,
                whiteSpace: "pre-wrap",
                background: m.role === "user" ? "#20a7c9" : "#f2f2f2",
                color: m.role === "user" ? "#fff" : "#222",
              }}
            >
              {m.role === "assistant" ? <Markdown text={m.content} /> : m.content}
              {m.charts?.map((c, j) => <Chart key={j} spec={c} />)}
            </div>
          </div>
        ))}
        {loading && (
          <div style={{ color: "#888", fontStyle: "italic" }}>
            <Elapsed />
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 8, padding: 12, borderTop: "1px solid #eee" }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") send();
          }}
          placeholder="Nhập câu hỏi…"
          style={{
            flex: 1,
            minWidth: 0,
            padding: "8px 10px",
            borderRadius: 8,
            border: "1px solid #ccc",
          }}
        />
        <button
          type="button"
          onClick={send}
          disabled={loading}
          style={{
            padding: "8px 14px",
            borderRadius: 8,
            border: "none",
            background: "#20a7c9",
            color: "#fff",
            cursor: loading ? "default" : "pointer",
          }}
        >
          Gửi
        </button>
      </div>
    </div>
  );
}
