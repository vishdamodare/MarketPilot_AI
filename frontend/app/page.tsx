import Link from "next/link";
import { Bot, GitMerge, Scale, ShieldAlert, Split } from "lucide-react";

const modules = [
  { icon: Split, name: "SmartSplit", text: "Sellers, commissions and per-order allocations. Payment, commission, settlement and payout are tracked as separate records, so a successful checkout is never mistaken for a payout." },
  { icon: ShieldAlert, name: "RefundGuard", text: "Deterministic validation before every refund, then anomaly signals for duplicates and odd patterns. A merchant approves each refund; the AI never decides." },
  { icon: Scale, name: "DisputePilot", text: "Collects only verified evidence, shows what is missing, drafts the response, and submits to PayPal only after explicit approval." },
  { icon: GitMerge, name: "MoneyMatch", text: "Reconciles orders, captures, commissions, refunds and payouts. Every total drills down to the transactions behind it." },
];

export default function Landing() {
  return (
    <div className="bg-white text-navy">
      <header className="mx-auto flex max-w-6xl items-center justify-between px-5 py-4">
        <Link href="/" className="text-lg font-semibold tracking-tight">MarketPilot<span className="text-brand"> AI</span></Link>
        <nav className="flex items-center gap-5 text-sm">
          <a href="#modules" className="hidden text-slate-600 hover:text-navy sm:block">Product</a>
          <a href="#paypal" className="hidden text-slate-600 hover:text-navy sm:block">PayPal</a>
          <Link href="/login" className="text-slate-600 hover:text-navy">Log in</Link>
          <Link href="/signup" className="rounded-md bg-navy px-3.5 py-2 font-medium text-white hover:bg-[#1c3166]">Start free</Link>
        </nav>
      </header>

      <section className="bg-navy text-white">
        <div className="mx-auto max-w-6xl px-5 py-20">
          <p className="text-sm font-medium text-gold">Built on PayPal APIs</p>
          <h1 className="mt-3 max-w-3xl text-4xl font-semibold leading-tight tracking-tight sm:text-5xl">The AI Financial Operating System for Marketplaces.</h1>
          <p className="mt-5 max-w-2xl text-lg text-slate-300">One platform. Every transaction. Every seller. Every refund. Every dispute. One intelligent financial brain that investigates your real records and recommends what to do next.</p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/signup?mode=demo" className="rounded-md bg-brand px-5 py-3 font-medium text-white hover:bg-[#d9631a]">Explore the demo marketplace</Link>
            <Link href="/signup" className="rounded-md border border-white/30 px-5 py-3 font-medium hover:bg-white/10">Connect PayPal Sandbox</Link>
          </div>
          <p className="mt-4 text-xs text-slate-400">Demo mode uses clearly labelled simulated data and never calls PayPal.</p>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 py-16">
        <h2 className="text-2xl font-semibold">Marketplaces lose money in the gaps between tools.</h2>
        <p className="mt-3 max-w-3xl text-slate-600">Collections, commissions, seller payouts, refunds, disputes and reconciliation usually live in different dashboards. Duplicate refunds, missed dispute deadlines and payouts that do not match the ledger go unnoticed until month-end.</p>
      </section>

      <section id="modules" className="bg-surface py-16">
        <div className="mx-auto max-w-6xl px-5">
          <h2 className="text-2xl font-semibold">Four engines, one agent</h2>
          <div className="mt-8 grid gap-4 sm:grid-cols-2">
            {modules.map((m) => (
              <div key={m.name} className="rounded-lg border border-slate-200 bg-white p-5">
                <m.icon className="text-brand" size={22} />
                <h3 className="mt-3 font-semibold">{m.name}</h3>
                <p className="mt-1.5 text-sm text-slate-600">{m.text}</p>
              </div>
            ))}
            <div className="rounded-lg border border-navy bg-navy p-5 text-white sm:col-span-2">
              <Bot className="text-gold" size={22} />
              <h3 className="mt-3 font-semibold">MarketPilot AI Agent</h3>
              <p className="mt-1.5 text-sm text-slate-300">Ask &ldquo;What is putting my marketplace money at risk?&rdquo; The agent calls audited, permission-checked tools across all four engines, cites the transactions it used, and prepares drafts. It cannot move money, approve refunds or submit disputes. Every number comes from SQL and deterministic code, never from the model.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 py-16">
        <h2 className="text-2xl font-semibold">How it works</h2>
        <ol className="mt-6 grid gap-4 sm:grid-cols-4">
          {["Connect PayPal Sandbox or load the demo marketplace", "Orders are split into seller allocations and commissions", "Engines flag refund, dispute and ledger problems", "You approve; sensitive actions are audited"].map((t, i) => (
            <li key={t} className="rounded-lg border border-slate-200 p-4 text-sm"><span className="text-2xl font-semibold text-brand">{i + 1}</span><p className="mt-2 text-slate-700">{t}</p></li>
          ))}
        </ol>
      </section>

      <section id="paypal" className="bg-surface py-16">
        <div className="mx-auto max-w-6xl px-5">
          <h2 className="text-2xl font-semibold">PayPal at the core, honest about eligibility</h2>
          <p className="mt-3 max-w-3xl text-slate-600">Orders, Payments, Refunds, Disputes, Transaction Search, Payouts and Webhooks are called through a single server-side adapter with OAuth, idempotency keys, retries and verified webhooks. Multiparty payments, partner fees and seller onboarding require PayPal partner approval and are not available in every country; where unavailable, flows are shown as explicitly labelled simulations and never presented as real PayPal payments.</p>
        </div>
      </section>

      <section className="mx-auto max-w-6xl px-5 py-16">
        <h2 className="text-2xl font-semibold">Why teams use it</h2>
        <ul className="mt-5 grid gap-3 text-sm text-slate-700 sm:grid-cols-3">
          <li className="rounded-lg border border-slate-200 p-4"><b>Stop avoidable leakage.</b> Duplicate and over-refunds are caught before money leaves.</li>
          <li className="rounded-lg border border-slate-200 p-4"><b>Never miss a deadline.</b> Dispute deadlines come from PayPal and are surfaced by urgency.</li>
          <li className="rounded-lg border border-slate-200 p-4"><b>Trust every number.</b> An append-only ledger and drill-downs make totals auditable.</li>
        </ul>
        <div className="mt-10 rounded-lg bg-navy p-8 text-white">
          <h3 className="text-xl font-semibold">See the &ldquo;Marketplace Financial Crisis&rdquo; in three minutes</h3>
          <p className="mt-2 text-slate-300">25 sellers, 120 orders, 5 disputes, 3 duplicate refunds, 4 unreconciled records and 2 payout mismatches, all simulated and ready to investigate.</p>
          <Link href="/signup?mode=demo" className="mt-5 inline-block rounded-md bg-brand px-5 py-2.5 font-medium hover:bg-[#d9631a]">Open the demo</Link>
        </div>
      </section>

      <footer className="border-t border-slate-200 py-8 text-center text-xs text-slate-500">
        © MarketPilot AI · Not affiliated with or endorsed by PayPal · Demo data is simulated
      </footer>
    </div>
  );
}
