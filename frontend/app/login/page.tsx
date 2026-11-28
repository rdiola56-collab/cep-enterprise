"use client";

import { useEffect, useState } from "react";
import { api, setToken } from "@/lib/api";

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sso, setSso] = useState<{ enabled: boolean; label: string | null } | null>(null);
  useEffect(() => { api<{ enabled: boolean; label: string | null }>("/auth/sso").then(setSso).catch(() => undefined); }, []);

  async function startSso() {
    setError(null);
    try {
      const r = await api<{ authorization_url: string }>("/auth/sso/start", { method: "POST" });
      window.location.href = r.authorization_url;
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ access_token: string }>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      setToken(r.access_token);
      window.location.href = "/";
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full items-center justify-center">
      <form onSubmit={submit} className="panel w-80 space-y-3 p-6">
        <div>
          <div className="font-semibold">CEP: ENTERPRISE</div>
          <div className="muted text-sm">Sign in to continue</div>
        </div>
        <label className="block text-sm">
          Email
          <input className="field mt-1 w-full" type="email" autoComplete="username" value={email}
            onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="block text-sm">
          Password
          <input className="field mt-1 w-full" type="password" autoComplete="current-password" value={password}
            onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div className="text-sm text-red-500">{error}</div>}
        <button className="btn w-full" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        {sso?.enabled && <>
          <div className="muted text-center text-xs">or</div>
          <button type="button" className="panel w-full py-1.5 text-sm" onClick={startSso}>{sso.label}</button>
        </>}
        <p className="muted text-xs">Forgot your password? Ask an administrator for a reset link.</p>
      </form>
    </div>
  );
}
