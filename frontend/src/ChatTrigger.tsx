import React, { useEffect, useState } from "react";
import { chat } from "@apache-superset/core";

// The collapsed entry point the host renders (floating bubble). Like
// Messenger, clicking it toggles the panel open/closed.
export default function ChatTrigger() {
  const [open, setOpen] = useState(() => chat.isOpen());

  useEffect(() => {
    const subs = [
      chat.onDidOpen(() => setOpen(true)),
      chat.onDidClose(() => setOpen(false)),
    ];
    return () => subs.forEach((s) => s.dispose());
  }, []);

  const label = open ? "Đóng trợ lý bệnh viện" : "Hỏi trợ lý bệnh viện";

  return (
    <button
      type="button"
      onClick={() => (open ? chat.close() : chat.open())}
      title={label}
      aria-label={label}
      aria-expanded={open}
      style={{
        width: 52,
        height: 52,
        borderRadius: "50%",
        border: "none",
        cursor: "pointer",
        background: "#20a7c9",
        color: "#fff",
        fontSize: open ? 20 : 24,
        lineHeight: 1,
        boxShadow: "0 4px 12px rgba(0,0,0,0.25)",
      }}
    >
      {open ? "✕" : "💬"}
    </button>
  );
}
