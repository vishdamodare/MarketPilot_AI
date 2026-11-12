import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }), usePathname: () => "/settings", useSearchParams: () => new URLSearchParams() }));
import Integrations from "@/app/(app)/integrations/page";
import Settings from "@/app/(app)/settings/page";
import { SessionProvider } from "@/lib/session";

const calls: string[] = [];
let keys: any[] = [];
const ME = { user: { id: "u", name: "Ada", email: "a@example.com" }, memberships: [{ org_id: "o1", org_name: "Mkt", mode: "sandbox", currency: "USD", role: "admin", seller_id: null, permissions: ["dashboard", "settings.manage", "integrations.manage", "reconcile.run", "notifications.read"] }] };

function setup(routes: Record<string, any> = {}) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const key = `${init.method ?? "GET"} ${String(url).replace(/^\/api/, "")}`;
    calls.push(key);
    const table: Record<string, any> = {
      "GET /auth/me": ME, "GET /notifications": [], "GET /org": { name: "Mkt", currency: "USD", default_commission_pct: "10.00" },
      "GET /settings/api-keys": () => keys,
      "POST /settings/api-keys": () => { const k = { id: "k1", name: "Shop", prefix: "mpk_abcd", last_used_at: null, revoked_at: null }; keys = [k]; return { ...k, key: "mpk_abcdSECRETSECRETSECRET" }; },
      "DELETE /settings/api-keys/k1": () => { keys = [{ ...keys[0], revoked_at: "2026-11-10T10:00:00Z" }]; return keys[0]; },
      ...routes,
    };
    const v = table[key];
    if (v === undefined) return new Response(JSON.stringify({ detail: `unmocked ${key}` }), { status: 404 });
    return new Response(JSON.stringify(typeof v === "function" ? v() : v), { status: 200 });
  }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (ui: React.ReactElement) => render(<QueryClientProvider client={qc}><SessionProvider>{ui}</SessionProvider></QueryClientProvider>);
}
afterEach(() => { cleanup(); vi.unstubAllGlobals(); calls.length = 0; keys = []; window.localStorage.clear(); });

describe("storefront API keys", () => {
  it("creates a key, shows the secret once with a curl example, then revokes it", async () => {
    const u = userEvent.setup();
    setup()(<Settings />);
    await u.click(await screen.findByRole("tab", { name: "Developers" }));
    expect(await screen.findByText("No API keys yet.")).toBeInTheDocument();
    await u.type(screen.getByLabelText("Key name"), "Shop");
    await u.click(screen.getByRole("button", { name: "Create key" }));
    expect(await screen.findByTestId("new-key")).toHaveTextContent("mpk_abcdSECRETSECRETSECRET");
    expect(screen.getByText(/will not be shown again/)).toBeInTheDocument();
    expect(screen.getByText(/\/api\/store\/v1\/orders/)).toBeInTheDocument();
    expect(await screen.findByText("mpk_abcd…")).toBeInTheDocument(); // the list only ever shows the prefix
    await u.click(screen.getByRole("button", { name: "Revoke" }));
    expect(await screen.findByText("revoked")).toBeInTheDocument();
    expect(calls).toContain("DELETE /settings/api-keys/k1");
  });
});

describe("background sync card", () => {
  it("shows scheduler state and the last result, and runs a sync on demand", async () => {
    const u = userEvent.setup();
    setup({
      "GET /integrations/paypal/status": { org_mode: "sandbox", configured: true, connected: true, environment: "sandbox", base_url: "https://api-m.sandbox.paypal.com", last_sync_at: null, last_error: null, webhook: { webhook_id_configured: true, last_event_at: null, url_path: "/api/webhooks/paypal/o1" }, multiparty_ready: false, eligibility_notes: [] },
      "GET /webhooks/events": [],
      "GET /jobs/status": { scheduler_enabled: true, interval_seconds: 900, paypal_configured: true, last_run: { at: "2026-11-10T10:00:00Z", result: { org: "Mkt", mode: "sandbox", mismatches: 2, disputes_synced: 4 } } },
      "POST /jobs/run": { org: "Mkt", mode: "sandbox", mismatches: 5, disputes_synced: 7, deadline_alerts: 1 },
    })(<Integrations />);
    expect(await screen.findByText("every 15 min")).toBeInTheDocument();
    expect(screen.getByText(/^disputes synced:/)).toHaveTextContent("4");
    await u.click(screen.getByRole("button", { name: "Run sync now" }));
    await waitFor(() => expect(calls).toContain("POST /jobs/run"));
    expect(await screen.findByText(/^deadline alerts:/)).toHaveTextContent("1");
  });
});
