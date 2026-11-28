"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type U = { id: string; email: string; name: string; global_role: string; is_active: boolean; locked: boolean; sso_linked: boolean };
const ROLES = ["admin", "cost_engineer", "estimator", "viewer"];

export default function UsersPage() {
  const [users, setUsers] = useState<U[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [link, setLink] = useState<{ email: string; url: string; expires: string } | null>(null);
  const [f, setF] = useState({ email: "", name: "", password: "", global_role: "estimator" });

  const load = () => api<U[]>("/users").then(setUsers).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); load(); } catch (e) { setError((e as Error).message); }
  }

  const resetLink = (u: U) => act(async () => {
    const r = await api<{ path: string; expires_at: string }>(`/users/${u.id}/reset-link`, { method: "POST" });
    setLink({ email: u.email, url: `${window.location.origin}${r.path}`, expires: new Date(r.expires_at).toLocaleString() });
  });

  return (
    <div className="space-y-4 text-sm">
      <h1 className="text-xl font-semibold">Users</h1>
      {error && <div className="text-red-500">{error}</div>}
      {link && (
        <div className="panel space-y-1 p-4">
          <div>One-time reset link for <b>{link.email}</b>, valid until {link.expires}. CEP does not email it: pass it on yourself.</div>
          <input className="field w-full font-mono text-xs" readOnly value={link.url} onFocus={(e) => e.target.select()} />
          <button className="underline" onClick={() => navigator.clipboard?.writeText(link.url)}>Copy</button>{" "}
          <button className="underline" onClick={() => setLink(null)}>Done</button>
        </div>
      )}
      <section className="panel overflow-x-auto">
        <table className="datagrid">
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>SSO</th><th /></tr></thead>
          <tbody>{users.map((u) => (
            <tr key={u.id}>
              <td>{u.name}</td><td>{u.email}</td><td>{u.global_role.replace("_", " ")}</td>
              <td className={u.locked ? "text-amber-600" : u.is_active ? "" : "muted"}>{!u.is_active ? "deactivated" : u.locked ? "locked" : "active"}</td>
              <td>{u.sso_linked ? "linked" : ""}</td>
              <td className="space-x-2 whitespace-nowrap text-xs">
                <button className="underline" onClick={() => resetLink(u)}>Reset link</button>
                {u.locked && <button className="underline" onClick={() => act(() => api(`/users/${u.id}/unlock`, { method: "POST" }))}>Unlock</button>}
                <button className={u.is_active ? "text-red-500 underline" : "underline"}
                  onClick={() => act(() => api(`/users/${u.id}/active`, { method: "POST", body: JSON.stringify({ is_active: !u.is_active }) }))}>
                  {u.is_active ? "Deactivate" : "Reactivate"}</button>
              </td>
            </tr>
          ))}</tbody>
        </table>
      </section>
      <form className="panel flex flex-wrap items-end gap-2 p-4" onSubmit={(e) => { e.preventDefault(); act(async () => {
        await api("/users", { method: "POST", body: JSON.stringify(f) }); setF({ email: "", name: "", password: "", global_role: "estimator" }); }); }}>
        <label className="flex flex-col"><span className="muted text-xs">Email</span><input className="field w-56" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} /></label>
        <label className="flex flex-col"><span className="muted text-xs">Name</span><input className="field w-44" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="flex flex-col"><span className="muted text-xs">Temporary password</span><input className="field w-44" type="password" minLength={10} required value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
        <label className="flex flex-col"><span className="muted text-xs">Role</span>
          <select className="field" value={f.global_role} onChange={(e) => setF({ ...f, global_role: e.target.value })}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></label>
        <button className="btn">Add user</button>
      </form>
    </div>
  );
}
