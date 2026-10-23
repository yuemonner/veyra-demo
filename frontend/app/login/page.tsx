"use client";

import { FormEvent, useEffect, useState } from "react";
import s from "../field-case/field-case.module.css";

const API = process.env.NEXT_PUBLIC_API_URL ?? "/local-api";

// Only same-site paths are accepted as the place to return to after signing in.
function safeNext(): string {
  const next = new URLSearchParams(window.location.search).get("next") || "/field-case";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/field-case";
}

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${API}/auth/me`, { credentials: "include" }).then((r) => r.json()).then((me) => {
      if (me?.user) window.location.replace(safeNext());
    }).catch(() => undefined);
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const r = await fetch(`${API}/auth/login`, { method: "POST", credentials: "include", headers: { "content-type": "application/json", "x-veyra-request": "1" },
        body: JSON.stringify({ email, password }) });
      const body = await r.json().catch(() => null);
      if (!r.ok) throw new Error(body?.detail || "Sign-in failed");
      window.location.replace(safeNext());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <div className={s.page}>
      <header className={s.topbar}><span className={s.brand}><span className={s.mark}>V</span>Veyra</span></header>
      <div className={s.center}>
        <form className={s.dialog} style={{ maxWidth: 400, textAlign: "left" }} onSubmit={submit}>
          <h1 style={{ fontSize: 22, margin: "0 0 4px", letterSpacing: "-0.02em" }}>Sign in</h1>
          <p className={s.lede} style={{ margin: "0 0 14px" }}>Your workspace is private: only people in it see its cases.</p>
          <label className={s.field}><span>Email</span><input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus /></label>
          <label className={s.field} style={{ marginTop: 12 }}><span>Password</span><input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} /></label>
          {error && <p className={s.warning} role="alert">{error}</p>}
          <button className={s.button} style={{ marginTop: 16, width: "100%" }} disabled={busy || !email || !password}>{busy ? "Signing in…" : "Sign in"}</button>
          <p className={s.note}>No account? Ask your workspace owner. There is no public sign-up.</p>
        </form>
      </div>
    </div>
  );
}
