"use client";

import { useEffect, useState } from "react";
import FormulaRunner from "@/components/FormulaRunner";
import { api, type FormulaInfo } from "@/lib/api";

export default function FormulasPage() {
  const [formulas, setFormulas] = useState<FormulaInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<FormulaInfo[]>("/formulas").then(setFormulas).catch((e) => setError(e.message)); }, []);

  return (
    <div className="max-w-4xl space-y-4">
      <h1 className="text-xl font-semibold">Formula library</h1>
      <p className="muted text-sm">
        Shared, versioned formulas. Inputs are unit-checked; formulas run in a controlled parser, never as code.
      </p>
      {error && <div className="text-sm text-red-500">{error}</div>}
      {formulas.map((f) => (
        <section key={f.code} className="panel p-4">
          <div className="mb-2 flex items-baseline justify-between">
            <div>
              <span className="font-semibold">{f.name}</span>{" "}
              <span className="muted font-mono text-xs">{f.code} v{f.version} · {f.scope}</span>
            </div>
            <span className="muted font-mono text-xs">{f.expression} → {f.output_unit}</span>
          </div>
          <FormulaRunner formula={f} />
        </section>
      ))}
    </div>
  );
}
