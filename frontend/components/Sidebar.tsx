"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, setToken, type Me, type ModuleInfo } from "@/lib/api";

export default function Sidebar() {
  const path = usePathname();
  const [modules, setModules] = useState<ModuleInfo[] | null>(null);
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (path === "/login") return;
    api<Me>("/auth/me").then(setMe).catch(() => undefined);
    api<ModuleInfo[]>("/modules").then(setModules).catch((e) => setError(e.message));
  }, [path]);

  if (path === "/login") return null;

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
    <aside className="flex w-64 shrink-0 flex-col border-r border-[var(--line)] bg-[var(--panel)] p-3">
      <div className="mb-4 px-2">
        <div className="text-sm font-semibold tracking-wide">CEP: ENTERPRISE</div>
        <div className="muted text-xs">Universal Estimating Engine</div>
      </div>
      <nav className="space-y-0.5">
        {link("/", "Overview")}
        {link("/projects", "Projects")}
        {link("/formulas", "Formula library")}
        {link("/units", "Unit converter")}
      </nav>
      <div className="muted mt-5 mb-1 px-2 text-[11px] font-semibold uppercase">Modules</div>
      {error && <div className="px-2 text-xs text-red-500">{error}</div>}
      <nav className="flex-1 space-y-0.5 overflow-y-auto">
        {modules?.map((m) =>
          link(`/modules/${m.code}`, `${m.number}. ${m.name}`,
            m.kind !== "estimating" ? <span className="muted text-[10px]">{m.kind}</span> : null),
        )}
      </nav>
      {me && (
        <div className="mt-3 border-t border-[var(--line)] px-2 pt-3 text-xs">
          <div className="truncate font-medium">{me.name}</div>
          <div className="muted truncate">{me.email} · {me.global_role.replace("_", " ")}</div>
          <button className="mt-2 underline" onClick={() => { setToken(null); window.location.href = "/login"; }}>
            Sign out
          </button>
        </div>
      )}
    </aside>
  );
}
