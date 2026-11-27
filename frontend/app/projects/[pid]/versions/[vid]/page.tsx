"use client";

import { use, useEffect, useState } from "react";
import { api, money, qty } from "@/lib/api";

type Item = { item_key: string; module: string; trade: string; description: string; quantity: string; unit: string; unit_rate: string; amount: string };
type BoqLine = { line_no: number; trade: string; assembly_code: string; description: string; unit: string; quantity: string; unit_rate: string; amount: string };
type Summary = { status: string; totals: Record<string, string> };
type Group = Record<string, string | number>;

const VIEWS = ["items", "boq", "floor", "trade", "module"] as const;

export default function VersionPage({ params }: { params: Promise<{ pid: string; vid: string }> }) {
  const { pid, vid } = use(params);
  const base = `/projects/${pid}/versions/${vid}`;
  const [view, setView] = useState<(typeof VIEWS)[number]>("items");
  const [items, setItems] = useState<Item[]>([]);
  const [boq, setBoq] = useState<BoqLine[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    api<Item[]>(`${base}/items`).then(setItems).catch((e) => setError(e.message));
    api<BoqLine[]>(`${base}/boq`).then(setBoq).catch(() => undefined);
    api<Summary>(`${base}/summary`).then(setSummary).catch(() => undefined);
  };
  useEffect(load, [base]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (view === "floor" || view === "trade" || view === "module") {
      api<Group[]>(`${base}/consolidate?by=${view}`).then(setGroups).catch((e) => setError(e.message));
    }
  }, [view, base]);

  async function approve() {
    setError(null);
    try {
      await api(`${base}/approve`, { method: "POST" });
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const shown = items.filter((i) => `${i.item_key} ${i.description} ${i.module} ${i.trade}`.toLowerCase().includes(filter.toLowerCase()));
  const t = summary?.totals ?? {};

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Detailed estimate <span className="muted text-sm">({summary?.status ?? "…"})</span></h1>
        {summary?.status === "draft" && <button className="btn text-sm" onClick={approve}>Approve version</button>}
      </div>
      {error && <div className="text-sm text-red-500">{error}</div>}
      <section className="panel grid grid-cols-3 gap-4 p-4 text-sm md:grid-cols-6">
        {["material", "labor", "equipment", "direct", "overhead", "total"].map((k) => (
          <div key={k}><div className="muted text-xs capitalize">{k}</div><div className="font-mono">{money(t[k])}</div></div>
        ))}
      </section>
      <div className="flex items-center gap-1 text-sm">
        {VIEWS.map((v) => (
          <button key={v} onClick={() => setView(v)}
            className={`rounded px-3 py-1 ${view === v ? "bg-[var(--accent)] text-white" : "panel"}`}>
            {v === "boq" ? "BOQ" : v === "items" ? "Items" : `By ${v}`}
          </button>
        ))}
        {view === "items" && <input className="field ml-auto w-64" placeholder="Filter items" value={filter} onChange={(e) => setFilter(e.target.value)} />}
      </div>
      <section className="panel overflow-x-auto">
        {view === "items" && (
          <table className="datagrid">
            <thead><tr><th>Item</th><th>Module</th><th>Description</th><th className="num">Quantity</th><th>Unit</th><th className="num">Unit rate</th><th className="num">Amount</th></tr></thead>
            <tbody>{shown.map((i) => (
              <tr key={i.item_key}><td className="font-mono text-xs">{i.item_key}</td><td>{i.module}</td><td>{i.description}</td>
                <td className="num">{qty(i.quantity)}</td><td>{i.unit}</td><td className="num">{money(i.unit_rate)}</td><td className="num">{money(i.amount)}</td></tr>
            ))}</tbody>
          </table>
        )}
        {view === "boq" && (
          <table className="datagrid">
            <thead><tr><th>#</th><th>Trade</th><th>Work item</th><th className="num">Quantity</th><th>Unit</th><th className="num">Unit rate</th><th className="num">Amount</th></tr></thead>
            <tbody>{boq.map((l) => (
              <tr key={l.line_no}><td>{l.line_no}</td><td>{l.trade}</td><td>{l.description} <span className="muted text-xs">{l.assembly_code}</span></td>
                <td className="num">{qty(l.quantity)}</td><td>{l.unit}</td><td className="num">{money(l.unit_rate)}</td><td className="num">{money(l.amount)}</td></tr>
            ))}</tbody>
          </table>
        )}
        {(view === "floor" || view === "trade" || view === "module") && (
          <table className="datagrid">
            <thead><tr><th className="capitalize">{view}</th><th className="num">Items</th><th className="num">Direct cost</th><th className="num">Amount</th></tr></thead>
            <tbody>{groups.map((g, n) => (
              <tr key={n}><td>{String(g[view]) || <span className="muted">(no {view})</span>}</td><td className="num">{g.items}</td>
                <td className="num">{money(String(g.direct))}</td><td className="num">{money(String(g.amount))}</td></tr>
            ))}</tbody>
          </table>
        )}
      </section>
    </div>
  );
}
