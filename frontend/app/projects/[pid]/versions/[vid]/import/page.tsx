"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import { api, money, qty, upload, type ModuleInfo, type WbsBuilding } from "@/lib/api";

type Adapter = { label: string; extensions: string[]; status: string };
type Rule = {
  id: string; code: string; adapter: string; match_group: string; match_label: string | null;
  match_properties: Record<string, string>; module: string; item_type: string; assembly_code: string | null;
  priced_item_code: string | null; quantity_basis: string; input_map: Record<string, string | { value: string; unit: string }>;
  condition: string; aggregate: boolean; priority: number;
};
type Measure = { value: string; unit: string };
type Row = {
  id: string; row_no: number; source_ref: string; group: string; label: string; level: string | null;
  measures: Record<string, Measure>; properties: Record<string, string>; status: string; messages: string[];
  rule_code: string | null; item_key: string | null;
  preview: { quantity: string; unit: string; amount: string; rows: number; methodology: string } | null;
};
type Batch = {
  id: string; adapter: string; source_name: string; source_revision: string; file_name: string; status: string;
  created_at: string; created_by: string;
  summary: { counts?: Record<string, number>; warnings?: string[]; info?: Record<string, unknown>; items?: number;
    new_items?: number; updated_items?: number; stale_items?: string[]; total_amount?: string; ready?: boolean };
  rows?: Row[];
};

const STATUS_STYLE: Record<string, string> = {
  mapped: "text-green-600", imported: "text-green-600", unmapped: "text-amber-600", invalid: "text-red-500", excluded: "muted",
};

function lines(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const l of text.split("\n")) {
    const i = l.indexOf("=");
    if (i > 0) out[l.slice(0, i).trim()] = l.slice(i + 1).trim();
  }
  return out;
}

export default function ImportPage({ params }: { params: Promise<{ pid: string; vid: string }> }) {
  const { pid, vid } = use(params);
  const back = `/projects/${pid}/versions/${vid}`;
  const [adapters, setAdapters] = useState<Record<string, Adapter>>({});
  const [modules, setModules] = useState<ModuleInfo[]>([]);
  const [wbs, setWbs] = useState<WbsBuilding[]>([]);
  const [rules, setRules] = useState<Rule[]>([]);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [open, setOpen] = useState<Batch | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadRules = () => api<Rule[]>(`/projects/${pid}/import-rules`).then(setRules).catch((e) => setError(e.message));
  const loadBatches = () => api<Batch[]>(`/projects/${pid}/imports?version_id=${vid}`).then(setBatches).catch(() => undefined);
  useEffect(() => {
    api<Record<string, Adapter>>("/import/adapters").then(setAdapters).catch(() => undefined);
    api<ModuleInfo[]>(`/modules?project_id=${pid}`).then((m) => setModules(m.filter((x) => x.kind === "estimating"))).catch(() => undefined);
    api<WbsBuilding[]>(`/projects/${pid}/wbs`).then(setWbs).catch(() => undefined);
    loadRules();
    loadBatches();
  }, [pid, vid]); // eslint-disable-line react-hooks/exhaustive-deps

  async function run<T>(fn: () => Promise<T>): Promise<T | undefined> {
    setError(null);
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const openBatch = (id: string) => run(async () => setOpen(await api<Batch>(`/projects/${pid}/imports/${id}`)));
  const after = (b: Batch | undefined) => { if (b) setOpen(b); loadBatches(); };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-xl font-semibold">Import drawing and BIM quantities</h1>
        <Link className="panel px-3 py-1 text-sm" href={back}>Back to estimate</Link>
      </div>
      <p className="muted text-sm">
        Files are read, mapped by your rules and priced through the same engine as manual entries. Nothing reaches the
        estimate until every row is mapped and valid or excluded with a reason.
      </p>
      {error && <div className="text-sm text-red-500">{error}</div>}
      <UploadForm adapters={adapters} wbs={wbs} busy={busy}
        onUpload={(form) => run(async () => after(await upload<Batch>(`/projects/${pid}/versions/${vid}/imports`, form)))} />
      {open && (
        <BatchView batch={open} busy={busy} onClose={() => setOpen(null)}
          remap={() => run(async () => after(await api<Batch>(`/projects/${pid}/imports/${open.id}/remap`, { method: "POST" })))}
          exclude={(ids, excluded, reason) => run(async () => after(await api<Batch>(`/projects/${pid}/imports/${open.id}/exclude`,
            { method: "POST", body: JSON.stringify({ row_ids: ids, excluded, reason }) })))}
          commit={(removeMissing) => run(async () => after(await api<Batch>(`/projects/${pid}/imports/${open.id}/commit`,
            { method: "POST", body: JSON.stringify({ remove_missing: removeMissing }) })))}
          discard={() => run(async () => { await api(`/projects/${pid}/imports/${open.id}/discard`, { method: "POST" }); setOpen(null); loadBatches(); })} />
      )}
      <section className="panel overflow-x-auto">
        <h2 className="px-3 pt-3 font-medium">Imports into this version</h2>
        <table className="datagrid">
          <thead><tr><th>File</th><th>Source</th><th>Rev.</th><th>Type</th><th>Status</th><th className="num">Rows</th><th className="num">Items</th><th className="num">Amount</th><th /></tr></thead>
          <tbody>
            {batches.length === 0 && <tr><td colSpan={9} className="muted">No imports yet.</td></tr>}
            {batches.map((b) => (
              <tr key={b.id}>
                <td>{b.file_name}</td><td>{b.source_name}</td><td>{b.source_revision}</td><td>{b.adapter}</td>
                <td className={STATUS_STYLE[b.status === "committed" ? "imported" : b.status === "discarded" ? "excluded" : "mapped"]}>{b.status}</td>
                <td className="num">{Object.values(b.summary.counts ?? {}).reduce((a, n) => a + n, 0)}</td>
                <td className="num">{b.summary.items ?? 0}</td><td className="num">{money(b.summary.total_amount)}</td>
                <td><button className="underline text-xs" onClick={() => openBatch(b.id)}>Review</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <RulesPanel pid={pid} rules={rules} modules={modules} adapters={adapters} onChange={loadRules} setError={setError} />
    </div>
  );
}

function UploadForm({ adapters, wbs, busy, onUpload }: {
  adapters: Record<string, Adapter>; wbs: WbsBuilding[]; busy: boolean; onUpload: (f: FormData) => void;
}) {
  const [adapter, setAdapter] = useState("takeoff_csv");
  const [file, setFile] = useState<File | null>(null);
  const [vals, setVals] = useState({ source_name: "", source_revision: "", level: "", drawing_unit: "", default_unit: "", building_id: "" });
  const set = (k: keyof typeof vals) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setVals({ ...vals, [k]: e.target.value });
  const floors = wbs.flatMap((b) => b.floors.map((f) => ({ ...f, building: b.code })));

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    form.append("adapter", adapter);
    for (const [k, v] of Object.entries(vals)) if (v) form.append(k, v);
    onUpload(form);
  }

  return (
    <form onSubmit={submit} className="panel flex flex-wrap items-end gap-3 p-4 text-sm">
      <label className="flex flex-col"><span className="muted text-xs">Source type</span>
        <select className="field" value={adapter} onChange={(e) => setAdapter(e.target.value)}>
          {Object.entries(adapters).map(([k, a]) => (
            <option key={k} value={k} disabled={a.status !== "operational"}>{a.label}{a.status !== "operational" ? ` (${a.status})` : ""}</option>
          ))}
        </select>
      </label>
      <label className="flex flex-col"><span className="muted text-xs">File</span>
        <input type="file" required accept={(adapters[adapter]?.extensions ?? []).join(",")} onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
      <label className="flex flex-col"><span className="muted text-xs">Drawing set / model name</span>
        <input className="field w-48" required value={vals.source_name} onChange={set("source_name")} placeholder="A-201 Finishes" /></label>
      <label className="flex flex-col"><span className="muted text-xs">Revision</span>
        <input className="field w-20" required value={vals.source_revision} onChange={set("source_revision")} placeholder="C" /></label>
      <label className="flex flex-col"><span className="muted text-xs">Building</span>
        <select className="field" value={vals.building_id} onChange={set("building_id")}>
          <option value="">(any)</option>{wbs.map((b) => <option key={b.id} value={b.id}>{b.code} {b.name}</option>)}
        </select></label>
      {adapter === "dxf" && <>
        <label className="flex flex-col"><span className="muted text-xs">Floor of this plan</span>
          <select className="field" value={vals.level} onChange={set("level")}>
            <option value="">(none)</option>{floors.map((f) => <option key={f.id} value={f.code}>{f.building} {f.code} {f.name}</option>)}
          </select></label>
        <label className="flex flex-col"><span className="muted text-xs">Drawing unit (if not set in file)</span>
          <input className="field w-24" value={vals.drawing_unit} onChange={set("drawing_unit")} placeholder="mm" /></label>
      </>}
      {adapter === "takeoff_csv" && (
        <label className="flex flex-col"><span className="muted text-xs">Unit for bare numbers</span>
          <input className="field w-24" value={vals.default_unit} onChange={set("default_unit")} placeholder="(refuse)" /></label>
      )}
      <button className="btn" disabled={busy || !file}>{busy ? "Reading…" : "Read file"}</button>
    </form>
  );
}

function BatchView({ batch, busy, onClose, remap, exclude, commit, discard }: {
  batch: Batch; busy: boolean; onClose: () => void; remap: () => void;
  exclude: (ids: string[], excluded: boolean, reason: string) => void; commit: (removeMissing: boolean) => void; discard: () => void;
}) {
  const [status, setStatus] = useState("all");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState("");
  const [removeMissing, setRemoveMissing] = useState(false);
  const s = batch.summary;
  const rows = useMemo(() => (batch.rows ?? []).filter((r) => status === "all" || r.status === status), [batch, status]);
  const staged = batch.status === "staged";
  const toggle = (id: string) => { const n = new Set(picked); if (n.has(id)) n.delete(id); else n.add(id); setPicked(n); };

  return (
    <section className="panel space-y-3 p-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto font-medium">{batch.file_name} <span className="muted">· {batch.source_name} rev. {batch.source_revision} · {batch.status}</span></h2>
        {staged && <button className="panel px-3 py-1" disabled={busy} onClick={remap}>Re-apply rules</button>}
        {staged && <button className="panel px-3 py-1 text-red-500" disabled={busy} onClick={discard}>Discard</button>}
        <button className="panel px-3 py-1" onClick={onClose}>Close</button>
      </div>
      <div className="flex flex-wrap gap-4">
        {Object.entries(s.counts ?? {}).map(([k, n]) => (
          <button key={k} onClick={() => setStatus(status === k ? "all" : k)} className={`${STATUS_STYLE[k]} ${status === k ? "underline" : ""}`}>{n} {k}</button>
        ))}
        <span>{s.items ?? 0} items ({s.new_items ?? 0} new, {s.updated_items ?? 0} updated)</span>
        <span className="font-mono">{money(s.total_amount)}</span>
      </div>
      {(s.warnings ?? []).map((w) => <div key={w} className="text-amber-600">{w}</div>)}
      {(s.stale_items?.length ?? 0) > 0 && (
        <div className="text-amber-600">
          {s.stale_items!.length} item(s) from earlier imports of this source are not in this file: {s.stale_items!.join(", ")}
        </div>
      )}
      {staged && (
        <div className="flex flex-wrap items-center gap-2">
          <input className="field w-72" placeholder="Reason for excluding the selected rows" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button className="panel px-3 py-1" disabled={busy || !picked.size || !reason.trim()}
            onClick={() => { exclude([...picked], true, reason); setPicked(new Set()); setReason(""); }}>Exclude selected</button>
          <button className="panel px-3 py-1" disabled={busy || !picked.size} onClick={() => { exclude([...picked], false, ""); setPicked(new Set()); }}>Include selected</button>
          {(s.stale_items?.length ?? 0) > 0 && (
            <label className="flex items-center gap-1"><input type="checkbox" checked={removeMissing} onChange={(e) => setRemoveMissing(e.target.checked)} /> remove items no longer in the source</label>
          )}
          <button className="btn ml-auto disabled:opacity-40" disabled={busy || !s.ready} onClick={() => commit(removeMissing)}
            title={s.ready ? "" : "Map or exclude every row first"}>Import into estimate</button>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="datagrid">
          <thead><tr>{staged && <th />}<th>#</th><th>Source id</th><th>Group</th><th>Label</th><th>Level</th><th>Measured</th><th>Status</th><th>Rule</th><th className="num">Quantity</th><th className="num">Amount</th><th>Messages</th></tr></thead>
          <tbody>{rows.map((r, n) => {
            // an aggregated item's quantity and amount are shown once, on its first row
            const repeat = !!r.item_key && rows.slice(0, n).some((x) => x.item_key === r.item_key);
            return (
            <tr key={r.id}>
              {staged && <td>{r.status !== "imported" && <input type="checkbox" checked={picked.has(r.id)} onChange={() => toggle(r.id)} />}</td>}
              <td>{r.row_no}</td><td className="font-mono text-xs">{r.source_ref}</td><td>{r.group}</td><td>{r.label}</td><td>{r.level}</td>
              <td className="text-xs">{Object.entries(r.measures).filter(([k]) => !k.includes(".")).slice(0, 4)
                .map(([k, m]) => `${k} ${qty(m.value)} ${m.unit}`).join(" · ")}</td>
              <td className={STATUS_STYLE[r.status]}>{r.status}</td><td>{r.rule_code}</td>
              <td className="num">{repeat ? <span className="muted">in item above</span> : r.preview ? `${qty(r.preview.quantity)} ${r.preview.unit}` : ""}{!repeat && r.preview && r.preview.rows > 1 ? ` (Σ ${r.preview.rows} rows)` : ""}</td>
              <td className="num">{r.preview && !repeat ? money(r.preview.amount) : ""}</td>
              <td className="text-xs">{r.messages.join("; ")}</td>
            </tr>
            );
          })}</tbody>
        </table>
      </div>
    </section>
  );
}

function RulesPanel({ pid, rules, modules, adapters, onChange, setError }: {
  pid: string; rules: Rule[]; modules: ModuleInfo[]; adapters: Record<string, Adapter>; onChange: () => void; setError: (e: string | null) => void;
}) {
  const empty = { code: "", adapter: "takeoff_csv", match_group: "", match_label: "", props: "", module: "", item_type: "",
    quantity_basis: "measured", inputs: "measured=area", condition: "normal", aggregate: false, assembly_code: "", priced_item_code: "", priority: "100" };
  const [f, setF] = useState(empty);
  const mod = modules.find((m) => m.code === f.module);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const input_map: Record<string, string | { value: string; unit: string }> = {};
    for (const [k, v] of Object.entries(lines(f.inputs))) {
      const m = /^([\d.]+)\s+(\S+)$/.exec(v); // "0.2 m" is a constant, anything else a measure name
      input_map[k] = m ? { value: m[1], unit: m[2] } : v;
    }
    try {
      await api(`/projects/${pid}/import-rules`, { method: "POST", body: JSON.stringify({
        code: f.code, adapter: f.adapter, match_group: f.match_group, match_label: f.match_label || null,
        match_properties: lines(f.props), module: f.module, item_type: f.item_type, quantity_basis: f.quantity_basis,
        input_map, condition: f.condition, aggregate: f.aggregate, assembly_code: f.assembly_code || null,
        priced_item_code: f.priced_item_code || null, priority: Number(f.priority) || 100 }) });
      setF(empty);
      onChange();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const deactivate = async (id: string) => { await api(`/projects/${pid}/import-rules/${id}`, { method: "DELETE" }).catch((e) => setError(e.message)); onChange(); };
  const field = (k: keyof typeof empty, label: string, w = "w-40", placeholder = "") => (
    <label className="flex flex-col"><span className="muted text-xs">{label}</span>
      <input className={`field ${w}`} value={String(f[k])} placeholder={placeholder} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>
  );

  return (
    <section className="panel space-y-3 p-4 text-sm">
      <h2 className="font-medium">Mapping rules (this project)</h2>
      <p className="muted text-xs">
        A rule says which markups, layers or model elements become which item type, and which measured value feeds each
        formula input. Patterns use * and ?. The first matching rule by priority wins. Rules hold no prices.
      </p>
      <table className="datagrid">
        <thead><tr><th>Code</th><th>Source</th><th>Matches</th><th>Item type</th><th>Basis</th><th>Inputs</th><th>Aggregate</th><th>Prio.</th><th /></tr></thead>
        <tbody>
          {rules.length === 0 && <tr><td colSpan={9} className="muted">No rules yet: every row will be unmapped.</td></tr>}
          {rules.map((r) => (
            <tr key={r.id}>
              <td className="font-mono text-xs">{r.code}</td><td>{r.adapter}</td>
              <td>{r.match_group}{r.match_label ? ` / ${r.match_label}` : ""}{Object.entries(r.match_properties).map(([k, v]) => ` · ${k}=${v}`).join("")}</td>
              <td>{r.module} / {r.item_type}{r.assembly_code ? ` (${r.assembly_code})` : ""}</td><td>{r.quantity_basis}</td>
              <td className="text-xs">{Object.entries(r.input_map).map(([k, v]) => `${k} ← ${typeof v === "string" ? v : `${v.value} ${v.unit}`}`).join(", ")}</td>
              <td>{r.aggregate ? "per floor" : "per element"}</td><td>{r.priority}</td>
              <td><button className="text-xs text-red-500 underline" onClick={() => deactivate(r.id)}>Retire</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <form onSubmit={save} className="flex flex-wrap items-end gap-2">
        {field("code", "Code", "w-28", "TILE-FLR")}
        <label className="flex flex-col"><span className="muted text-xs">Source type</span>
          <select className="field" value={f.adapter} onChange={(e) => setF({ ...f, adapter: e.target.value })}>
            {Object.entries(adapters).filter(([, a]) => a.status === "operational").map(([k, a]) => <option key={k} value={k}>{a.label}</option>)}
          </select></label>
        {field("match_group", "Subject / layer / IFC class", "w-44", "IfcBeam or A-FLOR-*")}
        {field("match_label", "Label / block / type (optional)", "w-44")}
        <label className="flex flex-col"><span className="muted text-xs">Properties (key=value per line)</span>
          <textarea className="field h-9 w-56 font-mono text-xs" value={f.props} placeholder="Pset_WallCommon.IsExternal=False" onChange={(e) => setF({ ...f, props: e.target.value })} /></label>
        <label className="flex flex-col"><span className="muted text-xs">Module</span>
          <select className="field" required value={f.module} onChange={(e) => setF({ ...f, module: e.target.value, item_type: "" })}>
            <option value="" />{modules.map((m) => <option key={m.code} value={m.code}>{m.number}. {m.name}</option>)}
          </select></label>
        <label className="flex flex-col"><span className="muted text-xs">Item type</span>
          <select className="field" required value={f.item_type} onChange={(e) => setF({ ...f, item_type: e.target.value })}>
            <option value="" />{mod?.item_types.map((t) => <option key={t.code} value={t.code}>{t.label} ({t.formula})</option>)}
          </select></label>
        <label className="flex flex-col"><span className="muted text-xs">Quantity from</span>
          <select className="field" value={f.quantity_basis} onChange={(e) => setF({ ...f, quantity_basis: e.target.value,
            inputs: e.target.value === "measured" ? "measured=area" : "length=Length\nheight=Height", aggregate: e.target.value === "measured" && f.aggregate })}>
            <option value="measured">measured quantity</option><option value="formula">dimensions → item formula</option>
          </select></label>
        <label className="flex flex-col"><span className="muted text-xs">Inputs (input=measure, or input=0.2 m)</span>
          <textarea className="field h-9 w-48 font-mono text-xs" value={f.inputs} onChange={(e) => setF({ ...f, inputs: e.target.value })} /></label>
        {field("assembly_code", "Assembly override", "w-32")}
        {mod?.item_types.find((t) => t.code === f.item_type)?.priced_directly && field("priced_item_code", "Price-book item", "w-32")}
        {field("condition", "Condition", "w-28")}
        {field("priority", "Priority", "w-16")}
        <label className="flex items-center gap-1 pb-1"><input type="checkbox" disabled={f.quantity_basis !== "measured"} checked={f.aggregate}
          onChange={(e) => setF({ ...f, aggregate: e.target.checked })} /> one item per floor</label>
        <button className="btn">Add rule</button>
      </form>
    </section>
  );
}
