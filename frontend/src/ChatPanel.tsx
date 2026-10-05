import React, { useEffect, useRef, useState } from "react";
import { chat } from "@apache-superset/core";
import { currentDashboard, streamAsk } from "./api";
import Chart, { type ChartSpec } from "./Chart";
import Markdown from "./Markdown";
import {
  addMsg,
  getState,
  reset,
  setDashboard,
  setLoading,
  subscribe,
  updateLast,
} from "./store";

async function ask(question: string) {
  const { conversationId } = getState();
  addMsg({ role: "user", content: question }, conversationId);
  addMsg({ role: "assistant", content: "", steps: [], pending: true }, conversationId);
  setLoading(true, conversationId);
  const update = (fn: Parameters<typeof updateLast>[0]) => updateLast(fn, conversationId);
  try {
    await streamAsk(
      { question, conversation_id: conversationId, dashboard: currentDashboard() },
      (event) => {
        switch (event.type) {
          case "context":
            setDashboard(event.dashboard?.title ?? null);
            break;
          case "tool_start":
            update((m) => ({ ...m, steps: [...(m.steps ?? []), { text: `${event.label}…` }] }));
            break;
          case "tool_result":
            update((m) => ({
              ...m,
              steps: [...(m.steps ?? []), { text: event.summary, ok: event.ok }],
              charts: event.chart ? [...(m.charts ?? []), event.chart as ChartSpec] : m.charts,
            }));
            break;
          case "token":
            update((m) => ({ ...m, content: m.content + event.text }));
            break;
          case "discard":
            update((m) => ({ ...m, content: "" }));
            break;
          case "error":
            update((m) => ({
              ...m,
              content: (m.content ? m.content + "\n\n" : "") + event.message,
            }));
            break;
          default:
            break;
        }
      },
    );
  } catch (e) {
    update((m) => ({ ...m, content: "Lỗi kết nối: " + String((e as Error).message || e) }));
  } finally {
    update((m) => ({ ...m, pending: false, content: m.content || "(Không có câu trả lời)" }));
    setLoading(false, conversationId);
  }
}

function Steps({ steps, pending }: { steps: { text: string; ok?: boolean }[]; pending?: boolean }) {
  if (!steps.length) return null;
  const list = (
    <div style={{ marginTop: 4 }}>
      {steps.map((s, i) => (
        <div key={i} style={{ color: s.ok === false ? "#c92a2a" : "#868e96" }}>
          {s.ok === undefined ? "⋯" : s.ok ? "✓" : "✗"} {s.text}
        </div>
      ))}
    </div>
  );
  // While streaming, show the progress; afterwards fold it away.
  return (
    <div style={{ fontSize: 12, whiteSpace: "normal", marginBottom: 6 }}>
      {pending ? (
        list
      ) : (
        <details>
          <summary style={{ cursor: "pointer", color: "#868e96" }}>
            Chi tiết xử lý ({steps.filter((s) => s.ok !== undefined).length} bước)
          </summary>
          {list}
        </details>
      )}
    </div>
  );
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
  const [{ msgs, loading, dashboard }, setView] = useState(getState);
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
        <span style={{ minWidth: 0 }}>
          <div>Trợ lý bệnh viện</div>
          {dashboard && (
            <div
              title={dashboard}
              style={{
                fontSize: 11,
                fontWeight: 400,
                color: "#868e96",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
            >
              Dashboard: {dashboard}
            </div>
          )}
        </span>
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
              {m.role === "assistant" && <Steps steps={m.steps ?? []} pending={m.pending} />}
              {m.role === "assistant" && m.pending && !m.content && (
                <span style={{ color: "#888", fontStyle: "italic" }}>
                  <Elapsed />
                </span>
              )}
              {m.role === "assistant" ? m.content && <Markdown text={m.content} /> : m.content}
              {m.charts?.map((c, j) => <Chart key={j} spec={c} />)}
            </div>
          </div>
        ))}
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
