"use client";

import { useState } from "react";
import { api, type FormulaInfo } from "@/lib/api";

type Result = { value: string; unit: string; methodology: string; formula_version: number };

export default function FormulaRunner({ formula }: { formula: FormulaInfo }) {
  const [vals, setVals] = useState<Record<string, { value: string; unit: string }>>(
    Object.fromEntries(formula.inputs.map((i) => [i.name, { value: i.default ?? "", unit: i.unit }])),
  );
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const inputs = Object.fromEntries(Object.entries(vals).filter(([, v]) => v.value !== ""));
    try {
      setResult(await api<Result>(`/formulas/${formula.code}/evaluate`, { method: "POST", body: JSON.stringify({ inputs }) }));
    } catch (err) {
      setResult(null);
      setError((err as Error).message);
    }
  }

  return (
    <form onSubmit={run} className="space-y-2">
      <div className="grid grid-cols-[1fr_8rem_5rem] items-center gap-2 text-sm">
        {formula.inputs.map((i) => (
          <div key={i.name} className="contents">
            <label className="muted">{i.label || i.name}</label>
            <input className="field text-right" value={vals[i.name].value}
              onChange={(e) => setVals({ ...vals, [i.name]: { ...vals[i.name], value: e.target.value } })} />
            <input className="field" value={vals[i.name].unit}
              onChange={(e) => setVals({ ...vals, [i.name]: { ...vals[i.name], unit: e.target.value } })} />
          </div>
        ))}
      </div>
      <button className="btn text-sm">Calculate</button>
      {result && (
        <div className="text-sm">
          <div className="font-mono font-semibold">{result.value} {result.unit}</div>
          <div className="muted font-mono text-xs">{result.methodology} · v{result.formula_version}</div>
        </div>
      )}
      {error && <div className="text-sm text-red-500">{error}</div>}
    </form>
  );
}
