"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Unit = { symbol: string; name: string; kind: string; system: string };

export default function UnitsPage() {
  const [units, setUnits] = useState<Unit[]>([]);
  const [value, setValue] = useState("1");
  const [from, setFrom] = useState("ft");
  const [to, setTo] = useState("m");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { api<Unit[]>("/units").then(setUnits).catch((e) => setError(e.message)); }, []);
  const kind = units.find((u) => u.symbol === from)?.kind;
  const targets = units.filter((u) => u.kind === kind);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const r = await api<{ value: string }>("/units/convert", {
        method: "POST",
        body: JSON.stringify({ value, from_unit: from, to_unit: to }),
      });
      setResult(r.value);
    } catch (err) {
      setResult(null);
      setError((err as Error).message);
    }
  }

  return (
    <div className="max-w-2xl space-y-4">
      <h1 className="text-xl font-semibold">Unit converter</h1>
      <p className="muted text-sm">Exact metric ↔ imperial conversion, the same conversion every module uses.</p>
      <form onSubmit={run} className="panel flex flex-wrap items-center gap-2 p-4">
        <input className="field w-32 text-right" value={value} onChange={(e) => setValue(e.target.value)} />
        <select className="field" value={from} onChange={(e) => setFrom(e.target.value)}>
          {units.map((u) => <option key={u.symbol} value={u.symbol}>{u.symbol} ({u.name})</option>)}
        </select>
        <span className="muted">to</span>
        <select className="field" value={to} onChange={(e) => setTo(e.target.value)}>
          {targets.map((u) => <option key={u.symbol} value={u.symbol}>{u.symbol} ({u.name})</option>)}
        </select>
        <button className="btn">Convert</button>
        {result !== null && <div className="w-full pt-2 font-mono">= {result} {to}</div>}
        {error && <div className="w-full pt-2 text-sm text-red-500">{error}</div>}
      </form>
    </div>
  );
}
