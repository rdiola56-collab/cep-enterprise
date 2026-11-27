export type ModuleInfo = {
  number: number;
  code: string;
  name: string;
  trade: string;
  status: "configured" | "planned";
  item_types: { code: string; label: string; formula: string; default_assembly: string; classification: string }[];
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

export class ApiError extends Error {}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(typeof body.detail === "string" ? body.detail : `Request failed (${res.status})`);
  return body as T;
}
