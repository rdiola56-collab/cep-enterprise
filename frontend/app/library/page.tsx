"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import AssemblyEditor from "@/components/AssemblyEditor";
import { api } from "@/lib/api";

type Row = Record<string, unknown>;
type Field = { name: string; label: string; type?: "date" | "select"; options?: string[]; required?: boolean; default?: string };
type Tab = { key: string; label: string; list: string; add?: string; columns: [string, string][]; fields?: Field[];
  toBody?: (v: Record<string, string>) => Record<string, unknown> };

const CONDITIONS = ["normal", "high_rise", "commercial", "basement", "difficult_access"];
const TABS: Tab[] = [
  { key: "prices", label: "Prices", list: "/library/prices", add: "/library/prices",
    columns: [["material_code", "Item"], ["unit", "Unit"], ["price", "Price"], ["currency", "Cur."], ["valid_from", "Valid from"], ["valid_to", "Valid to"], ["revision", "Rev."], ["brand", "Brand"], ["scope", "Scope"]],
    fields: [{ name: "material_code", label: "Item code", required: true }, { name: "unit", label: "Unit", required: true },
      { name: "price", label: "Price", required: true }, { name: "currency", label: "Currency", default: "PHP", required: true },
      { name: "valid_from", label: "Valid from", type: "date", required: true }, { name: "brand", label: "Brand" }] },
  { key: "materials", label: "Materials", list: "/library/materials", add: "/library/materials",
    columns: [["code", "Code"], ["description", "Description"], ["unit", "Unit"], ["category", "Category"], ["scope", "Scope"]],
    fields: [{ name: "code", label: "Code", required: true }, { name: "description", label: "Description", required: true },
      { name: "unit", label: "Unit", required: true },
      { name: "category", label: "Category", type: "select", options: ["material", "fuel", "subcontract", "other"], default: "material" }] },
  { key: "labor", label: "Labor rates", list: "/library/labor-rates", add: "/library/labor-rates",
    columns: [["labor_class", "Labor class"], ["hourly_rate", "Hourly rate"], ["currency", "Cur."], ["valid_from", "Valid from"], ["scope", "Scope"]],
    fields: [{ name: "labor_class", label: "Labor class", required: true }, { name: "hourly_rate", label: "Hourly rate", required: true },
      { name: "currency", label: "Currency", default: "PHP", required: true }, { name: "valid_from", label: "Valid from", type: "date", required: true }] },
  { key: "crews", label: "Crews", list: "/library/crews", add: "/library/crews",
    columns: [["code", "Code"], ["name", "Name"], ["members", "Members"], ["revision", "Rev."], ["scope", "Scope"]],
    fields: [{ name: "code", label: "Code", required: true }, { name: "name", label: "Name", required: true },
      { name: "members", label: "Members, e.g. foreman:1, carpenter:2", required: true }],
    toBody: (v) => ({ ...v, members: v.members.split(",").map((p) => p.split(":").map((x) => x.trim())).map(([c, n]) => [c, n || "1"]) }) },
  { key: "productivity", label: "Productivity", list: "/library/productivity", add: "/library/productivity",
    columns: [["code", "Code"], ["condition", "Condition"], ["output_per_crew_day", "Output / crew-day"], ["unit", "Unit"], ["revision", "Rev."], ["scope", "Scope"]],
    fields: [{ name: "code", label: "Code", required: true }, { name: "output_per_crew_day", label: "Output per crew-day", required: true },
      { name: "unit", label: "Unit", required: true }, { name: "condition", label: "Condition", type: "select", options: CONDITIONS, default: "normal" }] },
  { key: "equipment", label: "Equipment", list: "/library/equipment-rates", add: "/library/equipment-rates",
    columns: [["equipment_code", "Equipment"], ["rental_per_hour", "Rental/h"], ["fuel_litres_per_hour", "Fuel L/h"], ["fuel_item_code", "Fuel item"], ["operating_per_hour", "Operating/h"], ["valid_from", "Valid from"], ["scope", "Scope"]],
    fields: [{ name: "equipment_code", label: "Equipment code", required: true }, { name: "rental_per_hour", label: "Rental per hour", required: true },
      { name: "fuel_litres_per_hour", label: "Fuel L/h", default: "0" }, { name: "fuel_item_code", label: "Fuel item code" },
      { name: "operating_per_hour", label: "Operating per hour", default: "0" }, { name: "currency", label: "Currency", default: "PHP", required: true },
      { name: "valid_from", label: "Valid from", type: "date", required: true }] },
  { key: "assemblies", label: "Assemblies", list: "/library/assemblies",
    columns: [["code", "Code"], ["description", "Description"], ["trade", "Trade"], ["unit", "Unit"], ["revision", "Rev."], ["components", "Components"], ["scope", "Scope"]] },
];

function show(v: unknown, key: string): string {
  if (v == null) return "";
  if (key === "members" && Array.isArray(v)) return v.map((m: Row) => `${m.labor_class} × ${Number(m.count)}`).join(", ");
  if (key === "components" && Array.isArray(v)) return v.map((c: Row) => (c.item_code ?? c.task ?? c.equipment_code) as string).join(", ");
  return String(v);
}

function Library() {
  const params = useSearchParams();
  const [tab, setTab] = useState<Tab>(TABS.find((t) => t.key === params.get("tab")) ?? TABS[0]);
  const [projects, setProjects] = useState<{ id: string; code: string }[]>([]);
  const [scope, setScope] = useState<string>("");
  const [rows, setRows] = useState<Row[]>([]);
  const [form, setForm] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  useEffect(() => { api<{ id: string; code: string }[]>("/projects").then(setProjects).catch(() => undefined); }, []);
  const load = () => api<Row[]>(`${tab.list}${scope ? `?project_id=${scope}` : ""}`).then(setRows).catch((e) => setMsg(e.message));
  useEffect(() => {
    setForm(Object.fromEntries((tab.fields ?? []).map((f) => [f.name, f.default ?? ""])));
    load();
  }, [tab, scope]); // eslint-disable-line react-hooks/exhaustive-deps

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setMsg(null);
    const v = Object.fromEntries(Object.entries(form).filter(([, x]) => x !== ""));
    try {
      await api(tab.add!, { method: "POST", body: JSON.stringify({ ...(tab.toBody ? tab.toBody(v) : v), project_id: scope || null }) });
      setMsg("Saved.");
      load();
    } catch (err) {
      setMsg((err as Error).message);
    }
  }

  const shown = rows.filter((r) => JSON.stringify(r).toLowerCase().includes(filter.toLowerCase()));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-xl font-semibold">Library</h1>
        <label className="text-sm">Scope{" "}
          <select className="field" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="">Master library</option>
            {projects.map((p) => <option key={p.id} value={p.id}>Project {p.code} (own records + master)</option>)}
          </select>
        </label>
      </div>
      <div className="flex flex-wrap gap-1 text-sm">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t)} className={`rounded px-3 py-1 ${tab.key === t.key ? "bg-[var(--accent)] text-white" : "panel"}`}>{t.label}</button>
        ))}
        <input className="field ml-auto w-56" placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      <p className="muted text-xs">
        Records are never edited in place: adding a price or rate for the same code creates a new revision, so approved estimates keep the figures they were priced with.
        {scope ? " New records here belong to this project only." : " New records here go to the master library (admins and cost engineers)."}
      </p>
      {tab.fields && (
        <form onSubmit={add} className="panel flex flex-wrap items-end gap-2 p-4 text-sm">
          {tab.fields.map((f) => (
            <label key={f.name} className="flex flex-col">
              <span className="muted text-xs">{f.label}</span>
              {f.type === "select" ? (
                <select className="field" value={form[f.name] ?? ""} onChange={(e) => setForm({ ...form, [f.name]: e.target.value })}>
                  {f.options!.map((o) => <option key={o}>{o}</option>)}
                </select>
              ) : (
                <input className="field w-40" type={f.type ?? "text"} required={f.required} value={form[f.name] ?? ""}
                  onChange={(e) => setForm({ ...form, [f.name]: e.target.value })} />
              )}
            </label>
          ))}
          <button className="btn">Add</button>
          {msg && <span>{msg}</span>}
        </form>
      )}
      {tab.key === "assemblies" && <AssemblyEditor projectId={scope || null} onSaved={load} />}
      <section className="panel overflow-x-auto">
        <table className="datagrid">
          <thead><tr>{tab.columns.map(([k, l]) => <th key={k}>{l}</th>)}</tr></thead>
          <tbody>
            {shown.map((r, n) => <tr key={n}>{tab.columns.map(([k]) => <td key={k}>{show(r[k], k)}</td>)}</tr>)}
            {shown.length === 0 && <tr><td colSpan={tab.columns.length} className="muted">Nothing here yet.</td></tr>}
          </tbody>
        </table>
      </section>
    </div>
  );
}

export default function LibraryPage() {
  return <Suspense><Library /></Suspense>;
}
