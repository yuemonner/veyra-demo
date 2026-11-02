// Small charts as SVG strings: the page and the downloadable HTML report draw the same picture from the same numbers.
// No library, no external fonts; text is escaped. Colours are fixed so a saved report looks the same anywhere.

export const C = { ink: "#17183a", ink2: "#4a4c6a", ink3: "#8a8ca3", grid: "#e4e5ef", thread: "#4c47d6", threadSoft: "#e9e8fb", fault: "#c2304a", faultSoft: "#fbe7eb", ok: "#10806a", amber: "#b06a00" };
export const esc = (s: string | number) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c] as string));
const r1 = (x: number) => Math.round(x * 10) / 10;

export type TimelinePacket = { n: number; t: number; len: number; dir: "to_agent" | "from_agent"; bad: boolean; zero: boolean; line: number; complete?: boolean };
export type Span = { from: number; to: number; label: string };

/** Every packet on one time axis: board → agent above the line, agent → board below; bar height = bytes. */
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
    return `<g><title>${esc(`#${p.n} · line ${p.line} · ${up ? "board → agent" : "agent → board"} · ${p.len} bytes · +${r1((p.t - t0) * 1000)} ms${p.zero ? " · all zero" : p.bad ? " · not valid XRCE" : p.complete === false ? " · bytes not in the log" : ""}`)}</title>
      <rect x="${r1(x(p.t) - bw / 2)}" y="${r1(y)}" width="${bw}" height="${r1(hh)}" fill="${fill}" rx="1.5"${unknown ? ` stroke="${C.ink3}" stroke-dasharray="2 2"` : ""}/>${ring}
      <text x="${r1(x(p.t))}" y="${r1(up ? y - (hl.has(p.n) ? 7 : 4) : y + hh + (hl.has(p.n) ? 14 : 11))}" font-size="9.5" fill="${C.ink2}" text-anchor="middle">${p.len}</text></g>`;
  }).join("");
  const legend = [[C.thread, "valid, board → agent"], [C.ink2, "agent → board"], [C.fault, "all-zero bytes"], [C.amber, "not valid XRCE"]]
    .map(([c, l], i) => `<rect x="${L + i * 150}" y="${H - 12}" width="9" height="9" fill="${c}" rx="1.5"/><text x="${L + i * 150 + 13}" y="${H - 4}" font-size="10.5" fill="${C.ink2}">${l}</text>`).join("");
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(opt.title || "Packets over time")}" font-family="system-ui, -apple-system, Segoe UI, sans-serif">
  ${ticks.join("")}${sess}
  <line x1="${L}" x2="${W - R}" y1="${mid}" y2="${mid}" stroke="${C.ink3}"/>
  <text x="${L - 4}" y="${mid - 30}" font-size="10" fill="${C.ink2}" text-anchor="end">board</text><text x="${L - 4}" y="${mid - 18}" font-size="10" fill="${C.ink2}" text-anchor="end">→ agent</text>
  <text x="${L - 4}" y="${mid + 20}" font-size="10" fill="${C.ink2}" text-anchor="end">agent</text><text x="${L - 4}" y="${mid + 32}" font-size="10" fill="${C.ink2}" text-anchor="end">→ board</text>
  <text x="${W - R}" y="${T - 6}" font-size="10" fill="${C.ink3}" text-anchor="end">bar height = bytes · time from the first packet</text>
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
  <text x="${L}" y="${H - 6}" font-size="10" fill="${C.ink3}">shaded = inside the range this machine shows in healthy data</text></svg>`;
}
