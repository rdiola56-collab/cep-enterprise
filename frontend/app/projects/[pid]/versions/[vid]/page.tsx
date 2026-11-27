"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import BulkEntry from "@/components/BulkEntry";
import ItemEditor, { type EditableItem } from "@/components/ItemEditor";
import { api, download, money, qty } from "@/lib/api";

type Item = EditableItem & { trade: string; quantity: string; unit: string; unit_rate: string; amount: string };
type BoqLine = { line_no: number; trade: string; assembly_code: string; description: string; unit: string; quantity: string; unit_rate: string; amount: string };
type Summary = { status: string; totals: Record<string, string> };
type Group = Record<string, string | number>;
type SortKey = "item_key" | "module" | "description" | "quantity" | "amount";

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
  const [sort, setSort] = useState<{ key: SortKey; desc: boolean }>({ key: "item_key", desc: false });
  const [editing, setEditing] = useState<Item | null>(null);
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
  }, [view, base, items]);

  const locked = summary !== null && summary.status !== "draft";

  async function act(fn: () => Promise<unknown>) {
    setError(null);
    try {
      await fn();
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  const remove = (key: string) =>
    act(() => api(`${base}/items/${encodeURIComponent(key)}`, { method: "DELETE" }));

  const shown = useMemo(() => {
    const f = filter.toLowerCase();
    const rows = items.filter((i) => `${i.item_key} ${i.description} ${i.module} ${i.trade}`.toLowerCase().includes(f));
    const num = sort.key === "quantity" || sort.key === "amount";
    rows.sort((a, b) => {
      const r = num ? Number(a[sort.key]) - Number(b[sort.key]) : String(a[sort.key]).localeCompare(String(b[sort.key]));
      return sort.desc ? -r : r;
    });
    return rows;
  }, [items, filter, sort]);

  const th = (key: SortKey, label: string, num = false) => (
    <th className={`cursor-pointer select-none ${num ? "num" : ""}`}
      onClick={() => setSort({ key, desc: sort.key === key ? !sort.desc : false })}>
      {label}{sort.key === key ? (sort.desc ? " ▾" : " ▴") : ""}
    </th>
  );

  function onRowKey(e: React.KeyboardEvent<HTMLTableRowElement>, i: Item) {
    const row = e.currentTarget;
    if (e.key === "ArrowDown") (row.nextElementSibling as HTMLElement | null)?.focus();
    else if (e.key === "ArrowUp") (row.previousElementSibling as HTMLElement | null)?.focus();
    else if (e.key === "Enter" && !locked) setEditing(i);
    else if (e.key === "Delete" && !locked) remove(i.item_key);
    else return;
    e.preventDefault();
  }

  const t = summary?.totals ?? {};

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-xl font-semibold">
          Detailed estimate <span className="muted text-sm">({summary?.status ?? "…"})</span>
        </h1>
        {!locked && <Link className="panel px-3 py-1 text-sm" href={`${base}/import`}>Import drawings / BIM</Link>}
        <button className="panel px-3 py-1 text-sm" onClick={() => act(() => download(`${base}/export.xlsx`, "estimate.xlsx"))}>Export Excel</button>
        <button className="panel px-3 py-1 text-sm" onClick={() => act(() => download(`${base}/export.pdf`, "estimate.pdf"))}>Export PDF</button>
        {summary?.status === "draft" && <button className="btn text-sm" onClick={() => act(() => api(`${base}/approve`, { method: "POST" }))}>Approve version</button>}
      </div>
      {error && <div className="text-sm text-red-500">{error}</div>}
      {locked && <div className="panel p-3 text-sm">This version is {summary?.status} and locked. Create a revision to change it.</div>}
      <section className="panel grid grid-cols-3 gap-4 p-4 text-sm md:grid-cols-6">
        {["material", "labor", "equipment", "direct", "overhead", "total"].map((k) => (
          <div key={k}><div className="muted text-xs capitalize">{k}</div><div className="font-mono">{money(t[k])}</div></div>
        ))}
      </section>
      <ItemEditor pid={pid} base={base} editing={editing} locked={locked}
        onSaved={() => { setEditing(null); load(); }} onCancel={() => setEditing(null)} />
      <div className="flex items-center gap-1 text-sm">
        {VIEWS.map((v) => (
          <button key={v} onClick={() => setView(v)}
            className={`rounded px-3 py-1 ${view === v ? "bg-[var(--accent)] text-white" : "panel"}`}>
            {v === "boq" ? "BOQ" : v === "items" ? `Items (${items.length})` : `By ${v}`}
          </button>
        ))}
        {view === "items" && <input className="field ml-auto w-64" placeholder="Filter items" value={filter} onChange={(e) => setFilter(e.target.value)} />}
      </div>
      <section className="panel overflow-x-auto">
        {view === "items" && (
          <table className="datagrid">
            <thead><tr>{th("item_key", "Item")}{th("module", "Module")}{th("description", "Description")}{th("quantity", "Quantity", true)}<th>Unit</th><th className="num">Unit rate</th>{th("amount", "Amount", true)}<th /></tr></thead>
            <tbody>{shown.map((i) => (
              <tr key={i.item_key} tabIndex={0} onKeyDown={(e) => onRowKey(e, i)} onDoubleClick={() => !locked && setEditing(i)}
                className={`outline-none focus:bg-[var(--bg)] ${editing?.item_key === i.item_key ? "bg-[var(--bg)]" : ""}`}>
                <td className="font-mono text-xs">{i.item_key}</td><td>{i.module}</td><td>{i.description}</td>
                <td className="num">{qty(i.quantity)}</td><td>{i.unit}</td><td className="num">{money(i.unit_rate)}</td><td className="num">{money(i.amount)}</td>
                <td className="whitespace-nowrap text-xs">{!locked && <>
                  <button className="underline" onClick={() => setEditing(i)}>Edit</button>{" "}
                  <button className="text-red-500 underline" onClick={() => remove(i.item_key)}>Delete</button></>}
                </td></tr>
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
      <p className="muted text-xs">Keyboard: ↑/↓ move between rows, Enter edits, Delete removes. Double-click a row to edit.</p>
      {!locked && <BulkEntry base={base} onDone={load} />}
    </div>
  );
}
