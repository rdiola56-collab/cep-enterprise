"use client";

import { useState } from "react";
import { api } from "@/lib/api";

const EXAMPLE = `tile-L03, tiling, floor_tile, length=30 m, width=12 m
beam-L03, reinforced_concrete, beam, length=6 m, width=300 mm, height=600 mm, pieces=24 pcs`;

/** One item per line: key, module, item type, then name=value unit pairs. Each line goes through the same engine. */
export default function BulkEntry({ base, onDone }: { base: string; onDone: () => void }) {
  const [text, setText] = useState("");
  const [log, setLog] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  async function run() {
    setBusy(true);
    const out: string[] = [];
    for (const [n, line] of text.split("\n").entries()) {
      if (!line.trim()) continue;
      const [key, module, itemType, ...pairs] = line.split(/[,\t]/).map((x) => x.trim());
      const inputs: Record<string, { value: string; unit: string }> = {};
      for (const p of pairs) {
        const m = /^(\w+)\s*=\s*([-\d.,]+)\s*(.*)$/.exec(p);
        if (m) inputs[m[1]] = { value: m[2].replace(/,/g, ""), unit: m[3] || "1" };
      }
      try {
        await api(`${base}/items`, { method: "POST", body: JSON.stringify({ item_key: key, module, item_type: itemType, inputs }) });
        out.push(`line ${n + 1}: ${key} saved`);
      } catch (err) {
        out.push(`line ${n + 1}: ${key || "?"} failed: ${(err as Error).message}`);
      }
    }
    setLog(out);
    setBusy(false);
    onDone();
  }

  return (
    <div className="panel space-y-2 p-4 text-sm">
      <div className="font-semibold">Bulk entry</div>
      <p className="muted text-xs">One item per line: key, module, item type, then name=value unit. Paste from Excel works (tabs or commas).</p>
      <textarea className="field h-28 w-full font-mono text-xs" placeholder={EXAMPLE} value={text} onChange={(e) => setText(e.target.value)} />
      <button className="btn" disabled={busy || !text.trim()} onClick={run}>{busy ? "Calculating…" : "Calculate all"}</button>
      {log.length > 0 && <pre className="muted whitespace-pre-wrap text-xs">{log.join("\n")}</pre>}
    </div>
  );
}
