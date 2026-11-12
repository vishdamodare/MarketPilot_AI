// Renders EVERY dashboard page against a running backend (real data, no mocks). Skipped unless LIVE_URL is set:
//   LIVE_URL=http://localhost:3217 LIVE_EMAIL=… LIVE_PASSWORD=… npx vitest run tests/live.test.tsx
import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }), usePathname: () => "/overview", useSearchParams: () => new URLSearchParams() }));
vi.mock("recharts", async (orig) => { const m: any = await orig(); return { ...m, ResponsiveContainer: ({ children }: any) => <div style={{ width: 600, height: 300 }}>{children}</div> }; });

import { SessionProvider } from "@/lib/session";
const URL_ = process.env.LIVE_EMAIL ? process.env.LIVE_URL : undefined, d = URL_ ? describe : describe.skip;
const PAGES = [...(process.env.LIVE_SELLER ? ["portal"] : []), "overview", "agent", "sellers", "transactions", "smartsplit", "refundguard", "disputepilot", "moneymatch", "analytics", "notifications", "integrations", "settings"];
let cookie = "", org = "";

d("every page renders with live data", () => {
  beforeAll(async () => {
    const r = await fetch(`${URL_}/api/auth/login`, { method: "POST", headers: { "Content-Type": "application/json", "X-Requested-With": "marketpilot" }, body: JSON.stringify({ email: process.env.LIVE_EMAIL, password: process.env.LIVE_PASSWORD }) });
    cookie = r.headers.get("set-cookie")!.split(";")[0];
    org = (await r.json()).memberships[0].org_id;
    window.localStorage.setItem("mp_org", org);
    vi.stubGlobal("fetch", (u: string, init: any = {}) => fetch0(`${URL_}${u}`, { ...init, headers: { ...init.headers, cookie } }));
  });
  afterAll(() => vi.unstubAllGlobals());
  const fetch0 = globalThis.fetch;

  for (const p of PAGES) {
    it(`/${p}`, async () => {
      const errors: unknown[] = [];
      const spy = vi.spyOn(console, "error").mockImplementation((...a) => { errors.push(a[0]); });
      const Page = (await import(`@/app/(app)/${p}/page`)).default;
      render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><SessionProvider><Page /></SessionProvider></QueryClientProvider>);
      await waitFor(() => expect(screen.queryByText(/^Loading/)).not.toBeInTheDocument(), { timeout: 15000 });
      await new Promise((r) => setTimeout(r, 400));
      const alerts = screen.queryAllByRole("alert").filter((a) => !/need immediate attention/.test(a.textContent ?? "")); // the deadline banner is intentional
      expect(alerts.map((a) => a.textContent), "page shows an error box").toEqual([]);
      expect(errors.filter((e) => !String(e).includes("act(")), "console errors").toEqual([]);
      spy.mockRestore();
      cleanup();
    }, 30000);
  }
});
