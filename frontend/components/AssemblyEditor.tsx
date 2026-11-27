"use client";

import { useState } from "react";
import { api } from "@/lib/api";

type Mat = { item_code: string; description: string; consumption: string; unit: string; wastage_pct: string; category: string };
type Lab = { task: string; crew_code: string; productivity_code: string };
type Eq = { equipment_code: string; description: string; basis: string; hours_per_unit: string; labor_task: string; count: string };

const blankMat: Mat = { item_code: "", description: "", consumption: "", unit: "", wastage_pct: "0", category: "material" };
const blankLab: Lab = { task: "", crew_code: "", productivity_code: "" };
const blankEq: Eq = { equipment_code: "", description: "", basis: "per_unit", hours_per_unit: "", labor_task: "", count: "1" };

function Rows<T extends Record<string, string>>({ title, rows, setRows, blank, fields }: {
  title: string; rows: T[]; setRows: (r: T[]) => void; blank: T;
  fields: { key: keyof T & string; label: string; width: string; options?: string[] }[];
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2"><span className="font-medium">{title}</span>
        <button type="button" className="underline" onClick={() => setRows([...rows, { ...blank }])}>+ add</button></div>
      {rows.map((r, n) => (
        <div key={n} className="flex flex-wrap gap-1">
          {fields.map((f) => f.options ? (
            <select key={f.key} className={`field ${f.width}`} value={r[f.key]}
              onChange={(e) => setRows(rows.map((x, i) => (i === n ? { ...x, [f.key]: e.target.value } : x)))}>
              {f.options.map((o) => <option key={o}>{o}</option>)}
            </select>
          ) : (
            <input key={f.key} className={`field ${f.width}`} placeholder={f.label} value={r[f.key]}
              onChange={(e) => setRows(rows.map((x, i) => (i === n ? { ...x, [f.key]: e.target.value } : x)))} />
          ))}
          <button type="button" className="text-red-500" onClick={() => setRows(rows.filter((_, i) => i !== n))}>×</button>
        </div>
      ))}
    </div>
  );
}

export default function AssemblyEditor({ projectId, onSaved }: { projectId: string | null; onSaved: () => void }) {
  const [head, setHead] = useState({ code: "", description: "", trade: "", unit: "", notes: "" });
  const [mats, setMats] = useState<Mat[]>([{ ...blankMat }]);
  const [labs, setLabs] = useState<Lab[]>([{ ...blankLab }]);
  const [eqs, setEqs] = useState<Eq[]>([]);
  const [msg, setMsg] = useState<string | null>(null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    try {
      const r = await api<{ revision: number }>("/library/assemblies", {
        method: "POST",
        body: JSON.stringify({
          ...head, project_id: projectId,
          materials: mats.filter((m) => m.item_code),
          labor: labs.filter((l) => l.task),
          equipment: eqs.filter((q) => q.equipment_code).map((q) => ({
            equipment_code: q.equipment_code, description: q.description, basis: q.basis, count: q.count || "1",
            ...(q.basis === "per_unit" ? { hours_per_unit: q.hours_per_unit } : { labor_task: q.labor_task }),
          })),
        }),
      });
      setMsg(`Saved ${head.code} as revision ${r.revision}.`);
      onSaved();
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  return (
    <form onSubmit={save} className="panel space-y-3 p-4 text-sm">
      <div className="font-semibold">New assembly or new revision</div>
      <p className="muted text-xs">Saving an existing code adds a revision; earlier revisions stay attached to the estimates that used them.</p>
      <div className="flex flex-wrap gap-2">
        {(["code", "description", "trade", "unit"] as const).map((k) => (
          <input key={k} className={`field ${k === "description" ? "flex-1" : "w-32"}`} placeholder={k} required value={head[k]}
            onChange={(e) => setHead({ ...head, [k]: e.target.value })} />
        ))}
      </div>
      <Rows title="Materials (per assembly unit)" rows={mats} setRows={setMats} blank={blankMat} fields={[
        { key: "item_code", label: "item code", width: "w-28" }, { key: "description", label: "description", width: "w-48" },
        { key: "consumption", label: "consumption", width: "w-24" }, { key: "unit", label: "unit", width: "w-16" },
        { key: "wastage_pct", label: "waste %", width: "w-16" },
        { key: "category", label: "category", width: "w-28", options: ["material", "subcontract", "other"] }]} />
      <Rows title="Labor" rows={labs} setRows={setLabs} blank={blankLab} fields={[
        { key: "task", label: "task", width: "w-32" }, { key: "crew_code", label: "crew code", width: "w-28" },
        { key: "productivity_code", label: "productivity code", width: "w-36" }]} />
      <Rows title="Equipment" rows={eqs} setRows={setEqs} blank={blankEq} fields={[
        { key: "equipment_code", label: "equipment code", width: "w-32" }, { key: "description", label: "description", width: "w-40" },
        { key: "basis", label: "basis", width: "w-28", options: ["per_unit", "labor_time"] },
        { key: "hours_per_unit", label: "h per unit", width: "w-20" }, { key: "labor_task", label: "labor task", width: "w-28" },
        { key: "count", label: "count", width: "w-14" }]} />
      <div className="flex items-center gap-3"><button className="btn">Save assembly</button>{msg && <span>{msg}</span>}</div>
    </form>
  );
}
