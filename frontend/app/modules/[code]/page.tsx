"use client";

import { use, useEffect, useState } from "react";
import FormulaRunner from "@/components/FormulaRunner";
import { api, type FormulaInfo, type ModuleInfo } from "@/lib/api";

export default function ModulePage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const [mod, setMod] = useState<ModuleInfo | null>(null);
  const [formulas, setFormulas] = useState<FormulaInfo[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<ModuleInfo[]>("/modules").then((ms) => setMod(ms.find((m) => m.code === code) ?? null)).catch((e) => setError(e.message));
    api<FormulaInfo[]>("/formulas").then(setFormulas).catch(() => undefined);
  }, [code]);

  if (error) return <div className="text-sm text-red-500">{error}</div>;
  if (!mod) return <div className="muted text-sm">Loading…</div>;

  return (
    <div className="max-w-4xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold">{mod.number}. {mod.name}</h1>
        <p className="muted text-sm">Trade: {mod.trade}</p>
      </div>
      {mod.kind !== "estimating" ? (
        <div className="panel p-4 text-sm">
          This is a {mod.kind} workspace over the shared services ({mod.services.join(", ")}).
          {mod.kind === "reporting" ? " Open an estimate under Projects to see the BOQ and consolidations." : " Records are maintained through the library API; the editing screen is not built yet."}
        </div>
      ) : (
        mod.item_types.map((it) => {
          const f = formulas.find((x) => x.code === it.formula);
          return (
            <section key={it.code} className="panel p-4">
              <div className="mb-2 flex items-baseline justify-between">
                <span className="font-semibold">{it.label}</span>
                <span className="muted text-xs">
                  {it.classification} · formula {it.formula} ·{" "}
                  {it.priced_directly ? "priced from a price-book item" : `assembly ${it.default_assembly}`}
                </span>
              </div>
              {f ? <FormulaRunner formula={f} /> : <div className="muted text-sm">Formula not loaded.</div>}
              <p className="muted mt-2 text-xs">
                Pricing an item needs a project with its assembly and rates in the library.
              </p>
            </section>
          );
        })
      )}
    </div>
  );
}
