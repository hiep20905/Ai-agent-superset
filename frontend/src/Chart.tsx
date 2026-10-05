import React from "react";

// Charts the backend built from dataset query results (see api.py `_chart`).
// Drawn as plain SVG/HTML so the extension needs no charting library, and
// sized for the narrow chat panel: bars are horizontal so long ward names fit.

export type ChartSpec = {
  type: "bar" | "line" | "pie";
  title: string;
  x_label: string;
  labels: string[];
  series: { name: string; values: (number | null)[] }[];
  explore_url?: string;
  truncated?: boolean;
};

const COLORS = ["#20a7c9", "#f59f00", "#7b61ff", "#2fb344", "#e8590c", "#d6336c"];

const fmt = (v: number | null | undefined) =>
  v == null ? "–" : Number.isInteger(v) ? String(v) : v.toFixed(1);

function Legend({ items }: { items: { name: string; color: string }[] }) {
  if (items.length < 2) return null;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 6 }}>
      {items.map((it) => (
        <span key={it.name} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
          <span style={{ width: 10, height: 10, borderRadius: 2, background: it.color }} />
          {it.name}
        </span>
      ))}
    </div>
  );
}

function BarChart({ spec }: { spec: ChartSpec }) {
  const max = Math.max(
    1e-9,
    ...spec.series.flatMap((s) => s.values.map((v) => (v == null ? 0 : v))),
  );
  return (
    <div>
      {spec.labels.map((label, i) => (
        <div key={i} style={{ marginBottom: 6 }}>
          <div style={{ color: "#444", marginBottom: 2 }}>{label}</div>
          {spec.series.map((s, j) => {
            const v = s.values[i];
            return (
              <div key={j} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <div style={{ flex: 1, background: "#f1f3f5", borderRadius: 3, height: 12 }}>
                  <div
                    style={{
                      width: `${Math.max(0, ((v ?? 0) / max) * 100)}%`,
                      background: COLORS[j % COLORS.length],
                      height: "100%",
                      borderRadius: 3,
                    }}
                  />
                </div>
                <span style={{ minWidth: 34, textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                  {fmt(v)}
                </span>
              </div>
            );
          })}
        </div>
      ))}
      <Legend
        items={spec.series.map((s, j) => ({ name: s.name, color: COLORS[j % COLORS.length] }))}
      />
    </div>
  );
}

function LineChart({ spec }: { spec: ChartSpec }) {
  const W = 300;
  const H = 150;
  const P = { l: 34, r: 8, t: 8, b: 24 };
  const all = spec.series.flatMap((s) => s.values.filter((v): v is number => v != null));
  const max = Math.max(...all, 0);
  const min = Math.min(...all, 0);
  const span = max - min || 1;
  const n = spec.labels.length;
  const x = (i: number) => P.l + (n <= 1 ? 0 : (i * (W - P.l - P.r)) / (n - 1));
  const y = (v: number) => P.t + (1 - (v - min) / span) * (H - P.t - P.b);
  const step = Math.max(1, Math.ceil(n / 4));
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={spec.title}>
        {[min, (min + max) / 2, max].map((v, i) => (
          <g key={i}>
            <line x1={P.l} x2={W - P.r} y1={y(v)} y2={y(v)} stroke="#e9ecef" />
            <text x={P.l - 4} y={y(v) + 3} fontSize="9" textAnchor="end" fill="#868e96">
              {fmt(v)}
            </text>
          </g>
        ))}
        {spec.labels.map((label, i) =>
          i % step === 0 || i === n - 1 ? (
            <text key={i} x={x(i)} y={H - 8} fontSize="9" textAnchor="middle" fill="#868e96">
              {label.length > 10 ? label.slice(0, 10) : label}
            </text>
          ) : null,
        )}
        {spec.series.map((s, j) => (
          <g key={j}>
            <polyline
              fill="none"
              stroke={COLORS[j % COLORS.length]}
              strokeWidth="2"
              points={s.values
                .map((v, i) => (v == null ? null : `${x(i)},${y(v)}`))
                .filter(Boolean)
                .join(" ")}
            />
            {s.values.map((v, i) =>
              v == null ? null : (
                <circle key={i} cx={x(i)} cy={y(v)} r="2.5" fill={COLORS[j % COLORS.length]}>
                  <title>{`${spec.labels[i]}: ${fmt(v)}`}</title>
                </circle>
              ),
            )}
          </g>
        ))}
      </svg>
      <Legend
        items={spec.series.map((s, j) => ({ name: s.name, color: COLORS[j % COLORS.length] }))}
      />
    </div>
  );
}

function PieChart({ spec }: { spec: ChartSpec }) {
  const values = (spec.series[0]?.values ?? []).map((v) => Math.max(0, v ?? 0));
  const total = values.reduce((a, b) => a + b, 0) || 1;
  const R = 50;
  const r = 28;
  let angle = -Math.PI / 2;
  const slices = values.map((v, i) => {
    const start = angle;
    const sweep = (v / total) * Math.PI * 2;
    angle += sweep;
    return { v, i, start, end: angle, sweep };
  });
  const point = (a: number, rad: number) => [60 + rad * Math.cos(a), 60 + rad * Math.sin(a)];
  return (
    <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
      <svg viewBox="0 0 120 120" width={110} height={110} role="img" aria-label={spec.title}>
        {slices.map(({ v, i, start, end, sweep }) => {
          if (v <= 0) return null;
          const color = COLORS[i % COLORS.length];
          if (sweep >= Math.PI * 2 - 1e-6) {
            return <circle key={i} cx="60" cy="60" r={(R + r) / 2} fill="none" stroke={color} strokeWidth={R - r} />;
          }
          const large = sweep > Math.PI ? 1 : 0;
          const [x1, y1] = point(start, R);
          const [x2, y2] = point(end, R);
          const [x3, y3] = point(end, r);
          const [x4, y4] = point(start, r);
          return (
            <path
              key={i}
              d={`M${x1},${y1} A${R},${R} 0 ${large} 1 ${x2},${y2} L${x3},${y3} A${r},${r} 0 ${large} 0 ${x4},${y4} Z`}
              fill={color}
            >
              <title>{`${spec.labels[i]}: ${fmt(v)}`}</title>
            </path>
          );
        })}
      </svg>
      <div style={{ flex: 1, minWidth: 0 }}>
        {spec.labels.map((label, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ width: 10, height: 10, borderRadius: 2, flex: "none", background: COLORS[i % COLORS.length] }} />
            <span style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}>{label}</span>
            <span style={{ fontVariantNumeric: "tabular-nums" }}>
              {fmt(values[i])} ({Math.round((values[i] / total) * 100)}%)
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Chart({ spec }: { spec: ChartSpec }) {
  const Body = spec.type === "pie" ? PieChart : spec.type === "line" ? LineChart : BarChart;
  return (
    <div
      style={{
        background: "#fff",
        border: "1px solid #e5e5e5",
        borderRadius: 8,
        padding: 10,
        marginTop: 8,
        fontSize: 12,
        whiteSpace: "normal",
      }}
    >
      {spec.title && <div style={{ fontWeight: 600, marginBottom: 8 }}>{spec.title}</div>}
      <Body spec={spec} />
      <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8, color: "#868e96" }}>
        <span>{spec.truncated ? "Chỉ hiển thị 50 nhóm đầu." : ""}</span>
        {spec.explore_url && (
          <a href={spec.explore_url} target="_blank" rel="noopener noreferrer" style={{ color: "#20a7c9" }}>
            Mở trong Superset ↗
          </a>
        )}
      </div>
    </div>
  );
}
