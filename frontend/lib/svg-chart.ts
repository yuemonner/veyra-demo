// Small charts as SVG strings: the page and the downloadable HTML report draw the same picture from the same numbers.
// No library, no external fonts; text is escaped. Colours are fixed so a saved report looks the same anywhere.

export const C = { ink: "#17183a", ink2: "#4a4c6a", ink3: "#8a8ca3", grid: "#e4e5ef", thread: "#4c47d6", threadSoft: "#e9e8fb", fault: "#c2304a", faultSoft: "#fbe7eb", ok: "#10806a", amber: "#b06a00" };
export const esc = (s: string | number) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
const r1 = (x: number) => Math.round(x * 10) / 10;

export type TimelinePacket = { n: number; t: number; len: number; dir: "to_agent" | "from_agent"; bad: boolean; zero: boolean; line: number; complete?: boolean };
export type Span = { from: number; to: number; label: string };

/** Every packet in the agent's log on one time axis: received above the line, sent below; bar height = bytes. */
export function packetTimeline(packets: TimelinePacket[], opt: { session?: Span | null; highlight?: number[]; width?: number; title?: string } = {}): string {
  const W = opt.width ?? 760, H = 236, L = 62, R = 14, T = 32, mid = 126, B = 36;
  if (!packets.length) return "";
  const t0 = packets[0].t, tEnd = Math.max(...packets.map((p) => p.t)) - t0;
  const span = Math.max(0.05, tEnd * 1.04);
  const x = (t: number) => L + 12 + ((t - t0) / span) * (W - L - R - 12);
  const maxLen = Math.max(...packets.map((p) => p.len), 1);
  const h = (len: number) => (len / maxLen) * (mid - T - 12);
  const hl = new Set(opt.highlight || []);
  const step = [0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10].find((s) => span / s <= 10) ?? 10;
  const ticks: string[] = [];
  for (let s = 0; s <= span + 1e-9; s += step) ticks.push(`<line x1="${r1(x(t0 + s))}" x2="${r1(x(t0 + s))}" y1="${T}" y2="${H - B}" stroke="${C.grid}"/><text x="${r1(x(t0 + s))}" y="${H - B + 14}" font-size="10" fill="${C.ink3}" text-anchor="middle">${s < 1 ? `${Math.round(s * 1000)} ms` : `${r1(s)} s`}</text>`);
  const sess = opt.session ? `<rect x="${r1(x(opt.session.from))}" y="${T}" width="${r1(Math.max(2, x(opt.session.to) - x(opt.session.from)))}" height="${H - B - T}" fill="${C.threadSoft}"/>
    <text x="${r1(x(opt.session.from))}" y="${T - 6}" font-size="10" fill="${C.thread}">${esc(opt.session.label)}</text>` : "";
  const bars = packets.map((p) => {
    const up = p.dir === "to_agent", hh = Math.max(2, h(p.len)), bw = 7;
    const unknown = p.complete === false;
    const fill = unknown ? "none" : p.zero ? C.fault : p.bad ? C.amber : up ? C.thread : C.ink2;
    const y = up ? mid - hh : mid;
    const ring = hl.has(p.n) ? `<rect x="${r1(x(p.t) - bw / 2 - 3)}" y="${r1(y - 3)}" width="${bw + 6}" height="${r1(hh + 6)}" fill="none" stroke="${C.ink}" stroke-width="1.5" rx="2"/>` : "";
    return `<g><title>${esc(`#${p.n} · line ${p.line} · ${up ? "received by the agent" : "sent by the agent"} · ${p.len} bytes · +${r1((p.t - t0) * 1000)} ms${p.zero ? " · all zero" : p.bad ? " · not valid XRCE" : p.complete === false ? " · bytes not in the log" : ""}`)}</title>
      <rect x="${r1(x(p.t) - bw / 2)}" y="${r1(y)}" width="${bw}" height="${r1(hh)}" fill="${fill}" rx="1.5"${unknown ? ` stroke="${C.ink3}" stroke-dasharray="2 2"` : ""}/>${ring}
      <text x="${r1(x(p.t))}" y="${r1(up ? y - (hl.has(p.n) ? 7 : 4) : y + hh + (hl.has(p.n) ? 14 : 11))}" font-size="9.5" fill="${C.ink2}" text-anchor="middle">${p.len}</text></g>`;
  }).join("");
  const legend = [[C.thread, "received, valid XRCE"], [C.ink2, "sent by the agent"], [C.fault, "received, all bytes zero"], [C.amber, "received, not valid XRCE"]]
    .map(([c, l], i) => `<rect x="${L + i * 150}" y="${H - 12}" width="9" height="9" fill="${c}" rx="1.5"/><text x="${L + i * 150 + 13}" y="${H - 4}" font-size="10.5" fill="${C.ink2}">${l}</text>`).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opt.title || "Packets over time")}" font-family="system-ui, -apple-system, Segoe UI, sans-serif">
  ${ticks.join("")}${sess}
  <line x1="${L}" x2="${W - R}" y1="${mid}" y2="${mid}" stroke="${C.ink3}"/>
  <text x="${L - 4}" y="${mid - 30}" font-size="10" fill="${C.ink2}" text-anchor="end">agent</text><text x="${L - 4}" y="${mid - 18}" font-size="10" fill="${C.ink2}" text-anchor="end">received</text>
  <text x="${L - 4}" y="${mid + 20}" font-size="10" fill="${C.ink2}" text-anchor="end">agent</text><text x="${L - 4}" y="${mid + 32}" font-size="10" fill="${C.ink2}" text-anchor="end">sent</text>
  <text x="${W - R}" y="${T - 6}" font-size="10" fill="${C.ink3}" text-anchor="end">as logged by the agent on the host · bar height = bytes</text>
  ${bars}${legend}</svg>`;
}

export type RatioRow = { label: string; value: number; flagged: boolean; note?: string };

/** Each axis's error against its own healthy limit: 1.0 is the limit. */
export function ratioBars(rows: RatioRow[], opt: { width?: number; title?: string; limitLabel?: string } = {}): string {
  if (!rows.length) return "";
  const W = opt.width ?? 760, rowH = 20, T = 22, L = 120, R = 150, H = T + rows.length * rowH + 26;
  const max = Math.max(2, ...rows.map((r) => r.value)) * 1.05;
  const x = (v: number) => L + (v / max) * (W - L - R);
  const bars = rows.map((r, i) => {
    const y = T + i * rowH;
    return `<g><title>${esc(`${r.label}: ${r.value.toFixed(2)}× its healthy limit${r.note ? ` · ${r.note}` : ""}`)}</title>
      <text x="${L - 8}" y="${y + 13}" font-size="11" fill="${r.flagged ? C.fault : C.ink2}" text-anchor="end" font-weight="${r.flagged ? 650 : 400}">${esc(r.label)}</text>
      <rect x="${L}" y="${y + 4}" width="${r1(Math.max(1, x(r.value) - L))}" height="${rowH - 9}" rx="2" fill="${r.flagged ? C.fault : C.thread}" opacity="${r.flagged ? 1 : 0.7}"/>
      <text x="${W - R + 10}" y="${y + 13}" font-size="10.5" fill="${r.flagged ? C.fault : C.ink2}" font-weight="${r.flagged ? 650 : 400}">${r.value.toFixed(2)}×${r.flagged ? " · outside normal" : ""}</text></g>`;
  }).join("");
  const lim = x(1);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opt.title || "Error against healthy limit")}" font-family="system-ui, -apple-system, Segoe UI, sans-serif">
  <rect x="${L}" y="${T}" width="${r1(lim - L)}" height="${rows.length * rowH}" fill="${C.threadSoft}" opacity="0.6"/>
  <line x1="${r1(lim)}" x2="${r1(lim)}" y1="${T - 6}" y2="${T + rows.length * rowH + 2}" stroke="${C.ink}" stroke-dasharray="3 3"/>
  <text x="${r1(lim)}" y="${T - 9}" font-size="10" fill="${C.ink}" text-anchor="middle">${esc(opt.limitLabel || "healthy limit")}</text>
  ${bars}
  <text x="${L}" y="${H - 6}" font-size="10" fill="${C.ink3}">shaded = at or below this machine's healthy level</text></svg>`;
}

export type Line = { label: string; color: string; dash?: string; pts: { t: number; v: number }[]; width?: number };
export type Panel = { title: string; unit: string; lines: Line[]; hlines?: { y: number; label: string; color: string }[]; band?: { from: number; to: number; color: string; label: string } };

/** Several signals on one shared time axis, one panel per quantity, with the events marked across all panels. */
export function stackedChart(panels: Panel[], opt: { x0: number; x1: number; markers?: { t: number; label: string; color: string }[]; width?: number; xLabel?: string }): string {
  const W = opt.width ?? 760, L = 56, R = 150, T = 26, PH = 112, GAP = 26, B = 30;
  const H = T + panels.length * (PH + GAP) - GAP + B;
  const x = (t: number) => L + ((t - opt.x0) / (opt.x1 - opt.x0)) * (W - L - R);
  const step = [0.1, 0.2, 0.25, 0.5, 1, 2, 5, 10].find((s) => (opt.x1 - opt.x0) / s <= 8) ?? 10;
  let out = "";
  panels.forEach((p, i) => {
    const top = T + i * (PH + GAP), bot = top + PH;
    const vals = p.lines.flatMap((l) => l.pts.filter((q) => q.t >= opt.x0 && q.t <= opt.x1).map((q) => q.v)).concat((p.hlines || []).map((h) => h.y));
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = (hi - lo || 1) * 0.12; lo -= pad; hi += pad;
    const y = (v: number) => bot - ((v - lo) / (hi - lo)) * PH;
    out += `<rect x="${L}" y="${top}" width="${W - L - R}" height="${PH}" fill="#fff" stroke="${C.grid}"/>`;
    for (let k = 0; k <= 3; k++) { const v = lo + ((hi - lo) * k) / 3; out += `<line x1="${L}" x2="${W - R}" y1="${r1(y(v))}" y2="${r1(y(v))}" stroke="${C.grid}"/><text x="${L - 5}" y="${r1(y(v) + 3)}" font-size="9.5" fill="${C.ink3}" text-anchor="end">${v.toFixed(Math.abs(hi - lo) < 10 ? 1 : 0)}</text>`; }
    out += `<text x="${L}" y="${top - 5}" font-size="11" font-weight="650" fill="${C.ink}">${esc(p.title)} <tspan font-weight="400" fill="${C.ink3}">${esc(p.unit)}</tspan></text>`;
    if (p.band) out += `<rect x="${r1(x(p.band.from))}" y="${top}" width="${r1(Math.max(1, x(p.band.to) - x(p.band.from)))}" height="${PH}" fill="${p.band.color}" opacity="0.18"><title>${esc(p.band.label)}</title></rect>`;
    for (const h of p.hlines || []) out += `<line x1="${L}" x2="${W - R}" y1="${r1(y(h.y))}" y2="${r1(y(h.y))}" stroke="${h.color}" stroke-dasharray="5 3" stroke-width="1.3"/>`;
    p.lines.forEach((l, j) => {
      const pts = l.pts.filter((q) => q.t >= opt.x0 && q.t <= opt.x1);
      if (!pts.length) return;
      out += `<polyline fill="none" stroke="${l.color}" stroke-width="${l.width ?? 1.7}" ${l.dash ? `stroke-dasharray="${l.dash}"` : ""} points="${pts.map((q) => `${r1(x(q.t))},${r1(y(q.v))}`).join(" ")}"/>`;
      out += `<line x1="${W - R + 10}" x2="${W - R + 26}" y1="${top + 12 + j * 15}" y2="${top + 12 + j * 15}" stroke="${l.color}" stroke-width="2" ${l.dash ? `stroke-dasharray="${l.dash}"` : ""}/><text x="${W - R + 30}" y="${top + 15 + j * 15}" font-size="10" fill="${C.ink2}">${esc(l.label)}</text>`;
    });
    (p.hlines || []).forEach((h, j) => { const k = p.lines.length + j; out += `<line x1="${W - R + 10}" x2="${W - R + 26}" y1="${top + 12 + k * 15}" y2="${top + 12 + k * 15}" stroke="${h.color}" stroke-dasharray="5 3"/><text x="${W - R + 30}" y="${top + 15 + k * 15}" font-size="10" fill="${C.ink2}">${esc(h.label)}</text>`; });
  });
  const allBot = T + panels.length * (PH + GAP) - GAP;
  for (let s = Math.ceil(opt.x0 / step) * step; s <= opt.x1 + 1e-9; s += step) out += `<text x="${r1(x(s))}" y="${allBot + 14}" font-size="9.5" fill="${C.ink3}" text-anchor="middle">${r1(s)} s</text>`;
  // Events: a numbered tick on the chart, and one row per event under the axis, so close events never overlap.
  const ms = [...(opt.markers || [])].sort((a, b) => a.t - b.t);
  ms.forEach((m, i) => {
    const mx = r1(x(m.t));
    out += `<line x1="${mx}" x2="${mx}" y1="${T - 2}" y2="${allBot}" stroke="${m.color}" stroke-width="1.2" stroke-dasharray="2 2"><title>${esc(m.label)}</title></line>`;
    out += `<circle cx="${mx}" cy="${T - 9}" r="6.5" fill="${m.color}"/><text x="${mx}" y="${T - 5.8}" font-size="9" font-weight="700" fill="#fff" text-anchor="middle">${i + 1}</text>`;
    const ry = allBot + 32 + i * 15;
    out += `<circle cx="${L + 6}" cy="${ry - 3.5}" r="6" fill="${m.color}"/><text x="${L + 6}" y="${ry - 0.5}" font-size="8.5" font-weight="700" fill="#fff" text-anchor="middle">${i + 1}</text>`;
    out += `<text x="${L + 18}" y="${ry}" font-size="10.5" fill="${C.ink2}"><tspan font-weight="650" fill="${C.ink}">${m.t.toFixed(2)} s</tspan>  ${esc(m.label)}</text>`;
  });
  if (opt.xLabel) out += `<text x="${W - R + 10}" y="${allBot + 14}" font-size="9.5" fill="${C.ink3}">${esc(opt.xLabel)}</text>`;
  const extra = ms.length ? 26 + ms.length * 15 : 10;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H + extra}" role="img" font-family="system-ui, -apple-system, Segoe UI, sans-serif">${out}</svg>`;
}
