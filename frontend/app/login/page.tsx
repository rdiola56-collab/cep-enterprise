"use client";

import { useEffect, useState } from "react";
import { api, setToken } from "@/lib/api";

type SignInOptions = { enabled: boolean; label: string | null; forgot_password?: boolean;
  saml?: { enabled: boolean; label: string | null } };

export default function LoginPage() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sso, setSso] = useState<SignInOptions | null>(null);
  useEffect(() => {
    api<SignInOptions>("/auth/sso").then(setSso).catch(() => undefined);
    const failed = new URLSearchParams(window.location.search).get("sso_error");  // returned here by the SAML endpoint
    if (failed) setError(`Single sign-on failed: ${failed}`);
  }, []);

  async function startSso() {
    setError(null);
    try {
      const r = await api<{ authorization_url: string }>("/auth/sso/start", { method: "POST" });
      window.location.href = r.authorization_url;
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function startSaml() {
    setError(null);
    try {
      const r = await api<{ redirect_url: string }>("/auth/saml/start", { method: "POST" });
      window.location.href = r.redirect_url;
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
        {(sso?.enabled || sso?.saml?.enabled) && <div className="muted text-center text-xs">or</div>}
        {sso?.enabled && <button type="button" className="panel w-full py-1.5 text-sm" onClick={startSso}>{sso.label}</button>}
        {sso?.saml?.enabled && <button type="button" className="panel w-full py-1.5 text-sm" onClick={startSaml}>{sso.saml.label}</button>}
        {sso?.forgot_password
          ? <p className="text-xs"><a className="underline" href="/forgot-password">Forgot your password?</a></p>
          : <p className="muted text-xs">Forgot your password? Ask an administrator for a reset link.</p>}
      </form>
    </div>
  );
}
