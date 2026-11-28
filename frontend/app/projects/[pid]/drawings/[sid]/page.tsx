"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, blobUrl, qty, type Me, type Sheet, type SheetPage, type TakeoffMark, type WbsBuilding } from "@/lib/api";

type Tool = "select" | "length" | "area" | "count" | "calibrate";
type Pt = [number, number];
type Version = { code: string; name: string; version_id: string; version_no: number; status: string };

const PRESETS: { label: string; paper: string; real: string }[] = [
  { label: '1/16" = 1\'-0"', paper: "1/16 in", real: "1 ft" },
  { label: '1/8" = 1\'-0"', paper: "1/8 in", real: "1 ft" },
  { label: '3/16" = 1\'-0"', paper: "3/16 in", real: "1 ft" },
  { label: '1/4" = 1\'-0"', paper: "1/4 in", real: "1 ft" },
  { label: '3/8" = 1\'-0"', paper: "3/8 in", real: "1 ft" },
  { label: '1/2" = 1\'-0"', paper: "1/2 in", real: "1 ft" },
  { label: '1" = 10\'', paper: "1 in", real: "10 ft" },
  { label: '1" = 20\'', paper: "1 in", real: "20 ft" },
  { label: "1:20", paper: "1 mm", real: "0.02 m" },
  { label: "1:50", paper: "1 mm", real: "0.05 m" },
  { label: "1:100", paper: "1 mm", real: "0.1 m" },
  { label: "1:200", paper: "1 mm", real: "0.2 m" },
  { label: "1:500", paper: "1 mm", real: "0.5 m" },
];
const SNAP_PX = 8;

function hue(text: string) {
  let h = 0;
  for (const c of text) h = (h * 31 + c.charCodeAt(0)) % 360;
  return h;
}

function dist(a: Pt, b: Pt) { return Math.hypot(b[0] - a[0], b[1] - a[1]); }

/** Grid index of the page's vector endpoints, so the nearest one is found quickly while the pointer moves. */
function snapIndex(points: Pt[], cell = 20) {
  const grid = new Map<string, Pt[]>();
  for (const p of points) {
    const k = `${Math.floor(p[0] / cell)}:${Math.floor(p[1] / cell)}`;
    (grid.get(k) ?? grid.set(k, []).get(k)!).push(p);
  }
  return (q: Pt, radius: number): Pt | null => {
    let best: Pt | null = null;
    let bd = radius;
    const r = Math.ceil(radius / cell);
    const cx = Math.floor(q[0] / cell), cy = Math.floor(q[1] / cell);
    for (let i = -r; i <= r; i++) for (let j = -r; j <= r; j++) {
      for (const p of grid.get(`${cx + i}:${cy + j}`) ?? []) {
        const d = dist(p, q);
        if (d <= bd) { bd = d; best = p; }
      }
    }
    return best;
  };
}

export default function SheetPageView({ params }: { params: Promise<{ pid: string; sid: string }> }) {
  const { pid, sid } = use(params);
  const base = `/projects/${pid}/sheets/${sid}`;
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const [pageNo, setPageNo] = useState(1);
  const [zoom, setZoom] = useState(0.75);
  const [img, setImg] = useState<string | null>(null);
  const [snaps, setSnaps] = useState<Pt[]>([]);
  const [snapOn, setSnapOn] = useState(true);
  const [tool, setTool] = useState<Tool>("select");
  const [draft, setDraft] = useState<Pt[]>([]);
  const [hover, setHover] = useState<{ p: Pt; snapped: boolean } | null>(null);
  const [subject, setSubject] = useState("");
  const [label, setLabel] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [calLength, setCalLength] = useState("");
  const [preset, setPreset] = useState("");
  const [stated, setStated] = useState({ paper: "", real: "" });
  const [versions, setVersions] = useState<Version[]>([]);
  const [target, setTarget] = useState({ version_id: "", building_id: "" });
  const [wbs, setWbs] = useState<WbsBuilding[]>([]);
  const [me, setMe] = useState<Me | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => { api<Me>("/auth/me").then(setMe).catch(() => undefined); }, []);
  const role = me?.projects[pid];
  const canEdit = me?.global_role === "admin" || role === "manager" || role === "estimator";

  const load = useCallback(() => api<Sheet>(base).then(setSheet).catch((e) => setError(e.message)), [base]);
  useEffect(() => {
    load();
    api<Version[]>(`/projects/${pid}/estimates`).then((vs) => {
      const drafts = vs.filter((v) => v.status === "draft");
      setVersions(drafts);
      if (drafts[0]) setTarget((t) => ({ ...t, version_id: t.version_id || drafts[0].version_id }));
    }).catch(() => undefined);
    api<WbsBuilding[]>(`/projects/${pid}/wbs`).then(setWbs).catch(() => undefined);
  }, [load, pid]);

  const page: SheetPage | undefined = sheet?.pages?.find((p) => p.page_no === pageNo);
  const w = page ? Number(page.width_pt) : 0;
  const h = page ? Number(page.height_pt) : 0;
  const renderScale = zoom <= 0.75 ? 1 : zoom <= 1.5 ? 2 : 4;

  useEffect(() => {
    if (!page) return;
    let url: string | null = null;
    let live = true;
    blobUrl(`${base}/pages/${page.page_no}/image?scale=${renderScale}`).then((u) => { url = u; if (live) setImg(u); else URL.revokeObjectURL(u); })
      .catch((e) => setError(e.message));
    return () => { live = false; if (url) URL.revokeObjectURL(url); };
  }, [base, page?.page_no, renderScale]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!page) return;
    api<{ points: Pt[] }>(`${base}/pages/${page.page_no}/snaps`).then((s) => setSnaps(s.points)).catch(() => setSnaps([]));
  }, [base, page?.page_no]); // eslint-disable-line react-hooks/exhaustive-deps

  const nearest = useMemo(() => snapIndex(snaps), [snaps]);
  const marks = useMemo(() => (Array.isArray(sheet?.marks) ? sheet!.marks : []), [sheet]);
  const pageMarks = marks.filter((m) => m.page_no === pageNo);
  const subjects = useMemo(() => Array.from(new Set(marks.map((m) => m.subject))).sort(), [marks]);

  function toPoint(e: React.MouseEvent): { p: Pt; snapped: boolean } {
    const svg = svgRef.current!;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const loc = pt.matrixTransform(svg.getScreenCTM()!.inverse());
    let p: Pt = [Math.min(Math.max(loc.x, 0), w), Math.min(Math.max(loc.y, 0), h)];
    if (e.shiftKey && draft.length) {  // orthogonal from the last point
      const last = draft[draft.length - 1];
      p = Math.abs(p[0] - last[0]) > Math.abs(p[1] - last[1]) ? [p[0], last[1]] : [last[0], p[1]];
      return { p, snapped: false };
    }
    if (snapOn) {
      const s = nearest(p, SNAP_PX / zoom);
      if (s) return { p: s, snapped: true };
    }
    return { p, snapped: false };
  }

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    try { await fn(); } catch (e) { setError((e as Error).message); }
  }

  const finish = useCallback(async (raw: Pt[] = draft) => {
    if (tool === "calibrate") return;
    // a double-click also lands two clicks on the same spot: drop repeated points
    const points = raw.filter((p, i) => i === 0 || dist(p, raw[i - 1]) > 0.5 || tool === "count");
    if (!points.length) return;
    await run(async () => {
      if (!subject.trim()) throw new Error("Type a subject first (what you are measuring, e.g. Floor Tile)");
      await api(`${base}/marks`, { method: "POST", body: JSON.stringify({ page_no: pageNo, kind: tool, subject, label, points }) });
      setDraft([]);
      await load();
    });
  }, [draft, tool, subject, label, base, pageNo, load]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    function key(e: KeyboardEvent) {
      if (["INPUT", "SELECT", "TEXTAREA"].includes((e.target as HTMLElement)?.tagName)) return;
      if (e.key === "Escape") setDraft([]);
      if (e.key === "Enter") finish();
      if (e.key === "Backspace") setDraft((d) => d.slice(0, -1));
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [finish]);

  function click(e: React.MouseEvent) {
    if (!canEdit || tool === "select") return;
    const { p } = toPoint(e);
    if (tool === "calibrate") {
      setDraft((d) => (d.length >= 2 ? [p] : [...d, p]));
      return;
    }
    setDraft((d) => [...d, p]);
  }

  async function saveCalibration() {
    if (draft.length !== 2) return setError("Click both ends of a dimension you know first.");
    await run(async () => {
      await api(`${base}/pages/${pageNo}/scale`, { method: "PUT", body: JSON.stringify({ method: "measured", p1: draft[0], p2: draft[1], length: calLength }) });
      setDraft([]);
      setTool("select");
      await load();
    });
  }

  async function saveStated() {
    const chosen = PRESETS.find((p) => p.label === preset);
    const body = chosen ? { paper: chosen.paper, real: chosen.real } : stated;
    await run(async () => {
      await api(`${base}/pages/${pageNo}/scale`, { method: "PUT", body: JSON.stringify({ method: "stated", ...body }) });
      await load();
    });
  }

  async function send() {
    await run(async () => {
      const b = await api<{ id: string }>(`${base}/send`, { method: "POST", body: JSON.stringify({
        version_id: target.version_id, building_id: target.building_id || null }) });
      window.location.href = `/projects/${pid}/versions/${target.version_id}/import?batch=${b.id}`;
    });
  }

  // Preview while drawing (the saved quantity is computed exactly by the server).
  const k = page?.scale ? Number(page.scale.units_per_point) : null;
  const live = hover && tool !== "select" ? [...draft, hover.p] : draft;
  const previewLen = live.slice(1).reduce((a, p, i) => a + dist(live[i], p), 0);
  const previewArea = live.length > 2 ? Math.abs(live.reduce((a, p, i) => { const q = live[(i + 1) % live.length]; return a + p[0] * q[1] - q[0] * p[1]; }, 0)) / 2 : 0;
  const preview = tool === "count" ? `${draft.length} pcs`
    : tool === "calibrate" ? (draft.length === 2 ? `${qty(String(dist(draft[0], draft[1])))} pt picked` : "click both ends of a known dimension")
    : !k ? "set the page scale to see quantities"
    : tool === "length" ? `${qty(String(previewLen * k))} ${page!.scale!.unit}`
    : tool === "area" ? `${qty(String(previewArea * k * k))} ${page!.scale!.unit}²` : "";

  const totals = useMemo(() => {
    const t = new Map<string, Map<string, number>>();
    for (const m of marks) {
      const main = m.kind === "area" ? m.measures.area : m.kind === "length" ? m.measures.length : m.measures.count;
      if (!main) continue;
      const bySubject = t.get(m.subject) ?? t.set(m.subject, new Map()).get(m.subject)!;
      bySubject.set(main.unit, (bySubject.get(main.unit) ?? 0) + Number(main.value));
    }
    return t;
  }, [marks]);

  if (!sheet || !page) return <div className="muted">{error ?? "Loading…"}</div>;
  const sel = marks.find((m) => m.id === selected);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h1 className="mr-auto text-xl font-semibold">{sheet.set_name} <span className="muted text-base">rev. {sheet.revision} · {sheet.file_name}</span></h1>
        <Link className="panel px-3 py-1 text-sm" href={`/projects/${pid}/drawings`}>All drawings</Link>
      </div>
      {error && <div className="text-sm text-red-500">{error}</div>}
      <div className="flex flex-col gap-3 lg:flex-row">
        <div className="panel relative min-w-0 flex-1 overflow-auto" style={{ maxHeight: "78vh" }}>
          <div className="sticky left-0 top-0 z-10 flex flex-wrap items-center gap-1 border-b border-[var(--line)] bg-[var(--panel)] p-2 text-xs">
            {(["select", "length", "area", "count"] as Tool[]).map((t) => (
              <button key={t} disabled={!canEdit && t !== "select"} className={`rounded px-2 py-1 ${tool === t ? "btn" : "panel"}`}
                onClick={() => { setTool(t); setDraft([]); }}>{t === "select" ? "Select" : t === "length" ? "Length" : t === "area" ? "Area" : "Count"}</button>
            ))}
            <span className="mx-2 font-medium">{preview}</span>
            {draft.length > 0 && tool !== "calibrate" && <>
              <button className="btn px-2 py-1" onClick={() => finish()}>Finish (Enter)</button>
              <button className="underline" onClick={() => setDraft([])}>Cancel (Esc)</button>
            </>}
            <span className="ml-auto flex items-center gap-2">
              <label className="flex items-center gap-1"><input type="checkbox" checked={snapOn} onChange={(e) => setSnapOn(e.target.checked)} />snap to lines</label>
              <button className="panel px-2" onClick={() => setZoom((z) => Math.max(0.25, z / 1.25))}>−</button>
              <span>{Math.round(zoom * 100)}%</span>
              <button className="panel px-2" onClick={() => setZoom((z) => Math.min(4, z * 1.25))}>+</button>
            </span>
          </div>
          <div style={{ width: w * zoom, height: h * zoom, position: "relative" }}>
            {img && <img src={img} alt={`page ${pageNo}`} draggable={false} style={{ width: w * zoom, height: h * zoom, display: "block", background: "white" }} />}
            <svg ref={svgRef} viewBox={`0 0 ${w} ${h}`} width={w * zoom} height={h * zoom}
              style={{ position: "absolute", inset: 0, cursor: tool === "select" ? "default" : "crosshair" }}
              onClick={click} onDoubleClick={() => tool !== "count" && finish()} onMouseMove={(e) => setHover(toPoint(e))} onMouseLeave={() => setHover(null)}>
              {pageMarks.map((m) => {
                const pts = m.points.map(([x, y]) => `${x},${y}`).join(" ");
                const c = `hsl(${hue(m.subject)} 80% 42%)`;
                const on = m.id === selected;
                const sw = (on ? 3 : 2) / zoom;
                return (
                  <g key={m.id} onClick={(e) => { if (tool === "select") { e.stopPropagation(); setSelected(m.id); } }} style={{ cursor: "pointer" }}>
                    {m.kind === "area" && <polygon points={pts} fill={c} fillOpacity={on ? 0.35 : 0.2} stroke={c} strokeWidth={sw} />}
                    {m.kind === "length" && <polyline points={pts} fill="none" stroke={c} strokeWidth={sw * 1.5} />}
                    {m.kind === "count" && m.points.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={6 / zoom} fill={c} fillOpacity={0.8} />)}
                    <text x={m.points[0][0]} y={Number(m.points[0][1]) - 4 / zoom} fontSize={11 / zoom} fill={c}>{m.ref}</text>
                  </g>
                );
              })}
              {live.length > 0 && tool !== "select" && (tool === "count"
                ? draft.map(([x, y], i) => <circle key={i} cx={x} cy={y} r={6 / zoom} fill="#2563eb" fillOpacity={0.8} />)
                : tool === "area" && live.length > 2
                  ? <polygon points={live.map((p) => p.join(",")).join(" ")} fill="#2563eb" fillOpacity={0.15} stroke="#2563eb" strokeWidth={2 / zoom} strokeDasharray={`${6 / zoom} ${3 / zoom}`} />
                  : <polyline points={live.map((p) => p.join(",")).join(" ")} fill="none" stroke={tool === "calibrate" ? "#dc2626" : "#2563eb"} strokeWidth={2 / zoom} strokeDasharray={`${6 / zoom} ${3 / zoom}`} />)}
              {hover && tool !== "select" && (
                <circle cx={hover.p[0]} cy={hover.p[1]} r={(hover.snapped ? 5 : 3) / zoom} fill="none" stroke={hover.snapped ? "#16a34a" : "#2563eb"} strokeWidth={1.5 / zoom} />
              )}
            </svg>
          </div>
        </div>

        <aside className="w-full space-y-3 text-sm lg:w-96">
          <section className="panel space-y-2 p-3">
            <div className="flex items-center gap-2">
              <span className="font-medium">Page</span>
              <select className="field" value={pageNo} onChange={(e) => { setPageNo(Number(e.target.value)); setDraft([]); setSelected(null); }}>
                {sheet.pages!.map((p) => <option key={p.page_no} value={p.page_no}>{p.page_no}{p.label ? ` · ${p.label}` : ""}</option>)}
              </select>
              <span className="muted text-xs">{sheet.page_count} page(s)</span>
            </div>
            <label className="flex items-center gap-2"><span className="muted w-24 text-xs">Floor / label</span>
              <input key={`label-${pageNo}`} className="field flex-1" defaultValue={page.label} disabled={!canEdit} placeholder="e.g. L02 (matched to a WBS floor)"
                onBlur={(e) => e.target.value !== page.label && run(async () => { await api(`${base}/pages/${pageNo}`, { method: "PATCH", body: JSON.stringify({ label: e.target.value }) }); await load(); })} />
            </label>
          </section>

          <section className="panel space-y-2 p-3">
            <div className="font-medium">Scale</div>
            {page.scale
              ? <div className="text-xs"><span className="text-green-600">Set:</span> {page.scale.note} <span className="muted">(quantities in {page.scale.unit}; by {page.scale.set_by})</span></div>
              : <div className="text-xs text-amber-600">No scale yet. Lengths and areas on this page cannot be measured until it is set.</div>}
            {canEdit && <>
              <div className="flex flex-wrap items-center gap-1">
                <select className="field text-xs" value={preset} onChange={(e) => setPreset(e.target.value)}>
                  <option value="">Drawing scale…</option>
                  {PRESETS.map((p) => <option key={p.label}>{p.label}</option>)}
                  <option value="custom">Other (type it)</option>
                </select>
                {preset === "custom" && <>
                  <input className="field w-20 text-xs" placeholder={'1/4"'} value={stated.paper} onChange={(e) => setStated({ ...stated, paper: e.target.value })} />=
                  <input className="field w-20 text-xs" placeholder={"1'-0\""} value={stated.real} onChange={(e) => setStated({ ...stated, real: e.target.value })} />
                </>}
                <button className="btn px-2 py-1 text-xs" disabled={!preset} onClick={saveStated}>Use</button>
              </div>
              <div className="flex flex-wrap items-center gap-1">
                <button className={`rounded px-2 py-1 text-xs ${tool === "calibrate" ? "btn" : "panel"}`} onClick={() => { setTool("calibrate"); setDraft([]); }}>
                  Calibrate from a known dimension</button>
                {tool === "calibrate" && <>
                  <input className="field w-24 text-xs" placeholder="e.g. 24'-0&quot;" value={calLength} onChange={(e) => setCalLength(e.target.value)} />
                  <button className="btn px-2 py-1 text-xs" disabled={draft.length !== 2 || !calLength} onClick={saveCalibration}>Set</button>
                </>}
              </div>
              <p className="muted text-xs">Check a printed scale against a known dimension: PDFs are often plotted at a different size than drawn.</p>
            </>}
          </section>

          {canEdit && (
            <section className="panel space-y-2 p-3">
              <div className="font-medium">New marks</div>
              <label className="flex items-center gap-2"><span className="muted w-16 text-xs">Subject</span>
                <input className="field flex-1" list="subjects" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Floor Tile, Partition A, Floor drain" /></label>
              <datalist id="subjects">{subjects.map((s) => <option key={s} value={s} />)}</datalist>
              <label className="flex items-center gap-2"><span className="muted w-16 text-xs">Label</span>
                <input className="field flex-1" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="optional: room, type mark" /></label>
              <p className="muted text-xs">Click to add points, double-click or Enter to finish, Backspace removes the last point, Shift keeps it straight. Rules match the subject.</p>
            </section>
          )}

          <section className="panel p-3">
            <div className="mb-1 font-medium">Marks on this page</div>
            <table className="datagrid text-xs">
              <thead><tr><th>Ref</th><th>Subject</th><th>Label</th><th className="num">Quantity</th></tr></thead>
              <tbody>
                {pageMarks.length === 0 && <tr><td colSpan={4} className="muted">None yet.</td></tr>}
                {pageMarks.map((m) => {
                  const main = m.kind === "area" ? m.measures.area : m.kind === "length" ? m.measures.length : m.measures.count;
                  return (
                    <tr key={m.id} className={m.id === selected ? "bg-[var(--line)]" : ""} onClick={() => setSelected(m.id)} style={{ cursor: "pointer" }}>
                      <td>{m.ref}</td><td>{m.subject}</td><td>{m.label}</td>
                      <td className="num">{main ? `${qty(main.value)} ${main.unit}` : <span className="text-amber-600">{m.problems[0]}</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {sel && canEdit && (
              <div className="mt-2 flex flex-wrap items-center gap-1 text-xs">
                <span className="font-medium">{sel.ref}</span>
                <input key={`s-${sel.id}`} className="field w-28" defaultValue={sel.subject} id="edit-subject" />
                <input key={`l-${sel.id}`} className="field w-24" defaultValue={sel.label} id="edit-label" />
                <button className="btn px-2 py-1" onClick={() => run(async () => {
                  await api(`${base}/marks/${sel.id}`, { method: "PATCH", body: JSON.stringify({
                    subject: (document.getElementById("edit-subject") as HTMLInputElement).value,
                    label: (document.getElementById("edit-label") as HTMLInputElement).value }) });
                  await load();
                })}>Save</button>
                <button className="text-red-500 underline" onClick={() => run(async () => { await api(`${base}/marks/${sel.id}`, { method: "DELETE" }); setSelected(null); await load(); })}>Delete</button>
              </div>
            )}
          </section>

          <section className="panel p-3">
            <div className="mb-1 font-medium">Totals for the whole set</div>
            <table className="datagrid text-xs">
              <tbody>
                {totals.size === 0 && <tr><td className="muted">Nothing measured yet.</td></tr>}
                {Array.from(totals).map(([s, units]) => (
                  <tr key={s}><td>{s}</td><td className="num">{Array.from(units).map(([u, v]) => `${qty(String(v))} ${u}`).join(" + ")}</td></tr>
                ))}
              </tbody>
            </table>
          </section>

          {canEdit && (
            <section className="panel space-y-2 p-3">
              <div className="font-medium">Send to an estimate</div>
              {versions.length === 0 ? <div className="muted text-xs">No draft estimate version in this project.</div> : <>
                <select className="field w-full" value={target.version_id} onChange={(e) => setTarget({ ...target, version_id: e.target.value })}>
                  {versions.map((v) => <option key={v.version_id} value={v.version_id}>{v.code} · {v.name} v{v.version_no}</option>)}
                </select>
                <select className="field w-full" value={target.building_id} onChange={(e) => setTarget({ ...target, building_id: e.target.value })}>
                  <option value="">Building: (any)</option>{wbs.map((b) => <option key={b.id} value={b.id}>{b.code} {b.name}</option>)}
                </select>
                <button className="btn w-full" disabled={!marks.length} onClick={send}>Review import of {marks.length} mark(s)</button>
                <p className="muted text-xs">Opens the import review: rows are mapped by your PDF takeoff rules and nothing reaches the estimate until you commit.</p>
              </>}
            </section>
          )}
        </aside>
      </div>
    </div>
  );
}
