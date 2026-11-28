"use client";

import { useState } from "react";
import { api } from "@/lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api<{ detail: string }>("/auth/forgot", { method: "POST", body: JSON.stringify({ email }) });
      setDone(r.detail);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex h-full items-center justify-center">
      <form onSubmit={submit} className="panel w-80 space-y-3 p-6 text-sm">
        <div><div className="font-semibold">CEP: ENTERPRISE</div><div className="muted">Reset your password by email</div></div>
        {done ? <div>{done}</div> : <>
          <label className="block">Email
            <input className="field mt-1 w-full" type="email" autoComplete="username" required value={email}
              onChange={(e) => setEmail(e.target.value)} /></label>
          {error && <div className="text-red-500">{error}</div>}
          <button className="btn w-full" disabled={busy}>{busy ? "Sending…" : "Send me a reset link"}</button>
        </>}
        <a className="block text-xs underline" href="/login">Back to sign in</a>
      </form>
    </div>
  );
}
