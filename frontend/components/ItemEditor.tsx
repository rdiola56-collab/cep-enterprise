"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type FormulaInfo, type ModuleInfo, type WbsBuilding } from "@/lib/api";

export type EditableItem = {
  item_key: string; module: string; item_type: string; description: string;
  inputs: Record<string, { value: string; unit: string }>;
  condition?: string;
  priced_item_code?: string | null;
  floor_id?: string | null;
  zone_id?: string | null;
  room_id?: string | null;
};

type Props = {
  pid: string;
  base: string;
  editing: EditableItem | null;
  onSaved: () => void;
  onCancel: () => void;
  locked: boolean;
};

const CONDITIONS = ["normal", "high_rise", "commercial", "basement", "difficult_access"];

export default function ItemEditor({ pid, base, editing, onSaved, onCancel, locked }: Props) {
  const [modules, setModules] = useState<ModuleInfo[]>([]);
  const [formulas, setFormulas] = useState<FormulaInfo[]>([]);
  const [wbs, setWbs] = useState<WbsBuilding[]>([]);
  const [module, setModule] = useState("");
  const [itemType, setItemType] = useState("");
  const [key, setKey] = useState("");
  const [description, setDescription] = useState("");
  const [condition, setCondition] = useState("normal");
  const [floorId, setFloorId] = useState("");
  const [priced, setPriced] = useState("");
  const [vals, setVals] = useState<Record<string, { value: string; unit: string }>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<ModuleInfo[]>(`/modules?project_id=${pid}`).then((ms) => setModules(ms.filter((m) => m.kind === "estimating")));
    api<FormulaInfo[]>(`/formulas?project_id=${pid}`).then(setFormulas);
    api<WbsBuilding[]>(`/projects/${pid}/wbs`).then(setWbs);
  }, [pid]);

  const mod = modules.find((m) => m.code === module);
  const it = mod?.item_types.find((t) => t.code === itemType);
  const formula = formulas.find((f) => f.code === it?.formula);
  const floors = useMemo(() => wbs.flatMap((b) => b.floors.map((f) => ({ ...f, building_id: b.id, label: `${b.code} / ${f.code}` }))), [wbs]);

  useEffect(() => {
    if (!editing) return;
    setModule(editing.module);
    setItemType(editing.item_type);
    setKey(editing.item_key);
    setDescription(editing.description);
    setCondition(editing.condition ?? "normal");
    setFloorId(editing.floor_id ?? "");
    setPriced(editing.priced_item_code ?? "");
    setVals(editing.inputs);
  }, [editing]);

  useEffect(() => {
    if (!formula || editing?.item_type === itemType) return;
    setVals(Object.fromEntries(formula.inputs.map((i) => [i.name, { value: i.default ?? "", unit: i.unit }])));
  }, [formula, itemType, editing]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!it) return;
    setBusy(true);
    setError(null);
    const floor = floors.find((f) => f.id === floorId);
    const inputs = Object.fromEntries(Object.entries(vals).filter(([, v]) => v.value !== ""));
    try {
      await api(`${base}/items`, {
        method: "POST",
        body: JSON.stringify({
          item_key: key || `${module}:${itemType}:${Date.now().toString(36)}`,
          module, item_type: itemType, inputs, condition,
          description: description || null,
          priced_item_code: it.priced_directly ? priced : null,
          building_id: floor?.building_id ?? null, floor_id: floor?.id ?? null,
          // keep a zone/room only while the item stays on the same floor
          zone_id: editing && editing.floor_id === floor?.id ? editing.zone_id ?? null : null,
          room_id: editing && editing.floor_id === floor?.id ? editing.room_id ?? null : null,
        }),
      });
      setKey("");
      setDescription("");
      setFloorId("");
      onSaved();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  if (locked) return null;

  return (
    <form onSubmit={save} className="panel space-y-3 p-4 text-sm">
      <div className="flex items-center justify-between">
        <div className="font-semibold">{editing ? `Edit ${editing.item_key}` : "Add item"}</div>
        {editing && <button type="button" className="underline" onClick={onCancel}>Cancel edit</button>}
      </div>
      <div className="flex flex-wrap gap-2">
        <select className="field" value={module} disabled={!!editing} required
          onChange={(e) => { setModule(e.target.value); setItemType(""); }}>
          <option value="">Module…</option>
          {modules.map((m) => <option key={m.code} value={m.code}>{m.number}. {m.name}</option>)}
        </select>
        <select className="field" value={itemType} disabled={!mod || !!editing} required onChange={(e) => setItemType(e.target.value)}>
          <option value="">Item type…</option>
          {mod?.item_types.map((t) => (
            <option key={t.code} value={t.code}>
              {t.label}{t.assembly_available === false ? " (assembly missing)" : ""}
            </option>
          ))}
        </select>
        <input className="field w-48" placeholder="Item key (auto)" value={key} disabled={!!editing} onChange={(e) => setKey(e.target.value)} />
        <input className="field flex-1" placeholder="Description" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      {it && (
        <>
          <div className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-2">
            {formula?.inputs.map((i) => (
              <label key={i.name} className="flex items-center gap-1">
                <span className="muted w-24 truncate" title={i.label || i.name}>{i.label || i.name}</span>
                <input className="field w-20 text-right" value={vals[i.name]?.value ?? ""}
                  onChange={(e) => setVals({ ...vals, [i.name]: { value: e.target.value, unit: vals[i.name]?.unit ?? i.unit } })} />
                <input className="field w-14" value={vals[i.name]?.unit ?? i.unit}
                  onChange={(e) => setVals({ ...vals, [i.name]: { value: vals[i.name]?.value ?? "", unit: e.target.value } })} />
              </label>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select className="field" value={condition} onChange={(e) => setCondition(e.target.value)}>
              {CONDITIONS.map((c) => <option key={c} value={c}>{c.replace("_", " ")}</option>)}
            </select>
            <select className="field" value={floorId} onChange={(e) => setFloorId(e.target.value)}>
              <option value="">No location</option>
              {floors.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
            </select>
            {it.priced_directly && (
              <input className="field w-48" placeholder="Price-book item code" value={priced} required
                onChange={(e) => setPriced(e.target.value)} />
            )}
            <span className="muted text-xs">formula {it.formula}{it.default_assembly ? ` · assembly ${it.default_assembly}` : ""}</span>
            <button className="btn ml-auto" disabled={busy}>{busy ? "Calculating…" : editing ? "Recalculate" : "Add and calculate"}</button>
          </div>
        </>
      )}
      {error && <div className="text-red-500">{error}</div>}
    </form>
  );
}
