import React from "react";

// Minimal Markdown renderer for assistant answers. Builds React elements
// (never innerHTML), so model output cannot inject markup.
// Supports: **bold**, *italic*, `code`, "-"/"*" bullets, "#" headings, "---",
// "> " quotes and pipe tables.

const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*)/g;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_SEPARATOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function renderInline(text: string): React.ReactNode[] {
  return text.split(INLINE).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={i}>{renderInline(part.slice(2, -2))}</strong>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code
          key={i}
          style={{ background: "#e6e6e6", borderRadius: 4, padding: "0 4px" }}
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) {
      return <em key={i}>{part.slice(1, -1)}</em>;
    }
    return part;
  });
}

function cells(row: string): string[] {
  return row
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());
}

const cellStyle: React.CSSProperties = {
  border: "1px solid #ddd",
  padding: "4px 8px",
  textAlign: "left",
  verticalAlign: "top",
  whiteSpace: "nowrap",
};

function Table({ header, rows }: { header: string[]; rows: string[][] }) {
  return (
    // Wide tables scroll sideways inside the bubble instead of overflowing it.
    <div style={{ overflowX: "auto", margin: "4px 0", maxWidth: "100%" }}>
      <table
        style={{ borderCollapse: "collapse", fontSize: 13, background: "#fff" }}
      >
        <thead>
          <tr>
            {header.map((h, i) => (
              <th
                key={i}
                style={{ ...cellStyle, background: "#eaf6fa", fontWeight: 600 }}
              >
                {renderInline(h)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {header.map((_, j) => (
                <td key={j} style={cellStyle}>
                  {renderInline(r[j] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderLine(line: string, key: number): React.ReactNode {
  if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
    return (
      <hr
        key={key}
        style={{ border: "none", borderTop: "1px solid #ddd", margin: "6px 0" }}
      />
    );
  }
  const heading = line.match(/^\s*#{1,6}\s+(.*)$/);
  if (heading) {
    return (
      <div key={key} style={{ fontWeight: 600 }}>
        {renderInline(heading[1])}
      </div>
    );
  }
  const quote = line.match(/^\s*>\s?(.*)$/);
  if (quote) {
    return (
      <div
        key={key}
        style={{ borderLeft: "3px solid #20a7c9", paddingLeft: 8, color: "#555" }}
      >
        {renderInline(quote[1])}
      </div>
    );
  }
  const bullet = line.match(/^(\s*)[-*+]\s+(.*)$/);
  if (bullet) {
    return (
      <div
        key={key}
        style={{ display: "flex", paddingLeft: 4 + bullet[1].length * 8 }}
      >
        <span style={{ marginRight: 6 }}>•</span>
        <span>{renderInline(bullet[2])}</span>
      </div>
    );
  }
  // Empty lines keep their height so paragraphs stay separated.
  return <div key={key}>{line ? renderInline(line) : " "}</div>;
}

export default function Markdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const out: React.ReactNode[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (TABLE_ROW.test(lines[i]) && TABLE_SEPARATOR.test(lines[i + 1] ?? "")) {
      const header = cells(lines[i]);
      const rows: string[][] = [];
      let j = i + 2;
      for (; j < lines.length && TABLE_ROW.test(lines[j]); j++) {
        rows.push(cells(lines[j]));
      }
      out.push(<Table key={i} header={header} rows={rows} />);
      i = j - 1;
      continue;
    }
    out.push(renderLine(lines[i], i));
  }
  return <>{out}</>;
}
