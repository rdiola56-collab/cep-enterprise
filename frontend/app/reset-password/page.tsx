"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, setToken } from "@/lib/api";

function ResetForm() {
  const token = useSearchParams().get("token") ?? "";
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (pw !== again) { setError("The two passwords differ."); return; }
    setError(null);
    try {
      const r = await api<{ access_token: string }>("/auth/reset", { method: "POST", body: JSON.stringify({ token, new_password: pw }) });
      setToken(r.access_token);
      window.location.href = "/";
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <form onSubmit={submit} className="panel w-80 space-y-3 p-6 text-sm">
      <div><div className="font-semibold">CEP: ENTERPRISE</div><div className="muted">Choose a new password</div></div>
      {!token && <div className="text-red-500">This page needs the link an administrator gave you.</div>}
      <label className="block">New password (at least 10 characters)
        <input className="field mt-1 w-full" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} required minLength={10} /></label>
      <label className="block">Repeat it
        <input className="field mt-1 w-full" type="password" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required /></label>
      {error && <div className="text-red-500">{error}</div>}
      <button className="btn w-full" disabled={!token}>Set password and sign in</button>
    </form>
  );
}

export default function ResetPasswordPage() {
  return <div className="flex h-full items-center justify-center"><Suspense><ResetForm /></Suspense></div>;
}
