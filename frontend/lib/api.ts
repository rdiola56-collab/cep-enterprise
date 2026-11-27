export type ModuleInfo = {
  number: number;
  code: string;
  name: string;
  trade: string;
  kind: "estimating" | "library" | "reporting";
  status: "configured" | "planned";
  services: string[];
  item_types: {
    code: string;
    label: string;
    formula: string;
    default_assembly: string | null;
    priced_directly: boolean;
    classification: string;
    assembly_available: boolean | null;
  }[];
};

export type FormulaInfo = {
  code: string;
  name: string;
  scope: string;
  version: number;
  expression: string;
  output_unit: string;
  inputs: { name: string; kind: string; unit: string; label: string; default: string | null }[];
};

export type Me = { id: string; email: string; name: string; global_role: string; projects: Record<string, string> };

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
  }
}

const TOKEN_KEY = "cep.token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session lasts for this page only */
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers ?? {}),
    },
  });
  const publicCall = ["/auth/login", "/auth/reset", "/auth/sso"].some((p) => path.startsWith(p));
  if (res.status === 401 && !publicCall) {
    setToken(null);
    if (typeof window !== "undefined" && window.location.pathname !== "/login") window.location.href = "/login";
  }
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`;
    throw new ApiError(detail, res.status);
  }
  return body as T;
}

export function money(v: string | null | undefined): string {
  if (v == null) return "–";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : v;
}

export function qty(v: string | null | undefined): string {
  if (v == null) return "–";
  const n = Number(v);
  return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: 3 }) : v;
}

export async function download(path: string, fallbackName: string) {
  const token = getToken();
  const res = await fetch(`/api${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new ApiError(typeof body.detail === "string" ? body.detail : `Download failed (${res.status})`, res.status);
  }
  const name = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? fallbackName;
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export type WbsFloor = { id: string; code: string; name: string; level: number; is_basement: boolean;
  gross_floor_area_m2: string | null;
  rooms: { id: string; code: string; name: string; zone_id: string | null }[]; zones: { id: string; code: string; name: string }[] };
export type WbsBuilding = { id: string; code: string; name: string; floors: WbsFloor[] };

/** multipart upload (the browser sets the boundary, so no JSON content type) */
export async function upload<T>(path: string, form: FormData): Promise<T> {
  const token = getToken();
  const res = await fetch(`/api${path}`, { method: "POST", body: form, headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (res.status === 401) {
    setToken(null);
    window.location.href = "/login";
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(typeof body.detail === "string" ? body.detail : `Upload failed (${res.status})`, res.status);
  return body as T;
}

export const PUBLIC_PAGES = ["/login", "/login/sso", "/reset-password"];

export type Job = { id: string; kind: string; status: "queued" | "running" | "succeeded" | "failed"; error: string | null;
  result: Record<string, unknown> | null; output_name: string | null; mode: string };

/** Poll a background job until it finishes (the worker writes its status to the database). */
export async function waitForJob(pid: string, job: Job, onUpdate?: (j: Job) => void): Promise<Job> {
  let current = job;
  let delay = 500;
  while (current.status === "queued" || current.status === "running") {
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(delay * 1.5, 4000);
    current = await api<Job>(`/projects/${pid}/jobs/${current.id}`);
    onUpdate?.(current);
  }
  return current;
}
