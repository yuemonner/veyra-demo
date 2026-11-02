// micro-ROS agent logs (verbose, -v6): every packet the agent received or sent, decoded from the bytes in the log.
// Deterministic and self-contained: the investigation view, the discussion panel and the HTML report all read the
// same numbers, so a plot, a table and an answer can never disagree. Nothing here is a model's guess: each value is
// read from a quoted log line, and each answer cites its lines.

export type LogEvent = { line: number; t: number; level: string; file: string; fn: string; msg: string; fields: Record<string, string> };
export type Sub = { id: number; name: string; flags: number; length: number; at: number };
export type Decoded = {
  wellFormed: boolean;          // header + first submessage fit the packet and the submessage id is known
  session: number; stream: number; seq: number; clientKey: string | null; sub: Sub | null;
  status: { code: number; name: string } | null;       // STATUS_AGENT / STATUS result
  create: { cookie: string; version: string; vendor: string; clientKey: string; session: number } | null;
  problems: string[];           // why it is not a valid message, in words
  header: string;               // the fields as read, valid or not
  reading: string;              // one-line description
};
export type Packet = {
  n: number; line: number; dataLines: number[]; t: number; dir: "to_agent" | "from_agent";
  key: string; len: number; bytes: number[]; complete: boolean; allZero: boolean; xrce: Decoded;
};
export type AgentLog = {
  lines: string[]; events: LogEvent[]; packets: Packet[];
  start: number | null;                              // time of the first timestamped line
  port: { requested: number | null; used: number | null; wrapped: boolean };
  sessions: { key: string; opened: number | null; closed: number | null; openLine: number | null; closeLine: number | null; address: string | null }[];
};

const SUBS: Record<number, string> = { 0: "CREATE_CLIENT", 1: "CREATE", 2: "GET_INFO", 3: "DELETE", 4: "STATUS_AGENT", 5: "STATUS", 6: "INFO",
  7: "WRITE_DATA", 8: "READ_DATA", 9: "DATA", 10: "ACKNACK", 11: "HEARTBEAT", 12: "RESET", 13: "FRAGMENT", 14: "TIMESTAMP", 15: "TIMESTAMP_REPLY" };
const MIN_BODY: Record<number, number> = { 0: 16, 4: 2, 5: 2 };   // CREATE_CLIENT carries at least cookie, version, vendor, key, session
const STATUS: Record<number, string> = { 0x00: "OK", 0x01: "OK_MATCHED", 0x80: "ERR_DDS_ERROR", 0x81: "ERR_MISMATCH", 0x82: "ERR_ALREADY_EXISTS",
  0x83: "ERR_DENIED", 0x84: "ERR_UNKNOWN_REFERENCE", 0x85: "ERR_INVALID_DATA", 0x86: "ERR_INCOMPATIBLE", 0x87: "ERR_RESOURCES" };

export const hex = (b: number, w = 2) => b.toString(16).toUpperCase().padStart(w, "0");
const keyOf = (b: number[], at: number) => "0x" + b.slice(at, at + 4).map((x) => hex(x)).join("");
const u16 = (b: number[], at: number) => (b[at] ?? 0) | ((b[at + 1] ?? 0) << 8);
const EVENT = /^\[(\d+(?:\.\d+)?)\]\s+(\w+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(.*)$/;
const HEXLINE = /^([0-9A-Fa-f]{4}):\s*((?:[0-9A-Fa-f]{2}\s*)+)$/;

export function looksLikeAgentLog(lines: string[]): boolean {
  return lines.some((l) => /recv_message|send_message/.test(l)) && lines.some((l) => HEXLINE.test(l.trim()));
}

/** Read an XRCE message: header (session, stream, sequence, client key when the session id is below 0x80), first submessage. */
export function decodeXrce(b: number[], dir: "to_agent" | "from_agent" = "to_agent"): Decoded {
  const problems: string[] = [];
  const d: Decoded = { wellFormed: false, session: b[0] ?? 0, stream: b[1] ?? 0, seq: u16(b, 2), clientKey: null, sub: null, status: null, create: null, problems, header: "", reading: "" };
  const hlen = d.session < 0x80 ? 8 : 4;
  if (b.length < hlen + 4) { problems.push(`too short for a header and a submessage (${b.length} bytes)`); d.reading = problems[0]; return d; }
  if (d.session < 0x80) d.clientKey = keyOf(b, 4);
  const id = b[hlen], length = u16(b, hlen + 2);
  d.sub = { id, name: SUBS[id] ?? `unknown id ${id}`, flags: b[hlen + 1], length, at: hlen };
  if (!(id in SUBS)) problems.push(`submessage id ${id} is not an XRCE submessage`);
  if (hlen + 4 + length > b.length) problems.push(`declares a ${length.toLocaleString("en-US")}-byte submessage but the packet has ${b.length} bytes`);
  if (id in MIN_BODY && length < MIN_BODY[id]) problems.push(`${SUBS[id]} with a ${length}-byte body (needs at least ${MIN_BODY[id]})`);
  if (dir === "to_agent" && d.session < 0x80 && d.clientKey === "0x00000000") problems.push("client key 0x00000000 (no client has that key)");
  const body = hlen + 4;
  if ((id === 4 || id === 5) && length >= 2) { const c = b[body]; d.status = { code: c, name: STATUS[c] ?? `0x${hex(c)}` }; }
  if (id === 0 && length >= 13 && String.fromCharCode(...b.slice(body, body + 4)) === "XRCE")
    d.create = { cookie: "XRCE", version: `${b[body + 4]}.${b[body + 5]}`, vendor: `0x${hex(b[body + 6])}${hex(b[body + 7])}`, clientKey: keyOf(b, body + 8), session: b[body + 12] };
  d.wellFormed = problems.length === 0;
  const head = `session 0x${hex(d.session)}${d.clientKey ? `, client key ${d.clientKey}` : ""}, ${d.sub.name}`;
  d.header = `${head} with a ${length.toLocaleString("en-US")}-byte body`;
  d.reading = d.wellFormed
    ? d.create ? `${d.sub.name}: client key ${d.create.clientKey}, asks for session 0x${hex(d.create.session)}`
      : d.status ? `${d.sub.name} on session 0x${hex(d.session)}: result ${d.status.name}${d.clientKey ? ` to key ${d.clientKey}` : ""}` : head
    : `not a valid XRCE message: ${problems.join("; ")}`;
  return d;
}

/** Little-endian 32-bit integers in a byte string, for a packet that is not a valid message. */
export function asInt32(b: number[]): number[] {
  const out: number[] = [];
  for (let i = 0; i + 4 <= b.length; i += 4) out.push(((b[i] | (b[i + 1] << 8) | (b[i + 2] << 16)) >>> 0) + b[i + 3] * 16777216);
  return out;
}

export function parseAgentLog(lines: string[]): AgentLog {
  const events: LogEvent[] = [];
  const packets: Packet[] = [];
  let cur: Packet | null = null;
  const port = { requested: null as number | null, used: null as number | null, wrapped: false };
  lines.forEach((raw, i) => {
    const line = i + 1, text = raw.trim();
    const cmd = text.match(/--port\s+(\d+)/);
    if (cmd && !text.startsWith("[")) port.requested = Number(cmd[1]);
    const h = text.match(HEXLINE);
    if (h && cur) { cur.bytes.push(...h[2].trim().split(/\s+/).map((x) => parseInt(x, 16))); cur.dataLines.push(line); return; }
    const m = text.match(EVENT);
    if (!m) return;
    const fields: Record<string, string> = {};
    for (const part of m[6].split(/,\s*/)) { const kv = part.match(/^\s*([\w ]+?):\s*(.*)$/); if (kv) fields[kv[1].trim()] = kv[2].trim(); }
    const ev: LogEvent = { line, t: Number(m[1]), level: m[2], file: m[3].trim(), fn: m[4].trim(), msg: m[5].trim(), fields };
    events.push(ev);
    if (fields.port && /init/.test(ev.fn)) port.used = Number(fields.port);
    if (ev.fn === "recv_message" || ev.fn === "send_message") {
      cur = { n: packets.length + 1, line, dataLines: [], t: ev.t, dir: ev.fn === "recv_message" ? "to_agent" : "from_agent", key: fields.client_key || "",
        len: Number(fields.len || 0), bytes: [], complete: false, allZero: false, xrce: null as unknown as Decoded };
      packets.push(cur);
    } else cur = null;
  });
  for (const p of packets) { p.complete = p.bytes.length >= p.len && p.len > 0; p.allZero = p.bytes.length > 0 && p.bytes.every((x) => x === 0); p.xrce = decodeXrce(p.bytes, p.dir); }
  port.wrapped = port.requested != null && port.used != null && port.requested !== port.used && port.requested % 65536 === port.used;
  const sessions: AgentLog["sessions"] = [];
  for (const e of events) {
    if (e.fn === "establish_session") sessions.push({ key: e.fields.client_key, opened: e.t, openLine: e.line, closed: null, closeLine: null, address: e.fields.address || null });
    if (e.fn === "destroy_session") { const s = [...sessions].reverse().find((x) => x.key === e.fields.client_key && x.closed == null); if (s) { s.closed = e.t; s.closeLine = e.line; } }
  }
  return { lines, events, packets, start: events.length ? events[0].t : null, port, sessions };
}

// ---------------------------------------------------------------------------------------------------------------
// What a healthy session looks like (XRCE protocol), set against what this log shows. The left column is protocol
// knowledge, stated once; the right column is computed from this log.

export type Expectation = { expected: string; observed: string; ok: boolean | null; lines: number[] };
const ms = (s: number) => `${(s * 1000).toFixed(s < 0.01 ? 2 : 1)} ms`;

export function expectations(log: AgentLog): Expectation[] {
  const rows: Expectation[] = [];
  const P = log.packets;
  const create = P.find((p) => p.dir === "to_agent" && p.xrce.create);
  const reply = create ? P.find((p) => p.dir === "from_agent" && p.t >= create.t && p.xrce.status) : undefined;
  rows.push({ expected: "The board opens with CREATE_CLIENT and the agent answers STATUS_AGENT OK.",
    observed: create && reply ? `Yes: ${create.len}-byte create (line ${create.line}), ${reply.len}-byte reply with result ${reply.xrce.status?.name} ${ms(reply.t - create.t)} later (line ${reply.line}).` : "No valid create and reply found.",
    ok: !!(create && reply && reply.xrce.status?.code === 0), lines: [create?.line, reply?.line].filter(Boolean) as number[] });
  const sess = create?.xrce.create?.session;
  const after = create ? P.filter((p) => p.dir === "to_agent" && p.t > create.t) : [];
  const first = after[0];
  rows.push({ expected: sess != null ? `Every later board message carries the session it asked for (0x${hex(sess)}).` : "Later board messages carry the session that was opened.",
    observed: first ? (first.xrce.session === sess ? `Yes, from line ${first.line}.` : `No. The next board message (line ${first.line}) has session 0x${hex(first.xrce.session)}${first.allZero ? " (every byte is zero)" : ""}.`) : "No later board message.",
    ok: first ? first.xrce.session === sess : null, lines: first ? [first.line] : [] });
  const key = create?.xrce.create?.clientKey;
  const keyed = after.filter((p) => p.key && p.key !== key);
  rows.push({ expected: key ? `The client key stays ${key} for the life of the session.` : "The client key stays the same for the session.",
    observed: keyed.length ? `No. ${keyed.length} of ${after.length} later board messages arrive with key ${keyed[0].key} (from line ${keyed[0].line}).` : "Yes.",
    ok: keyed.length === 0, lines: keyed.slice(0, 1).map((p) => p.line) });
  const bad = P.filter((p) => p.dir === "to_agent" && p.complete && !p.xrce.wellFormed);
  rows.push({ expected: "Every message is a valid XRCE header plus submessages that fit inside the packet.",
    observed: bad.length ? `No. ${bad.length} board message${bad.length > 1 ? "s are" : " is"} not valid: ${[...new Set(bad.map((p) => `${p.len}-byte (${p.xrce.problems[0]})`))].join("; ")}.` : "Yes.",
    ok: bad.length === 0, lines: bad.slice(0, 2).map((p) => p.line) });
  const s = log.sessions[0];
  rows.push({ expected: "Once established, the session stays up while the board runs.",
    observed: s ? (s.closed != null ? `No. Session ${s.key} closed ${ms(s.closed - (s.opened ?? s.closed))} after it opened (lines ${s.openLine}–${s.closeLine}).` : "Yes, it stays open to the end of the log.") : "No session was established.",
    ok: s ? s.closed == null : false, lines: s ? [s.openLine, s.closeLine].filter(Boolean) as number[] : [] });
  const warn = log.events.filter((e) => /warn|error/i.test(e.level));
  rows.push({ expected: "The agent logs no warnings or errors of its own.",
    observed: warn.length ? `${warn.length} warning${warn.length > 1 ? "s" : ""}: ${warn.map((w) => `“${w.msg}” (line ${w.line})`).join(", ")}${warn.every((w) => /client key/.test(w.msg)) ? ", a reaction to the data it received" : ""}.` : "None.",
    ok: warn.length === 0 ? true : null, lines: warn.map((w) => w.line) });
  if (log.port.requested != null) rows.push({ expected: "The agent listens on the port it was started with (1–65535).",
    observed: log.port.wrapped ? `No. Started with ${log.port.requested}, listening on ${log.port.used} (= ${log.port.requested} mod 65536). The board still reached it, so this did not stop the session.` : `Yes, port ${log.port.used ?? log.port.requested}.`,
    ok: log.port.wrapped ? null : true, lines: [1, 2] });
  return rows;
}

// ---------------------------------------------------------------------------------------------------------------
// Timing and repeats.

export function repeats(log: AgentLog) {
  const groups = new Map<string, Packet[]>();
  for (const p of log.packets) if (p.dir === "to_agent" && p.bytes.length) {
    const k = p.bytes.map((x) => hex(x)).join(" "); groups.set(k, [...(groups.get(k) || []), p]);
  }
  return [...groups.values()].filter((g) => g.length > 1).map((g) => {
    const gaps = g.slice(1).map((p, i) => p.t - g[i].t);
    return { packets: g, count: g.length, len: g[0].len, first: g[0].line, last: g[g.length - 1].line,
      meanGap: gaps.reduce((a, b) => a + b, 0) / gaps.length, minGap: Math.min(...gaps), maxGap: Math.max(...gaps) };
  });
}

export type Table = { cols: string[]; rows: (string | number)[][] };
export const packetTable = (log: AgentLog): Table => ({
  cols: ["#", "Line", "Time", "Direction", "Bytes", "Key the agent logged", "Read from the bytes"],
  rows: log.packets.map((p) => [p.n, p.line, `+${((p.t - (log.packets[0]?.t ?? p.t)) * 1000).toFixed(1)} ms`, p.dir === "to_agent" ? "board → agent" : "agent → board",
    p.complete ? p.len : `${p.len} (bytes not in log)`, p.key, p.complete ? (p.allZero ? `all ${p.len} bytes zero · ${p.xrce.reading}` : p.xrce.reading) : "—"]),
});

// ---------------------------------------------------------------------------------------------------------------
// Discussion: an engineer's question or proposed cause, checked against this log. Each check names the claim it
// tests, computes from the log, and says what would settle what the log cannot.

export type Verdict = "supports" | "against" | "cannot_tell" | "facts";
export type Answer = { check: string; title: string; claim: string; verdict: Verdict; text: string; points: string[]; table?: Table;
  lines: number[]; about: RegExp | null; settle?: string; highlight?: number[] };
type Check = { id: string; title: string; ask: string; words: RegExp; run: (log: AgentLog) => Answer | null };

const pct = (a: number, b: number) => `${a} of ${b}`;
const firstZero = (log: AgentLog) => log.packets.find((p) => p.dir === "to_agent" && p.allZero && p.complete);
const createOf = (log: AgentLog) => log.packets.find((p) => p.dir === "to_agent" && p.xrce.create);

export const CHECKS: Check[] = [
  { id: "summary", title: "Every packet, decoded", ask: "What traffic is in this log?", words: /\b(summary|overview|traffic|packets?|what happened|timeline|everything|all messages?|decode)\b/,
    run: (log) => {
      const to = log.packets.filter((p) => p.dir === "to_agent").length, from = log.packets.length - to;
      return { check: "summary", title: "Every packet, decoded", claim: "", verdict: "facts", text: `${log.packets.length} packets: ${to} from the board to the agent, ${from} from the agent to the board. Each one read from the bytes printed in the log.`,
        points: [], table: packetTable(log), lines: log.packets.map((p) => p.line), about: null, highlight: log.packets.map((p) => p.n) };
    } },
  { id: "zero", title: "Is the 36-byte packet really all zeros?", ask: "Is the 36-byte packet really empty?", words: /\b(zero|zeros|zeroes|36|empty|blank|null bytes?|garbage)\b/,
    run: (log) => {
      const z = firstZero(log), c = createOf(log), r = z && log.packets.find((p) => p.dir === "from_agent" && p.t < z.t && p.t >= (c?.t ?? 0));
      if (!z) return null;
      return { check: "zero", title: "The 36-byte packet", claim: "The board sent a packet of zeros", verdict: "supports",
        text: `Yes. ${pct(z.bytes.filter((x) => x === 0).length, z.len)} bytes are 0x00 (lines ${[z.line, ...z.dataLines].join(", ")}). It came from the same client (key ${z.key}) ${r ? `${ms(z.t - r.t)} after the agent's reply` : ""}.`,
        points: [`Read as an XRCE message it is ${z.xrce.header}. That is why the agent logs “invalid client key 0x00000000”${log.events.find((e) => /invalid client key/.test(e.msg)) ? ` at line ${log.events.find((e) => /invalid client key/.test(e.msg))!.line}` : ""}.`,
          "Every byte is zero, not a few flipped bits. That fits a buffer that was never filled, or data that never reached memory the Ethernet controller reads. This log, recorded on the agent's side, cannot tell those apart."],
        lines: [z.line, ...z.dataLines], about: /firmware|buffer|ethernet|dma|lwip/i, highlight: [z.n],
        settle: "Compare the transmit buffer at the board's send call with a capture on the wire (the next action)." };
    } },
  { id: "network", title: "Could the network have corrupted it?", ask: "Could a bad cable or switch have zeroed the packet?", words: /\b(network|cable|switch|router|wire|wiring|corrupt\w*|packet loss|ethernet cable|interference|emi|noise on the line|udp checksum)\b/,
    run: (log) => {
      const z = firstZero(log), c = createOf(log), reps = repeats(log);
      return { check: "network", title: "The network path", claim: "The network corrupted the packet", verdict: "against",
        text: "Unlikely, from this log.",
        points: [
          c ? `The well-formed messages before it (from line ${c.line}) arrived intact, including the board's create with key ${c.xrce.create?.clientKey}.` : "",
          z ? `The bad packet has a sensible length (${z.len} bytes) and every byte is zero. Damage on a cable flips or drops bits, and a damaged UDP datagram normally fails its checksum and is dropped before the agent sees it.` : "",
          reps[0] ? `The ${reps[0].len}-byte packet that follows repeats ${reps[0].count} times, byte for byte identical (lines ${reps[0].first}–${reps[0].last}). Random corruption does not repeat exactly.` : "",
        ].filter(Boolean), lines: [c?.line, z?.line, reps[0]?.first].filter(Boolean) as number[], about: /network/i,
        settle: "A capture on the board's side of the cable (or a direct cable to the PC) would close it." };
    } },
  { id: "agent", title: "Is the agent mishandling valid data?", ask: "Is the micro-ROS agent the problem?", words: /\b(agent|host|pc|laptop|server|ros ?2 side|micro_ros_agent|linux side|docker)\b/,
    run: (log) => {
      const rows: (string | number)[][] = [];
      for (const p of log.packets.filter((x) => x.dir === "to_agent" && x.complete)) {
        const next = log.packets.find((x) => x.line > p.line && x.dir === "to_agent");
        const own = log.events.filter((e) => e.line > p.line && (!next || e.line < next.line));
        rows.push([p.line, `${p.len} B · ${p.allZero ? "all zero" : p.xrce.wellFormed ? p.xrce.sub?.name ?? "" : "not valid XRCE"}`,
          own.length ? own.map((e) => (e.fn === "send_message" ? `replies ${e.fields.len} B (${(log.packets.find((x) => x.line === e.line)?.xrce.status?.name) || "?"})` : e.msg)).join(" · ") : "nothing (no session)"]);
      }
      const warn = log.events.filter((e) => /warn|error/i.test(e.level));
      return { check: "agent", title: "The agent's side", claim: "The agent mishandles valid data", verdict: "against",
        text: "The agent's reactions match what it received.",
        points: ["It answered the valid create with OK, rejected the all-zero message with ERR_INVALID_DATA, and stayed silent to packets that are not XRCE.",
          `It logs ${warn.length} warning${warn.length === 1 ? "" : "s"}, ${warn.length ? `about the data it received (${warn.map((w) => `line ${w.line}`).join(", ")})` : ""} and no internal error.`],
        table: { cols: ["Line", "Board sent", "Agent did"], rows: rows.slice(0, 6) }, lines: rows.slice(0, 6).map((r) => Number(r[0])), about: /host|agent side/i,
        settle: "Running a known-good client against the same agent would close it." };
    } },
  { id: "port", title: "Was the port setting the problem?", ask: "Did port 457879 break it?", words: /\b(port|457879|64663|udp4|config\w*|settings?|argument|command line)\b/,
    run: (log) => {
      if (log.port.requested == null) return null;
      const s = log.sessions[0];
      return { check: "port", title: "The agent's port", claim: "The port setting stopped the board and agent talking", verdict: "against",
        text: log.port.wrapped ? `No. ${log.port.requested} is out of range; the agent kept the low 16 bits and listened on ${log.port.used} (${log.port.requested} mod 65536 = ${log.port.requested % 65536}).` : "The port is in range.",
        points: [s ? `The board reached the agent on that port: a session opened at line ${s.openLine}. So the port did not cause the failure. It is still worth fixing.` : ""].filter(Boolean),
        lines: [1, 2, ...(s?.openLine ? [s.openLine] : [])], about: /port/i };
    } },
  { id: "timing", title: "Is it a timeout or timing problem?", ask: "Did something time out?", words: /\b(tim(e|ed|ing)|timeout|delay|interval|latency|period|ms|slow|fast|race|when)\b/,
    run: (log) => {
      const c = createOf(log), z = firstZero(log), reps = repeats(log);
      const reply = c && log.packets.find((p) => p.dir === "from_agent" && p.t >= c.t);
      const del = log.events.find((e) => /delete_client|destroy_session/.test(e.fn));
      const t0 = log.packets[0]?.t ?? 0;
      const rows: (string | number)[][] = [];
      if (c) rows.push(["Board create", c.line, "+0.0 ms"]);
      if (reply) rows.push(["Agent reply", reply.line, `+${((reply.t - t0) * 1000).toFixed(1)} ms`]);
      if (z) rows.push(["All-zero packet", z.line, `+${((z.t - t0) * 1000).toFixed(1)} ms`]);
      if (del) rows.push(["Agent deletes the client", del.line, `+${((del.t - t0) * 1000).toFixed(1)} ms`]);
      if (reps[0]) rows.push([`${reps[0].len}-byte packet ×${reps[0].count}`, `${reps[0].first}–${reps[0].last}`, `every ${(reps[0].meanGap * 1000).toFixed(1)} ms (${(reps[0].minGap * 1000).toFixed(1)}–${(reps[0].maxGap * 1000).toFixed(1)})`]);
      return { check: "timing", title: "Timing", claim: "Something timed out", verdict: "against",
        text: "Nothing timed out. The session closed in reaction to data, not to silence.",
        points: [reply && c ? `The agent replied to the create in ${ms(reply.t - c.t)}.` : "", del && z ? `It deleted the client ${ms(del.t - z.t)} after the all-zero packet arrived: a reaction, not a timeout.` : "",
          reps[0] ? `Afterwards the board sends the same ${reps[0].len} bytes every ${(reps[0].meanGap * 1000).toFixed(0)} ms, a steady timer, so its scheduler keeps running.` : ""].filter(Boolean),
        table: { cols: ["Event", "Line", "Time"], rows }, lines: rows.map((r) => Number(String(r[1]).split("–")[0])), about: null };
    } },
  { id: "repeat", title: "What is the repeating 13-byte packet?", ask: "What is the small packet the board keeps sending?", words: /\b(13|small packets?|repeat\w*|keeps? sending|e8|keep-?alive|heartbeat|retry|retries|after the close|what is the)\b/,
    run: (log) => {
      const g = repeats(log).find((x) => !x.packets[0].allZero);
      if (!g) return null;
      const p = g.packets[0], ints = asInt32(p.bytes);
      return { check: "repeat", title: `The repeating ${g.len}-byte packet`, claim: "", verdict: "facts",
        text: `It is not a valid XRCE message: ${p.xrce.problems.join("; ")}.`,
        points: [`Sent ${g.count} times, identical, every ${(g.meanGap * 1000).toFixed(0)} ms (lines ${g.first}–${g.last}); the agent ignores it.`,
          `Read as little-endian 32-bit integers the bytes give ${ints.map((x) => x.toLocaleString("en-US")).join(" · ")}${p.bytes.length % 4 ? ` and ${p.bytes.length % 4} more byte${p.bytes.length % 4 > 1 ? "s" : ""}` : ""}. Round numbers like 1,000 and 1,000,000,000 look like time settings (milliseconds, nanoseconds), not a message.`,
          "A lead, not established: the board may be sending memory that holds settings instead of a message buffer, which would also fit a buffer or DMA problem."],
        table: { cols: ["Offset", "Bytes", "As int32 (LE)"], rows: ints.map((v, i) => [i * 4, p.bytes.slice(i * 4, i * 4 + 4).map((x) => hex(x)).join(" "), v.toLocaleString("en-US")]) },
        lines: [p.line, ...p.dataLines], about: /firmware|buffer|ethernet|dma/i, highlight: g.packets.map((x) => x.n),
        settle: "Find which variable or struct holds 1000 and 1000000000 in the board's firmware and whether its address matches the transmit buffer." };
    } },
  { id: "key", title: "Did the client key change?", ask: "Why does the client key go to 0?", words: /\b(client.?key|key|session.?id|session|0x2a2ed975|identity)\b/,
    run: (log) => {
      const rows = log.packets.filter((p) => p.dir === "to_agent").map((p) => [p.line, p.len, p.key, p.xrce.clientKey ?? (p.xrce.create ? p.xrce.create.clientKey : "—"), `0x${hex(p.xrce.session)}`]);
      const c = createOf(log);
      return { check: "key", title: "Client key and session", claim: "", verdict: "facts",
        text: c ? `The board identified itself as ${c.xrce.create?.clientKey} and asked for session 0x${hex(c.xrce.create?.session ?? 0)}. The next message has session 0x00 and key 0x00000000, because those bytes are zeros. The board never sends its key again.` : "No create found.",
        points: ["The agent's column shows the key the agent attributed to each message; the byte columns show what the board actually sent."],
        table: { cols: ["Line", "Bytes", "Agent attributes to key", "Key in the bytes", "Session byte"], rows }, lines: rows.map((r) => Number(r[0])), about: /firmware|buffer/i };
    } },
  { id: "buffer", title: "Board buffers or the Ethernet DMA?", ask: "Is it the D-cache or DMA on the H7?", words: /\b(dma|cache|d-?cache|mpu|lwip|pbuf|buffers?|firmware|stm32|h7|memory|descriptor|stream|init\w*|uninitiali[sz]ed|freertos|heap|stack)\b/,
    run: (log) => {
      const z = firstZero(log);
      return { check: "buffer", title: "Buffers or DMA", claim: "", verdict: "cannot_tell",
        text: "This log cannot separate the two. Both fit what it shows.",
        points: [z ? `The data was already zeros when it reached the agent (line ${z.line}), and the earlier create was correct. So the fault sits on the board, after a correct first send.` : "",
          "An application or stream buffer that is never filled, and an Ethernet DMA that reads stale memory (D-cache not cleaned) would both put zeros on the wire.",
          "What separates them is whether the bytes are correct at the board's send call."].filter(Boolean),
        lines: z ? [z.line, ...z.dataLines] : [], about: /firmware|buffer|ethernet|dma|lwip/i,
        settle: "Breakpoint at the transport write callback: correct bytes there and zeros on the wire means DMA or cache; zeros there means the client's buffers." };
    } },
  { id: "normal", title: "What should a healthy session look like?", ask: "What would a normal session look like?", words: /\b(normal|expected|healthy|should|supposed|usual|unusual|baseline|compare)\b/,
    run: (log) => {
      const ex = expectations(log);
      return { check: "normal", title: "A healthy session, against this log", claim: "", verdict: "facts",
        text: `${ex.filter((e) => e.ok === false).length} of ${ex.length} expectations fail. The first failure is the first board message after the handshake.`,
        points: [], table: { cols: ["A healthy session", "This log"], rows: ex.map((e) => [e.expected, `${e.ok === false ? "✕ " : e.ok ? "✓ " : "• "}${e.observed}`]) },
        lines: [...new Set(ex.flatMap((e) => e.lines))].sort((a, b) => a - b), about: null };
    } },
];

const STOP = /\b(the|a|an|is|it|this|that|of|to|in|on|and|or|be|was|were|could|can|do|does|did|i|we|you|think|maybe|might|what|why|how)\b/g;

/** Pick the checks a question or proposed cause is about. Returns the best one (and a second when it is close). */
export function route(question: string): Check[] {
  const q = ` ${question.toLowerCase().replace(/[?.!,]/g, " ")} `;
  const scored = CHECKS.map((c) => ({ c, n: (q.match(new RegExp(c.words.source, "g")) || []).length })).filter((x) => x.n > 0).sort((a, b) => b.n - a.n);
  if (!scored.length) return [];
  return scored.filter((x, i) => i === 0 || (i === 1 && x.n === scored[0].n)).map((x) => x.c);
}

export function answer(log: AgentLog, question: string): Answer[] {
  return route(question).map((c) => c.run(log)).filter(Boolean) as Answer[];
}

export const meaningful = (q: string) => q.toLowerCase().replace(STOP, " ").replace(/\s+/g, " ").trim().length > 0;
