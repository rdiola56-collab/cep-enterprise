"use client";

import { useEffect, useState } from "react";
import { api, type WbsBuilding, type WbsFloor } from "@/lib/api";

type Kind = "buildings" | "floors" | "zones" | "rooms";
type Draft = Record<string, string | boolean>;
type Editing = { kind: Kind; id: string | null; parent?: string; draft: Draft } | null;

const FIELDS: Record<Kind, { key: string; label: string; type?: "number" | "checkbox" | "zone"; w?: string }[]> = {
  buildings: [{ key: "code", label: "Code", w: "w-20" }, { key: "name", label: "Name", w: "w-48" }],
  floors: [
    { key: "code", label: "Code", w: "w-20" }, { key: "name", label: "Name", w: "w-40" },
    { key: "level", label: "Level", type: "number", w: "w-16" }, { key: "gross_floor_area_m2", label: "GFA m²", type: "number", w: "w-24" },
    { key: "is_basement", label: "Basement", type: "checkbox" },
  ],
  zones: [{ key: "code", label: "Code", w: "w-20" }, { key: "name", label: "Name", w: "w-40" }],
  rooms: [{ key: "code", label: "Code", w: "w-20" }, { key: "name", label: "Name", w: "w-40" }, { key: "zone_id", label: "Zone", type: "zone" }],
};
const PARENT_KEY: Record<Kind, string | null> = { buildings: null, floors: "building_id", zones: "floor_id", rooms: "floor_id" };

export default function WbsEditor({ pid, canEdit }: { pid: string; canEdit: boolean }) {
  const [tree, setTree] = useState<WbsBuilding[]>([]);
  const [editing, setEditing] = useState<Editing>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());

  const load = () => api<WbsBuilding[]>(`/projects/${pid}/wbs`).then(setTree).catch((e) => setError(e.message));
  useEffect(() => { load(); }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggle = (id: string) => { const n = new Set(open); if (n.has(id)) n.delete(id); else n.add(id); setOpen(n); };

  async function save() {
    if (!editing) return;
    setError(null);
    const body: Record<string, unknown> = {};
    for (const f of FIELDS[editing.kind]) {
      const v = editing.draft[f.key];
      if (f.type === "checkbox") body[f.key] = Boolean(v);
      else if (f.type === "number") body[f.key] = v === "" || v === undefined ? null : f.key === "level" ? Number(v) : String(v);
      else if (f.type === "zone") body[f.key] = v ? v : null;
      else body[f.key] = v ?? "";
    }
    try {
      if (editing.id) {
        await api(`/projects/${pid}/wbs/${editing.kind}/${editing.id}`, { method: "PATCH", body: JSON.stringify(body) });
      } else {
        const pk = PARENT_KEY[editing.kind];
        if (pk) body[pk] = editing.parent;
        if (editing.kind === "floors" && body.level === null) body.level = 0;
        await api(`/projects/${pid}/${editing.kind}`, { method: "POST", body: JSON.stringify(body) });
        if (editing.parent) setOpen(new Set(open).add(editing.parent));
      }
      setEditing(null);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function remove(kind: Kind, id: string, label: string) {
    setError(null);
    try {
      await api(`/projects/${pid}/wbs/${kind}/${id}`, { method: "DELETE" });
      load();
    } catch (e) {
      setError(`${label}: ${(e as Error).message}`);
    }
  }

  const start = (kind: Kind, id: string | null, values: Draft, parent?: string) => {
    if (parent) setOpen(new Set(open).add(parent)); // show the new row's form under its parent
    setEditing({ kind, id, parent, draft: values });
  };

  function form(floor?: WbsFloor) {
    if (!editing) return null;
    return (
      <span className="inline-flex flex-wrap items-center gap-2">
        {FIELDS[editing.kind].map((f) => {
          const v = editing.draft[f.key];
          const set = (val: string | boolean) => setEditing({ ...editing, draft: { ...editing.draft, [f.key]: val } });
          if (f.type === "checkbox") {
            return <label key={f.key} className="flex items-center gap-1 text-xs"><input type="checkbox" checked={Boolean(v)} onChange={(e) => set(e.target.checked)} />{f.label}</label>;
          }
          if (f.type === "zone") {
            return (
              <select key={f.key} className="field text-xs" value={String(v ?? "")} onChange={(e) => set(e.target.value)}>
                <option value="">(no zone)</option>{floor?.zones.map((z) => <option key={z.id} value={z.id}>{z.code} {z.name}</option>)}
              </select>
            );
          }
          return <input key={f.key} aria-label={f.label} placeholder={f.label} className={`field text-xs ${f.w ?? ""}`} type={f.type === "number" ? "number" : "text"}
            step="any" value={String(v ?? "")} onChange={(e) => set(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(null); }} />;
        })}
        <button className="btn text-xs" onClick={save}>Save</button>
        <button className="text-xs underline" onClick={() => setEditing(null)}>Cancel</button>
      </span>
    );
  }

  const isEditing = (kind: Kind, id: string | null, parent?: string) =>
    editing && editing.kind === kind && editing.id === id && (id !== null || editing.parent === parent);

  const actions = (kind: Kind, id: string, values: Draft, label: string, extra?: React.ReactNode) => canEdit && (
    <span className="ml-3 space-x-2 text-xs">
      <button className="underline" onClick={() => start(kind, id, values)}>Edit</button>
      {extra}
      <button className="text-red-500 underline" onClick={() => remove(kind, id, label)}>Delete</button>
    </span>
  );

  return (
    <section className="panel space-y-2 p-4 text-sm">
      <div className="flex items-center">
        <h2 className="mr-auto font-medium">Buildings, floors, zones and rooms</h2>
        {canEdit && !isEditing("buildings", null) && <button className="panel px-3 py-1 text-xs" onClick={() => start("buildings", null, { code: "", name: "" })}>Add building</button>}
      </div>
      <p className="muted text-xs">Items keep their location by reference, so renaming never changes an amount. A node that estimate items or child nodes still use cannot be deleted.</p>
      {error && <div className="text-red-500">{error}</div>}
      {isEditing("buildings", null) && <div>{form()}</div>}
      {tree.length === 0 && !editing && <div className="muted">No buildings yet.</div>}
      <ul className="space-y-1">
        {tree.map((b) => (
          <li key={b.id}>
            <div className="flex items-center">
              <button className="w-5 text-left" onClick={() => toggle(b.id)}>{open.has(b.id) ? "▾" : "▸"}</button>
              {isEditing("buildings", b.id) ? form() : <>
                <span className="font-medium">{b.code}</span>&nbsp;{b.name}
                <span className="muted ml-2 text-xs">{b.floors.length} floor(s)</span>
                {actions("buildings", b.id, { code: b.code, name: b.name }, b.code,
                  <button className="underline" onClick={() => start("floors", null, { code: "", name: "", level: String((b.floors.at(-1)?.level ?? 0) + 1), gross_floor_area_m2: "", is_basement: false }, b.id)}>Add floor</button>)}
              </>}
            </div>
            {open.has(b.id) && (
              <ul className="ml-5 space-y-1 border-l border-[var(--line)] pl-3">
                {isEditing("floors", null, b.id) && <li>{form()}</li>}
                {b.floors.map((f) => (
                  <li key={f.id}>
                    <div className="flex items-center">
                      <button className="w-5 text-left" onClick={() => toggle(f.id)}>{open.has(f.id) ? "▾" : "▸"}</button>
                      {isEditing("floors", f.id) ? form() : <>
                        <span className="font-medium">{f.code}</span>&nbsp;{f.name}
                        <span className="muted ml-2 text-xs">level {f.level}{f.is_basement ? " · basement" : ""}
                          {f.gross_floor_area_m2 ? ` · ${f.gross_floor_area_m2} m²` : ""}</span>
                        {actions("floors", f.id, { code: f.code, name: f.name, level: String(f.level), is_basement: f.is_basement,
                          gross_floor_area_m2: f.gross_floor_area_m2 ?? "" }, f.code, <>
                          <button className="underline" onClick={() => start("zones", null, { code: "", name: "" }, f.id)}>Add zone</button>
                          <button className="underline" onClick={() => start("rooms", null, { code: "", name: "", zone_id: "" }, f.id)}>Add room</button>
                        </>)}
                      </>}
                    </div>
                    {open.has(f.id) && (
                      <ul className="ml-5 space-y-1 border-l border-[var(--line)] pl-3">
                        {(isEditing("zones", null, f.id) || isEditing("rooms", null, f.id)) && <li>{form(f)}</li>}
                        {f.zones.map((z) => (
                          <li key={z.id}>{isEditing("zones", z.id) ? form(f) : <>
                            <span className="muted text-xs">zone</span> <span className="font-medium">{z.code}</span> {z.name}
                            {actions("zones", z.id, { code: z.code, name: z.name }, z.code)}</>}
                          </li>
                        ))}
                        {f.rooms.map((r) => (
                          <li key={r.id}>{isEditing("rooms", r.id) ? form(f) : <>
                            <span className="muted text-xs">room</span> <span className="font-medium">{r.code}</span> {r.name}
                            {r.zone_id && <span className="muted ml-1 text-xs">in {f.zones.find((z) => z.id === r.zone_id)?.code}</span>}
                            {actions("rooms", r.id, { code: r.code, name: r.name, zone_id: r.zone_id ?? "" }, r.code)}</>}
                          </li>
                        ))}
                        {f.zones.length === 0 && f.rooms.length === 0 && !editing && <li className="muted text-xs">No zones or rooms.</li>}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
