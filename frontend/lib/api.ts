// Same-origin calls to /api/* (proxied to the FastAPI backend). The session is an httpOnly cookie: no tokens or secrets live in JS.
export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

const ORG_KEY = "mp_org";
export const getOrgId = () => (typeof window === "undefined" ? null : window.localStorage.getItem(ORG_KEY));
export const setOrgId = (id: string) => window.localStorage.setItem(ORG_KEY, id);

function messageOf(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => (d?.msg ? `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}` : String(d))).join("; ");
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  return fallback;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { "X-Requested-With": "marketpilot" }; // CSRF guard header required by the backend
  const org = getOrgId();
  if (org) headers["X-Org-Id"] = org;
  if (opts.body !== undefined) headers["Content-Type"] = "application/json";
  const r = await fetch(`/api${path}`, {
    method: opts.method ?? (opts.body !== undefined ? "POST" : "GET"),
    headers,
    credentials: "same-origin",
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (r.status === 204) return undefined as T;
  const data = await r.json().catch(() => null);
  if (!r.ok) throw new ApiError(r.status, messageOf(data?.detail, `Request failed (${r.status})`), data?.detail);
  return data as T;
}

export const money = (v: string | number | null | undefined, currency = "USD") =>
  v === null || v === undefined || v === ""
    ? "—"
    : new Intl.NumberFormat("en-US", { style: "currency", currency }).format(Number(v)); // display only; all math is server-side Decimal

export const when = (iso?: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
export const uid = () => (typeof crypto !== "undefined" && crypto.randomUUID ? crypto.randomUUID() : String(Math.random()).slice(2) + Date.now());
