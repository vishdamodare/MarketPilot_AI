import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace, push: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

import { LoginForm } from "@/components/auth-forms";
import { SessionProvider } from "@/lib/session";
import Agent from "@/app/(app)/agent/page";
import DisputePilot from "@/app/(app)/disputepilot/page";
import MoneyMatch from "@/app/(app)/moneymatch/page";
import Overview from "@/app/(app)/overview/page";
import RefundGuard from "@/app/(app)/refundguard/page";
import Sellers from "@/app/(app)/sellers/page";

// ---- fetch mock: "METHOD /path" -> JSON (or function of the parsed body) ----
const calls: { key: string; body: any; headers: Record<string, string> }[] = [];
function mockApi(routes: Record<string, any>) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit = {}) => {
    const key = `${init.method ?? "GET"} ${String(url).replace(/^\/api/, "")}`;
    const body = init.body ? JSON.parse(init.body as string) : undefined;
    calls.push({ key, body, headers: init.headers as Record<string, string> });
    const hit = Object.keys(routes).find((k) => key === k || key.startsWith(k + "?"));
    if (!hit) return new Response(JSON.stringify({ detail: `unmocked ${key}` }), { status: 404 });
    const v = typeof routes[hit] === "function" ? routes[hit](body) : routes[hit];
    return v instanceof Response ? v : new Response(JSON.stringify(v), { status: 200 });
  }));
}

const ME = (perms: string[] = ["dashboard", "sellers.read", "sellers.write", "commission.write", "refunds.read", "refunds.write", "refunds.approve", "disputes.read", "disputes.write", "disputes.submit", "reconcile.read", "reconcile.run", "agent.use", "notifications.read", "reports.read", "orders.write", "integrations.manage"], mode = "demo") => ({
  user: { id: "u1", name: "Ada", email: "ada@example.com" },
  memberships: [{ org_id: "o1", org_name: "Test Market", mode, currency: "USD", role: "admin", seller_id: null, permissions: perms }],
});

function renderPage(ui: React.ReactElement, extra: Record<string, any> = {}, perms?: string[]) {
  mockApi({ "GET /auth/me": ME(perms), "GET /notifications": [], ...extra });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><SessionProvider>{ui}</SessionProvider></QueryClientProvider>);
}

beforeEach(() => { calls.length = 0; replace.mockClear(); window.localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("authentication", () => {
  it("validates before submitting, then logs in with the CSRF header and routes to the dashboard", async () => {
    mockApi({ "POST /auth/login": ME() });
    const u = userEvent.setup();
    render(<LoginForm />);
    await u.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByText("Enter a valid email")).toBeInTheDocument();
    expect(calls).toHaveLength(0);
    await u.type(screen.getByLabelText("Email"), "ada@example.com");
    await u.type(screen.getByLabelText("Password"), "correct-horse-9");
    await u.click(screen.getByRole("button", { name: "Log in" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/overview"));
    expect(calls[0].headers["X-Requested-With"]).toBe("marketpilot");
    expect(calls[0].body).toEqual({ email: "ada@example.com", password: "correct-horse-9" });
  });

  it("shows the server's error for bad credentials", async () => {
    mockApi({ "POST /auth/login": new Response(JSON.stringify({ detail: "Invalid email or password" }), { status: 401 }) });
    const u = userEvent.setup();
    render(<LoginForm />);
    await u.type(screen.getByLabelText("Email"), "ada@example.com");
    await u.type(screen.getByLabelText("Password"), "nope");
    await u.click(screen.getByRole("button", { name: "Log in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });
});

describe("dashboard", () => {
  it("renders backend figures and AI alerts, and labels demo mode via the shell data", async () => {
    renderPage(<Overview />, {
      "GET /overview": {
        mode: "demo", currency: "USD", open_disputes: 5, pending_refunds: 3,
        summary: Object.fromEntries(["payments", "refunds", "commissions", "fees", "seller_earnings", "pending_settlements", "payouts", "failed_payments", "pending_payments"].map((k) => [k, { amount: k === "payments" ? "7960.38" : "10.00", count: 3 }])),
        reconciliation: { checked: 236, mismatches: 7, health_pct: 97, last_run: "2026-11-10T10:00:00Z" },
        chart: [{ day: "11-09", payments: 10, commission: 1 }], activity: [{ action: "refund.requested", actor: "user", at: "2026-11-10T10:00:00Z" }],
        alerts: [{ title: "2 refund request(s) flagged for review, 2 potential duplicate(s)", severity: "high", action: "Open RefundGuard", refs: [{ type: "refund", id: "r1", label: "r1" }] }],
      },
    });
    expect(await screen.findByText("$7,960.38")).toBeInTheDocument();
    expect(screen.getByText("Total payment volume")).toBeInTheDocument();
    expect(screen.getByText(/2 potential duplicate/)).toBeInTheDocument();
    expect(screen.getByText("97%")).toBeInTheDocument();
    expect(screen.getByText("All figures simulated")).toBeInTheDocument();
  });
});

const SELLERS = { total: 2, active: 1, pending: 1, suspended: 0, sellers: [
  { id: "s1", business_name: "Aurora Home", email: "a@example.com", status: "active", onboarding_status: "completed", paypal_merchant_id: "SIM-MERCH-1", effective_commission_pct: "10.00", commission_pct: null, balance: "90.00" },
  { id: "s2", business_name: "Bluebird", email: "b@example.com", status: "pending", onboarding_status: "not_started", paypal_merchant_id: null, effective_commission_pct: "10.00", commission_pct: null, balance: "0.00" }] };

describe("seller management", () => {
  it("lists sellers, filters, and creates a seller through a validated form", async () => {
    const u = userEvent.setup();
    renderPage(<Sellers />, { "GET /sellers": SELLERS, "POST /sellers": (b: any) => ({ id: "s3", ...b }) });
    expect(await screen.findByText("Aurora Home")).toBeInTheDocument();
    await u.type(screen.getByLabelText("Filter sellers"), "blue");
    expect(screen.queryByText("Aurora Home")).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Add seller" }));
    const dlg = within(screen.getByRole("dialog"));
    await u.click(dlg.getByRole("button", { name: "Add seller" }));
    expect(await dlg.findByText("Business name is required")).toBeInTheDocument();
    await u.type(dlg.getByLabelText("Business name"), "Cedar & Stone");
    await u.type(dlg.getByLabelText("Seller email"), "cedar@example.com");
    await u.click(dlg.getByRole("button", { name: "Add seller" }));
    await waitFor(() => expect(calls.find((c) => c.key === "POST /sellers")?.body).toMatchObject({ business_name: "Cedar & Stone", email: "cedar@example.com", commission_pct: null }));
  });
});

const REFUND_ROW = { id: "r1", order_number: "ORD-1027", amount: "81.75", currency: "USD", status: "needs_review", risk: "high", is_simulated: true, paypal_refund_id: null, created_at: "2026-11-10T10:00:00Z" };
const REFUND_DETAIL = { refund: REFUND_ROW, investigation: { risk_level: "high", recommended_action: "Hold: verify whether this is a duplicate.", report: {
  reason: "1 other refund(s) on this order for the identical amount 81.75", ai_summary: "Risk high.", disclaimer: "Anomaly signals are for human review. They are not a finding of fraud.",
  original_transaction_id: "SIM-CAP-1", order_number: "ORD-1027", seller_id: "abcdef123", customer_id: "CUST-1", original_amount: "163.50", previously_refunded: "81.75", requested_amount: "81.75",
  checks: [{ name: "cumulative_within_refundable", passed: true, detail: "ok" }], flags: [{ code: "potential_duplicate", severity: "high", detail: "identical amount" }],
  refund_history: [{ refund_id: "abcd1234", amount: "81.75", status: "completed", created_at: "2026-11-09T10:00:00Z" }] } } };

describe("refund investigation", () => {
  it("shows the investigation report and requires an explicit confirmation before approving", async () => {
    const u = userEvent.setup();
    renderPage(<RefundGuard />, {
      "GET /refunds": { stats: { pending: { count: 1, amount: "81.75" } }, total: 1, total_amount: "81.75", trend: [], refunds: [REFUND_ROW] },
      "GET /refunds/r1": REFUND_DETAIL, "POST /refunds/r1/approve": { ...REFUND_ROW, status: "completed" },
    });
    await u.click(await screen.findByText("ORD-1027"));
    const dlg = within(await screen.findByRole("dialog", { name: /Refund/ }));
    expect(await dlg.findByText(/potential duplicate/i)).toBeInTheDocument();
    expect(dlg.getByText(/not a finding of fraud/i)).toBeInTheDocument();
    expect(dlg.getByText(/Hold: verify/)).toBeInTheDocument();
    await u.click(dlg.getByRole("button", { name: /Approve refund/ }));
    expect(calls.some((c) => c.key.endsWith("/approve"))).toBe(false); // nothing sent yet
    await u.click(await screen.findByRole("button", { name: "Yes, approve refund" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /refunds/r1/approve")).toBe(true));
  });
});

const EVIDENCE = ["order", "invoice", "payment", "shipping_info", "tracking", "delivery_confirmation"].map((k) => ({ kind: k, label: k.replace("_", " "), available: k !== "invoice" && k !== "delivery_confirmation", detail: k === "invoice" ? "No invoice source is connected" : "present" }));
const DISPUTE = (resp: any) => ({ simulated: true, dispute: { id: "d1", paypal_dispute_id: "PP-SIM-D-5002", status: "waiting_merchant", amount: "100.00", currency: "USD", reason: "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", response_due_date: "2026-11-12T10:00:00Z" },
  analysis: { score: 84.6, score_note: "Evidence completeness only. This is NOT a probability of winning.", score_breakdown: { availability: { score: 20, max: 40, basis: "x" } }, evidence: EVIDENCE, required: ["order", "delivery_confirmation"], contradictions: [], recommendation: "gather_evidence", recommendation_reason: "Missing required evidence: Delivery confirmation.", deadline: { level: "critical", hours_left: 30 } }, response: resp });

describe("dispute workflow", () => {
  it("shows missing evidence, keeps Submit locked until approval, and never submits unapproved text", async () => {
    const u = userEvent.setup();
    let resp: any = { id: "x1", status: "draft", action: "contest", body: "DISPUTE RESPONSE\n- Delivery confirmation: NOT AVAILABLE", approved_by: null };
    renderPage(<DisputePilot />, {
      "GET /disputes": { stats: { waiting_merchant: { count: 1, amount: "100.00" } }, disputes: [{ id: "d1", paypal_dispute_id: "PP-SIM-D-5002", reason: "MERCHANDISE_OR_SERVICE_NOT_RECEIVED", amount: "100.00", currency: "USD", status: "waiting_merchant", is_simulated: true, urgency: { level: "critical", hours_left: 30 } }] },
      "GET /disputes/d1": () => DISPUTE(resp),
      "POST /disputes/d1/responses/x1/approve": () => { resp = { ...resp, status: "approved", approved_by: "u1" }; return DISPUTE(resp); },
      "POST /disputes/d1/responses/x1/submit": () => { resp = { ...resp, status: "submitted", result: { simulated: true, note: "Demo mode: nothing was sent to PayPal." }, submitted_at: "2026-11-10T11:00:00Z" }; return DISPUTE(resp); },
    });
    expect(await screen.findByText(/immediate attention/)).toBeInTheDocument();
    await u.click(within(await screen.findByRole("table")).getByText("PP-SIM-D-5002"));
    const dlg = within(await screen.findByRole("dialog", { name: /Dispute/ }));
    expect(await dlg.findByText(/MISSING: No invoice source/)).toBeInTheDocument();
    expect(dlg.getByText(/NOT a probability of winning/)).toBeInTheDocument();
    const submit = dlg.getByRole("button", { name: /Submit to PayPal/ });
    expect(submit).toBeDisabled();
    await u.click(dlg.getByRole("button", { name: "Approve response" }));
    await waitFor(() => expect(dlg.getByRole("button", { name: /Submit to PayPal/ })).toBeEnabled());
    await u.click(dlg.getByRole("button", { name: /Submit to PayPal/ }));
    await u.click(within(screen.getByRole("dialog", { name: /Submit evidence/ })).getByRole("button", { name: "Confirm" }));
    expect(await dlg.findByText(/nothing was sent to PayPal/)).toBeInTheDocument();
  });

  it("editing the draft locks approval until it is saved", async () => {
    const u = userEvent.setup();
    renderPage(<DisputePilot />, {
      "GET /disputes": { stats: {}, disputes: [{ id: "d1", paypal_dispute_id: "PP-1", reason: "OTHER", amount: "1.00", currency: "USD", status: "open", is_simulated: true, urgency: { level: "normal", hours_left: 200 } }] },
      "GET /disputes/d1": DISPUTE({ id: "x1", status: "draft", action: "contest", body: "original body text here", approved_by: null }),
    });
    await u.click(await screen.findByText("PP-1"));
    const dlg = within(await screen.findByRole("dialog", { name: /Dispute/ }));
    await u.type(await dlg.findByLabelText("Response text"), " plus edit");
    expect(dlg.getByRole("button", { name: "Approve response" })).toBeDisabled();
    expect(dlg.getByText(/unsaved edits/i)).toBeInTheDocument();
  });
});

describe("reconciliation", () => {
  it("shows mismatches with expected/actual amounts and can trigger a run", async () => {
    const u = userEvent.setup();
    renderPage(<MoneyMatch />, {
      "GET /moneymatch/latest": { run: { started_at: "2026-11-10T10:00:00Z", total_checked: 236, mismatches: 1 }, records: [{ id: "m1", kind: "payout_mismatch", severity: "high", ref_id: "ab12cd34", expected: "758.70", actual: "722.34", message: "Payout 722.34 differs from the 758.70 its settlements support" }] },
      "POST /moneymatch/run": { run: { started_at: "2026-11-10T10:05:00Z", total_checked: 236, mismatches: 0 }, records: [] },
    });
    expect(await screen.findByText("payout mismatch")).toBeInTheDocument();
    expect(screen.getByText("$758.70")).toBeInTheDocument();
    expect(screen.getByText("$722.34")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Run reconciliation" }));
    await waitFor(() => expect(calls.some((c) => c.key === "POST /moneymatch/run")).toBe(true));
  });
});

describe("AI conversation", () => {
  it("sends a question, renders findings with transaction references and the tool trace", async () => {
    const u = userEvent.setup();
    renderPage(<Agent />, {
      "GET /agent/status": { mode: "deterministic", note: "OPENAI_API_KEY not set", tools: [] },
      "POST /agent/ask": (b: any) => ({ mode: "deterministic", answer: `I investigated and found 1 issue(s) for: ${b.question}`, tool_calls: [{ tool: "get_financial_risk_summary", args: {}, ok: true }],
        findings: [{ title: "3 payout record(s) need attention", severity: "high", detail: "Payout 868.06 differs from 908.06", action: "Review payouts in MoneyMatch", refs: [{ type: "payout", id: "p1", label: "payout-ab12" }] }] }),
    });
    expect(await screen.findByText("Deterministic investigator")).toBeInTheDocument();
    await u.type(screen.getByLabelText("Ask the agent"), "What is putting my marketplace money at risk?");
    await u.click(screen.getByRole("button", { name: /Ask/ }));
    expect(await screen.findByText(/I investigated and found/)).toBeInTheDocument();
    expect(screen.getByText("3 payout record(s) need attention")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "payout-ab12" })).toHaveAttribute("href", "/moneymatch");
    expect(screen.getByText(/get_financial_risk_summary/)).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /agent/ask")?.body).toEqual({ question: "What is putting my marketplace money at risk?" });
  });
});
