"use client";

import { useEffect, useState } from "react";
import { api, setToken, type Me } from "@/lib/api";

export default function AccountPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [f, setF] = useState({ current_password: "", new_password: "", again: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => { api<Me>("/auth/me").then(setMe).catch(() => undefined); }, []);

  async function change(e: React.FormEvent) {
    e.preventDefault();
    if (f.new_password !== f.again) { setMsg({ ok: false, text: "The two new passwords differ." }); return; }
    try {
      const r = await api<{ access_token: string }>("/auth/password", { method: "POST",
        body: JSON.stringify({ current_password: f.current_password, new_password: f.new_password }) });
      setToken(r.access_token);
      setF({ current_password: "", new_password: "", again: "" });
      setMsg({ ok: true, text: "Password changed. Your other sessions have been signed out." });
    } catch (err) {
      setMsg({ ok: false, text: (err as Error).message });
    }
  }

  async function signOutEverywhere() {
    await api("/auth/logout-everywhere", { method: "POST" }).catch(() => undefined);
    setToken(null);
    window.location.href = "/login";
  }

  const field = (k: keyof typeof f, label: string, auto: string) => (
    <label className="block">{label}
      <input className="field mt-1 w-full" type="password" autoComplete={auto} value={f[k]} required
        onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>
  );

  return (
    <div className="max-w-md space-y-4 text-sm">
      <h1 className="text-xl font-semibold">Account</h1>
      {me && <div className="panel p-4"><div className="font-medium">{me.name}</div><div className="muted">{me.email} · {me.global_role.replace("_", " ")}</div></div>}
      <form onSubmit={change} className="panel space-y-3 p-4">
        <h2 className="font-medium">Change password</h2>
        {field("current_password", "Current password", "current-password")}
        {field("new_password", "New password (at least 10 characters)", "new-password")}
        {field("again", "Repeat new password", "new-password")}
        {msg && <div className={msg.ok ? "text-green-600" : "text-red-500"}>{msg.text}</div>}
        <button className="btn">Change password</button>
      </form>
      <div className="panel p-4">
        <button className="underline" onClick={signOutEverywhere}>Sign out on every device</button>
      </div>
    </div>
  );
}
