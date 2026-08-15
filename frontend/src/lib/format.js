const INR = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 0,
});

// Every formatter returns an em dash for null/undefined rather than 0, so a
// missing value never reads as a real one.
export function inr(value) {
  return value == null ? "—" : INR.format(value);
}

export function inrSigned(value) {
  if (value == null) return "—";
  return `${value < 0 ? "-" : "+"}${INR.format(Math.abs(value))}`;
}

export function pct(value, digits = 1) {
  return value == null ? "—" : `${value.toFixed(digits)}%`;
}

export function pnlClass(value) {
  if (value == null) return "";
  return value >= 0 ? "pnl-positive" : "pnl-negative";
}
