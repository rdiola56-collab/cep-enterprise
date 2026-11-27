"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Project = { id: string; code: string; name: string; currency: string };

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [form, setForm] = useState({ code: "", name: "", currency: "PHP" });
  const [error, setError] = useState<string | null>(null);

  const load = () => api<Project[]>("/projects").then(setProjects).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api("/projects", { method: "POST", body: JSON.stringify(form) });
      setForm({ code: "", name: "", currency: form.currency });
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-xl font-semibold">Projects</h1>
      <section className="panel">
        <table className="datagrid">
          <thead><tr><th>Code</th><th>Name</th><th>Currency</th></tr></thead>
          <tbody>
            {projects?.map((p) => (
              <tr key={p.id}>
                <td><Link className="text-[var(--accent)]" href={`/projects/${p.id}`}>{p.code}</Link></td>
                <td>{p.name}</td>
                <td>{p.currency}</td>
              </tr>
            ))}
            {projects?.length === 0 && <tr><td colSpan={3} className="muted">No projects you are a member of yet.</td></tr>}
          </tbody>
        </table>
      </section>
      <form onSubmit={create} className="panel flex flex-wrap items-end gap-2 p-4 text-sm">
        <label>Code<input className="field mt-1 block w-28" value={form.code} required
          onChange={(e) => setForm({ ...form, code: e.target.value })} /></label>
        <label className="flex-1">Name<input className="field mt-1 block w-full" value={form.name} required
          onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
        <label>Currency<input className="field mt-1 block w-20" value={form.currency} maxLength={3} required
          onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })} /></label>
        <button className="btn">New project</button>
        {error && <div className="w-full text-red-500">{error}</div>}
      </form>
    </div>
  );
}
