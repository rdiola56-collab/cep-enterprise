"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type ModuleInfo } from "@/lib/api";

export default function Sidebar() {
  const path = usePathname();
  const [modules, setModules] = useState<ModuleInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<ModuleInfo[]>("/modules").then(setModules).catch((e) => setError(e.message));
  }, []);

  const link = (href: string, label: string, extra?: React.ReactNode) => (
    <Link
      key={href}
      href={href}
      className={`flex items-center justify-between rounded px-2 py-1 text-[13px] ${
        path === href ? "bg-[var(--accent)] text-white" : "hover:bg-[var(--bg)]"
      }`}
    >
      <span className="truncate">{label}</span>
      {extra}
    </Link>
  );

  return (
    <aside className="w-64 shrink-0 border-r border-[var(--line)] bg-[var(--panel)] p-3 overflow-y-auto">
      <div className="mb-4 px-2">
        <div className="text-sm font-semibold tracking-wide">CEP: ENTERPRISE</div>
        <div className="muted text-xs">Universal Estimating Engine</div>
      </div>
      <nav className="space-y-0.5">
        {link("/", "Overview")}
        {link("/formulas", "Formula library")}
        {link("/units", "Unit converter")}
      </nav>
      <div className="muted mt-5 mb-1 px-2 text-[11px] font-semibold uppercase">Estimating modules</div>
      {error && <div className="px-2 text-xs text-red-500">API unavailable: {error}</div>}
      <nav className="space-y-0.5">
        {modules?.map((m) =>
          link(
            `/modules/${m.code}`,
            `${m.number}. ${m.name}`,
            m.status === "planned" ? <span className="muted text-[10px]">planned</span> : null,
          ),
        )}
      </nav>
    </aside>
  );
}
