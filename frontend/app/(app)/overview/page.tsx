"use client";
import Link from "next/link";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Card, CardHeader, ErrorBox, Loading, PageHead, StatusBadge, Stat } from "@/components/ui";
import { money, when } from "@/lib/api";
import { useQ, useSession } from "@/lib/session";

const LINK: Record<string, string> = { refund: "/refundguard", dispute: "/disputepilot", payout: "/moneymatch", order: "/transactions", capture: "/moneymatch" };

export default function Overview() {
  const { m } = useSession();
  const q = useQ<any>("/overview");
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const o = q.data, s = o.summary, c = o.currency;
  const rec = o.reconciliation;
  return (
    <>
      <PageHead title="Overview" sub={`Last 30 days · ${m.org_name}`} right={m.mode === "demo" ? <Badge tone="orange">All figures simulated</Badge> : undefined} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Total payment volume" value={money(s.payments.amount, c)} sub={`${s.payments.count} captured payments`} />
        <Stat label="Marketplace commission" value={money(s.commissions.amount, c)} sub="net of refund reversals" />
        <Stat label="Seller earnings" value={money(s.seller_earnings.amount, c)} sub="net of refund clawbacks" />
        <Stat label="Pending payouts" value={money(s.pending_settlements.amount, c)} sub={`${s.pending_settlements.count} settlements unpaid`} tone={Number(s.pending_settlements.amount) > 0 ? "warn" : undefined} />
        <Stat label="Refunds" value={money(s.refunds.amount, c)} sub={`${s.refunds.count} completed · ${o.pending_refunds} awaiting review`} />
        <Stat label="Open disputes" value={o.open_disputes} sub="need a merchant response" tone={o.open_disputes ? "bad" : undefined} />
        <Stat label="PayPal fees" value={money(Math.abs(Number(s.fees.amount)), c)} sub="borne by the marketplace" />
        <Stat label="Reconciliation health" value={rec.health_pct === null ? "Not run" : `${rec.health_pct}%`} sub={rec.last_run ? `${rec.mismatches} mismatches of ${rec.checked} records` : "Run it in MoneyMatch"} tone={rec.mismatches ? "warn" : undefined} />
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title="Payments vs. commission" sub="Last 14 days" />
          <div className="h-64 p-3">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={o.chart}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="day" fontSize={11} /><YAxis fontSize={11} width={48} />
                <Tooltip formatter={(v) => money(Number(v), c)} />
                <Area type="monotone" dataKey="payments" stroke="#132244" fill="#13224422" name="Payments" />
                <Area type="monotone" dataKey="commission" stroke="#F0721E" fill="#F0721E33" name="Commission" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
        <Card>
          <CardHeader title="AI financial alerts" sub="Computed from your records; nothing is estimated" right={<Link href="/agent" className="text-xs font-medium text-brand">Ask the agent →</Link>} />
          <ul className="divide-y divide-slate-100">
            {o.alerts.length === 0 && <li className="p-4 text-sm text-slate-500">No issues detected.</li>}
            {o.alerts.map((a: any) => (
              <li key={a.title} className="p-4 text-sm">
                <div className="flex items-start gap-2"><StatusBadge s={a.severity} /><span className="font-medium">{a.title}</span></div>
                <p className="mt-1 text-xs text-slate-500">{a.action}</p>
                <Link href={LINK[a.refs?.[0]?.type] ?? "/agent"} className="mt-1 inline-block text-xs font-medium text-brand">Investigate →</Link>
              </li>
            ))}
          </ul>
        </Card>
      </div>
      <Card className="mt-4">
        <CardHeader title="Recent activity" sub="Audit trail" />
        <ul className="divide-y divide-slate-100 text-sm">
          {o.activity.map((a: any, i: number) => (
            <li key={i} className="flex justify-between gap-3 px-4 py-2.5"><span><Badge>{a.actor}</Badge> <span className="ml-1 font-mono text-xs">{a.action}</span></span><span className="text-xs text-slate-500">{when(a.at)}</span></li>
          ))}
        </ul>
      </Card>
    </>
  );
}
