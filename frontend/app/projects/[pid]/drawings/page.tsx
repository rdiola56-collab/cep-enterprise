"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { api, upload, type Me, type Sheet } from "@/lib/api";

export default function DrawingsPage({ params }: { params: Promise<{ pid: string }> }) {
  const { pid } = use(params);
  const [sheets, setSheets] = useState<Sheet[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [vals, setVals] = useState({ set_name: "", revision: "" });
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => { api<Me>("/auth/me").then(setMe).catch(() => undefined); }, []);
  const role = me?.projects[pid];
  const canEdit = me?.global_role === "admin" || role === "manager" || role === "estimator";

  const load = () => api<Sheet[]>(`/projects/${pid}/sheets`).then(setSheets).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setError(null);
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("set_name", vals.set_name);
      form.append("revision", vals.revision);
      const s = await upload<Sheet>(`/projects/${pid}/sheets`, form);
      window.location.href = `/projects/${pid}/drawings/${s.id}`;
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="max-w-5xl space-y-4">
      <div className="flex items-center">
        <h1 className="mr-auto text-xl font-semibold">PDF drawings</h1>
        <Link className="panel px-3 py-1 text-sm" href={`/projects/${pid}`}>Back to project</Link>
      </div>
      <p className="muted text-sm">
        Upload a PDF drawing set, set each page&apos;s scale, then measure lengths, areas and counts on screen. Marks are
        sent to an estimate through the same mapping rules and review as any other import. A page without a scale shows no
        quantities: nothing is guessed.
      </p>
      {error && <div className="text-sm text-red-500">{error}</div>}
      {canEdit && (
        <form onSubmit={submit} className="panel flex flex-wrap items-end gap-3 p-4 text-sm">
          <label className="flex flex-col"><span className="muted text-xs">PDF file</span>
            <input type="file" required accept=".pdf,application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
          <label className="flex flex-col"><span className="muted text-xs">Drawing set</span>
            <input className="field w-48" required value={vals.set_name} placeholder="A-100 Floor plans"
              onChange={(e) => setVals({ ...vals, set_name: e.target.value })} /></label>
          <label className="flex flex-col"><span className="muted text-xs">Revision</span>
            <input className="field w-20" required value={vals.revision} placeholder="B"
              onChange={(e) => setVals({ ...vals, revision: e.target.value })} /></label>
          <button className="btn" disabled={busy || !file}>{busy ? "Uploading…" : "Upload"}</button>
        </form>
      )}
      <section className="panel overflow-x-auto">
        <table className="datagrid">
          <thead><tr><th>Drawing set</th><th>Rev.</th><th>File</th><th className="num">Pages</th><th className="num">Marks</th><th>Uploaded by</th><th /></tr></thead>
          <tbody>
            {sheets?.length === 0 && <tr><td colSpan={7} className="muted">No PDF drawings yet.</td></tr>}
            {sheets?.map((s) => (
              <tr key={s.id}>
                <td>{s.set_name}</td><td>{s.revision}</td><td>{s.file_name}</td><td className="num">{s.page_count}</td>
                <td className="num">{typeof s.marks === "number" ? s.marks : s.marks.length}</td><td className="muted">{s.created_by}</td>
                <td><Link className="text-[var(--accent)] underline" href={`/projects/${pid}/drawings/${s.id}`}>Open</Link></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
