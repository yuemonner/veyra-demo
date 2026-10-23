"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { API, postJson, uploadFile } from "../../lib/api";

type ParsedRow = Record<string, string>;
type TryResult = {
  mode: "backend" | "local";
  caseId?: string;
  fileName: string;
  rowCount: number;
  assets: string[];
  eventTypes: Record<string, number>;
  changes: string[];
  confirmed: string[];
  possibleCauses: { label: string; support: string; missing: string }[];
  nextCheck: string;
  missingEvidence: string[];
  citations: string[];
  report?: any;
};

const sampleCsv = `timestamp,asset_id,event_type,software_version,configuration,status,zone
2026-09-03T14:11:08Z,EX03,safe_stop,autonomy-2.7,localization-L4,failed,loading-zone-B
2026-09-03T14:18:00Z,EX05,safe_stop,autonomy-2.7,localization-L4,failed,loading-zone-B
2026-09-03T14:20:00Z,EX11,mission_segment,autonomy-2.7,localization-L4,healthy,loading-zone-C
2026-09-03T14:27:00Z,EX11,operator_note,autonomy-2.7,localization-L4,no_known_issue,loading-zone-C`;

function splitCsvLine(line: string) {
  const values: string[] = [];
  let current = "";
  let quoted = false;
  for (const char of line) {
    if (char === '"') quoted = !quoted;
    else if (char === "," && !quoted) {
      values.push(current.trim());
      current = "";
    } else current += char;
  }
  values.push(current.trim());
  return values;
}

function parseLocalFile(name: string, text: string): ParsedRow[] {
  if (name.endsWith(".json") || name.endsWith(".jsonl") || name.endsWith(".ndjson")) {
    if (name.endsWith(".jsonl") || name.endsWith(".ndjson")) {
      return text.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
    }
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) return parsed;
    if (Array.isArray(parsed.events)) return parsed.events;
    return [parsed];
  }
  const lines = text.split(/\r?\n/).filter(Boolean);
  const headers = splitCsvLine(lines[0] || "");
  return lines.slice(1).map((line) => {
    const values = splitCsvLine(line);
    return Object.fromEntries(headers.map((header, index) => [header, values[index] || ""]));
  });
}

function value(row: ParsedRow, keys: string[], fallback = "") {
  for (const key of keys) if (row[key]) return row[key];
  return fallback;
}

function summarizeRows(fileName: string, rows: ParsedRow[], mode: TryResult["mode"], report?: any, caseId?: string): TryResult {
  const assets = Array.from(new Set(rows.map((row) => value(row, ["asset_id", "robot_id", "machine_id", "vehicle_id"], "unknown"))));
  const eventTypes: Record<string, number> = {};
  const releases = new Set<string>();
  const configs = new Set<string>();
  const zones = new Set<string>();
  const statuses = new Set<string>();
  for (const row of rows) {
    const eventType = value(row, ["event_type", "type", "name", "topic"], "record");
    eventTypes[eventType] = (eventTypes[eventType] || 0) + 1;
    if (value(row, ["software_version", "release", "version"])) releases.add(value(row, ["software_version", "release", "version"]));
    if (value(row, ["configuration", "config", "profile", "localization"])) configs.add(value(row, ["configuration", "config", "profile", "localization"]));
    if (row.zone) zones.add(row.zone);
    if (row.status) statuses.add(row.status);
  }
  const failureRows = rows.filter((row) => /fail|safe_stop|error|abort|unhealthy/i.test(`${value(row, ["event_type", "type"])} ${row.status || ""}`));
  const healthyRows = rows.filter((row) => /healthy|ok|success|no_safe_stop/i.test(`${row.status || ""} ${value(row, ["event_type", "type"])}`));
  const changes = [
    releases.size ? `Release values present: ${Array.from(releases).join(", ")}` : "",
    configs.size ? `Configuration values present: ${Array.from(configs).join(", ")}` : "",
    zones.size ? `Zones present: ${Array.from(zones).join(", ")}` : "",
  ].filter(Boolean);
  const missingEvidence = [
    failureRows.length && healthyRows.length ? "" : "A useful healthy comparator is missing.",
    rows.some((row) => /intervention|action|rollback|restart/i.test(value(row, ["event_type", "type"]))) ? "" : "No operator action or intervention record was found.",
    rows.some((row) => /outcome|recover|resolved/i.test(`${value(row, ["event_type", "type"])} ${row.status || ""}`)) ? "" : "No recovery outcome record was found.",
  ].filter(Boolean);
  return {
    mode,
    caseId,
    fileName,
    rowCount: rows.length,
    assets,
    eventTypes,
    changes,
    confirmed: [
      `${rows.length} records parsed from ${fileName}.`,
      `${assets.length} asset${assets.length === 1 ? "" : "s"} found: ${assets.slice(0, 6).join(", ")}${assets.length > 6 ? "..." : ""}.`,
      failureRows.length ? `${failureRows.length} record${failureRows.length === 1 ? "" : "s"} look failure-related.` : "No obvious failure event was detected.",
    ],
    possibleCauses: [
      { label: "Shared configuration or localization", support: configs.size ? `${Array.from(configs).join(", ")} appears in the file.` : "No configuration column found.", missing: "Compare affected assets against healthy assets with the same release." },
      { label: "Environment or zone interaction", support: zones.size ? `${Array.from(zones).join(", ")} appears in the file.` : "No zone or environment column found.", missing: "Check whether a healthy machine shares the same zone." },
      { label: "Release-wide issue", support: releases.size ? `${Array.from(releases).join(", ")} appears in the file.` : "No release column found.", missing: "A release-wide claim needs healthy machines on the same release." },
    ],
    nextCheck: healthyRows.length
      ? "Compare the failure rows against the healthy rows that share the same release."
      : "Add one healthy comparator run from the same release before deciding what changed.",
    missingEvidence,
    citations: rows.slice(0, 6).map((_, index) => `${fileName}:row:${index + 1}`),
    report,
  };
}

export default function TryVeyraPage() {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<TryResult | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const backendReady = Boolean(API);
  const eventTypeList = useMemo(() => result ? Object.entries(result.eventTypes) : [], [result]);

  async function analyze(inputFile: File) {
    setBusy(true);
    setError("");
    try {
      const text = await inputFile.text();
      if (backendReady) {
        const created = await postJson<{ id: string }>("/field-cases", {
          title: `Uploaded Field Case · ${inputFile.name}`,
          asset_id: "uploaded-asset",
          trigger_payload: { source: "try_veyra_upload", synthetic: false },
        });
        await uploadFile(`/field-cases/${created.id}/evidence-files`, inputFile, "self-serve upload");
        const report = await postJson<any>(`/field-cases/${created.id}/report`, {
          question: "What can we reconstruct, what remains uncertain, and what should we check next?",
        });
        const rows = parseLocalFile(inputFile.name.toLowerCase(), text);
        setResult(summarizeRows(inputFile.name, rows, "backend", report, created.id));
      } else {
        const rows = parseLocalFile(inputFile.name.toLowerCase(), text);
        setResult(summarizeRows(inputFile.name, rows, "local"));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not analyze this file.");
    } finally {
      setBusy(false);
    }
  }

  async function runSample() {
    const sample = new File([sampleCsv], "fc021-sample.csv", { type: "text/csv" });
    setFile(sample);
    await analyze(sample);
  }

  return (
    <main className="try-page">
      <header className="try-header">
        <Link className="try-brand" href="/veyra"><span>V</span> Veyra</Link>
        <nav>
          <Link href="/veyra">Product</Link>
          <Link href="/product-demo">Product Demo</Link>
        </nav>
      </header>

      <section className="try-hero">
        <div>
          <span className="eyebrow">Try Veyra</span>
          <h1>Upload one incident file. Get a structured Field Case.</h1>
          <p>Use a CSV, JSON, JSONL or NDJSON file with machine events, release data, status, configuration or operator notes. Veyra separates confirmed facts from possible causes and missing evidence.</p>
        </div>
        <div className="try-status">
          <b>{backendReady ? "Backend connected" : "Local browser preview"}</b>
          <p>{backendReady ? "Files are parsed through the Field Case API." : "No API URL is configured, so this preview parses your file locally in the browser."}</p>
        </div>
      </section>

      <section className="try-workspace">
        <aside className="try-upload">
          <label htmlFor="incident-file">
            <span>Upload evidence</span>
            <strong>{file?.name || "CSV / JSON / JSONL"}</strong>
            <small>Columns like timestamp, asset_id, event_type, release, configuration, status and zone work best.</small>
          </label>
          <input
            id="incident-file"
            type="file"
            accept=".csv,.json,.jsonl,.ndjson,application/json,text/csv"
            onChange={(event) => {
              const next = event.target.files?.[0] || null;
              setFile(next);
              if (next) analyze(next);
            }}
          />
          <button className="try-primary" onClick={() => file && analyze(file)} disabled={!file || busy}>{busy ? "Analyzing..." : "Analyze file"}</button>
          <button className="try-secondary" onClick={runSample} disabled={busy}>Use sample field case</button>
          {error && <p className="try-error">{error}</p>}
          <div className="try-note">
            <b>What this is good for</b>
            <p>A first look at whether one incident file contains enough evidence to reconstruct facts, compare machines and decide what to check next.</p>
          </div>
        </aside>

        <section className="try-results">
          {!result ? (
            <div className="try-empty">
              <span>Waiting for evidence</span>
              <h2>Your result will appear here.</h2>
              <p>Upload a file or use the sample field case. The output is intentionally conservative: facts, possible causes, missing evidence and a next check.</p>
            </div>
          ) : (
            <>
              <div className="try-result-head">
                <div>
                  <span>{result.mode === "backend" ? "Persisted Field Case" : "Local Preview"}</span>
                  <h2>{result.fileName}</h2>
                </div>
                <b>{result.rowCount} records</b>
                {result.caseId && <b>{result.caseId}</b>}
              </div>

              <div className="try-grid">
                <article>
                  <span>Confirmed facts</span>
                  {result.confirmed.map((item) => <p key={item}>{item}</p>)}
                </article>
                <article>
                  <span>What changed or varied</span>
                  {result.changes.length ? result.changes.map((item) => <p key={item}>{item}</p>) : <p>No release, configuration or zone fields found.</p>}
                </article>
                <article>
                  <span>Next useful check</span>
                  <h3>{result.nextCheck}</h3>
                </article>
              </div>

              <div className="try-split">
                <article>
                  <span>Possible causes to test</span>
                  {result.possibleCauses.map((cause) => (
                    <div className="try-cause" key={cause.label}>
                      <b>{cause.label}</b>
                      <p><strong>Support</strong> {cause.support}</p>
                      <p><strong>Missing</strong> {cause.missing}</p>
                    </div>
                  ))}
                </article>
                <article>
                  <span>Missing evidence</span>
                  {result.missingEvidence.length ? result.missingEvidence.map((item) => <p key={item}>{item}</p>) : <p>No obvious missing action or outcome fields detected.</p>}
                  <span>Event types</span>
                  <div className="try-tags">{eventTypeList.map(([name, count]) => <b key={name}>{name} · {count}</b>)}</div>
                  <span>Citations</span>
                  <div className="try-tags">{result.citations.map((item) => <b key={item}>{item}</b>)}</div>
                </article>
              </div>

              {result.report && (
                <details className="try-report">
                  <summary>Backend report</summary>
                  <pre>{JSON.stringify(result.report, null, 2)}</pre>
                </details>
              )}
            </>
          )}
        </section>
      </section>
    </main>
  );
}
