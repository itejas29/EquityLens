// Mirrors the color tokens in index.css — Recharts renders to SVG and can't
// read CSS custom properties for stroke/fill, so the hex values are duplicated here.
export const chartColors = {
  grid: "rgba(255,255,255,0.06)",
  axis: "#575d78",
  accent: "#9a93ff",
  gold: "#f0b429",
  emerald: "#12b981",
  rose: "#f4425f",
  sky: "#38bdf8",
  violet: "#c084fc",
  muted: "#575d78",
};

export const pieColors = ["#6c63ff", "#f0b429", "#38bdf8", "#12b981", "#f4425f", "#c084fc", "#fb923c", "#2dd4bf"];

export const tooltipStyle = {
  contentStyle: {
    background: "#10162c",
    border: "1px solid rgba(255,255,255,0.16)",
    borderRadius: 9,
    fontFamily: "'JetBrains Mono', monospace",
    fontSize: 12.5,
  },
  labelStyle: { color: "#8b91ac", marginBottom: 4 },
  itemStyle: { padding: 0 },
};
