"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { api, money } from "@/lib/api";

type Version = {
  estimate_id: string; code: string; name: string; version_id: string; version_no: number;
  status: string; pricing_date: string; total_amount: string | null; approved_by: string | null;
};

export default function ProjectPage({ params }: { params: Promise<{ pid: string }> }) {
  const { pid } = use(params);
  const [versions, setVersions] = useState<Version[] | null>(null);
  const [form, setForm] = useState({ code: "", name: "", pricing_date: new Date().toISOString().slice(0, 10) });
  const [error, setError] = useState<string | null>(null);

  const load = () => api<Version[]>(`/projects/${pid}/estimates`).then(setVersions).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api(`/projects/${pid}/estimates`, { method: "POST", body: JSON.stringify(form) });
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="max-w-5xl space-y-4">
      <h1 className="text-xl font-semibold">Estimates</h1>
      <section className="panel">
        <table className="datagrid">
          <thead><tr><th>Estimate</th><th>Version</th><th>Status</th><th>Pricing date</th><th className="num">Total</th><th>Approved by</th></tr></thead>
          <tbody>
            {versions?.map((v) => (
              <tr key={v.version_id}>
                <td>{v.code} · {v.name}</td>
                <td><Link className="text-[var(--accent)]" href={`/projects/${pid}/versions/${v.version_id}`}>v{v.version_no}</Link></td>
                <td>{v.status}</td>
                <td>{v.pricing_date}</td>
                <td className="num">{money(v.total_amount)}</td>
                <td className="muted">{v.approved_by ?? ""}</td>
              </tr>
            ))}
            {versions?.length === 0 && <tr><td colSpan={6} className="muted">No estimates yet.</td></tr>}
          </tbody>
        </table>
      </section>
      <form onSubmit={create} className="panel flex flex-wrap items-end gap-2 p-4 text-sm">
        <label>Code<input className="field mt-1 block w-28" value={form.code} required
          onChange={(e) => setForm({ ...form, code: e.target.value })} /></label>
        <label className="flex-1">Name<input className="field mt-1 block w-full" value={form.name} required
          onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label>Pricing date<input className="field mt-1 block" type="date" value={form.pricing_date} required
          onChange={(e) => setForm({ ...form, pricing_date: e.target.value })} /></label>
        <button className="btn">New estimate</button>
        {error && <div className="w-full text-red-500">{error}</div>}
      </form>
    </div>
  );
}
