"use client";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, ErrorBox, Loading, Modal, PageHead, Select, Stat, StatusBadge, Table, Tabs } from "@/components/ui";
import { api, getOrgId, money, when } from "@/lib/api";
import { useAct, useQ, useSession } from "@/lib/session";

const LABEL: Record<string, string> = { payments: "Total processed", refunds: "Refunds", commissions: "Commissions (net)", fees: "PayPal fees", seller_earnings: "Seller earnings (net)", pending_settlements: "Pending seller settlements", payouts: "Payouts", failed_payments: "Failed payments", pending_payments: "Pending payments" };
const REPORTS = [["daily", "Daily"], ["weekly", "Weekly"], ["monthly", "Monthly"], ["commission", "Commission"], ["refunds", "Refunds"], ["disputes", "Disputes"]];

function Trace({ metric, onClose }: { metric: string; onClose: () => void }) {
  const { m } = useSession();
  const q = useQ<any>(`/moneymatch/trace?metric=${metric}&days=30`);
  return (
    <Modal open onClose={onClose} title={`Source records: ${LABEL[metric]}`} wide>
      {!q.data ? <Loading /> : <><p className="mb-2 text-sm">Total <b>{money(q.data.total, m.currency)}</b> = sum of the {q.data.rows.length} records below.</p>
        <div className="max-h-96 overflow-y-auto"><Table rows={q.data.rows} cols={[{ h: "Type", r: (r: any) => <Badge>{r.ref_type}</Badge> }, { h: "Reference", r: (r: any) => <code className="text-xs">{r.ref_id.slice(0, 18)}</code> }, { h: "Amount", r: (r: any) => money(r.amount, m.currency), right: true }, { h: "Date", r: (r: any) => when(r.at) }]} /></div></>}
    </Modal>
  );
}

export default function MoneyMatch() {
  const { m, can } = useSession();
  const [tab, setTab] = useState("recon");
  const [trace, setTrace] = useState<string | null>(null);
  const [report, setReport] = useState("weekly");
  const latest = useQ<any>("/moneymatch/latest");
  const sum = useQ<any>(tab === "summary" ? "/moneymatch/summary?days=30" : null);
  const ledger = useQ<any>(tab === "ledger" ? "/moneymatch/ledger" : null);
  const rep = useQ<any>(tab === "reports" ? `/moneymatch/reports/${report}` : null);
  const run = useAct(() => api("/moneymatch/run", { body: {} }));
  const download = async () => {
    const r = await fetch(`/api/moneymatch/reports/${report}?format=csv`, { headers: { "X-Org-Id": getOrgId() ?? "", "X-Requested-With": "marketpilot" } });
    const url = URL.createObjectURL(await r.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: `${report}-report.csv` });
    a.click(); URL.revokeObjectURL(url);
  };
  const run_ = latest.data?.run, recs = latest.data?.records ?? [];
  return (
    <>
      <PageHead title="MoneyMatch" sub="Reconciliation, mismatch detection, the financial ledger and reports" right={can("reconcile.run") && <Button disabled={run.isPending} onClick={() => run.mutate()}>{run.isPending ? "Reconciling…" : "Run reconciliation"}</Button>} />
      <ErrorBox error={run.error} />
      <Tabs tabs={[["recon", "Reconciliation"], ["summary", "Totals & drill-down"], ["ledger", "Ledger"], ["reports", "Reports"]]} value={tab} onChange={setTab} />
      {tab === "recon" && (latest.isLoading ? <Loading /> : <>
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Last run" value={run_ ? when(run_.started_at).split(",")[0] : "Never"} /><Stat label="Records checked" value={run_?.total_checked ?? 0} />
          <Stat label="Mismatches" value={run_?.mismatches ?? 0} tone={run_?.mismatches ? "bad" : undefined} /><Stat label="High severity" value={recs.filter((r: any) => r.severity === "high").length} tone="bad" />
        </div>
        <Card><CardHeader title="Mismatches" sub="Orders ↔ PayPal captures ↔ commissions ↔ refunds ↔ payouts" />
          <Table rows={recs} empty={run_ ? "No mismatches. Everything reconciles." : "Run a reconciliation to see results."} cols={[{ h: "Severity", r: (r: any) => <StatusBadge s={r.severity} /> }, { h: "Kind", r: (r: any) => r.kind.replaceAll("_", " ") }, { h: "Reference", r: (r: any) => <code className="text-xs">{r.ref_id}</code> },
            { h: "Expected", r: (r: any) => r.expected === null ? "—" : money(r.expected, m.currency), right: true }, { h: "Actual", r: (r: any) => r.actual === null ? "—" : money(r.actual, m.currency), right: true }, { h: "Explanation", r: (r: any) => <span className="text-xs text-slate-600">{r.message}</span> }]} /></Card></>)}
      {tab === "summary" && (!sum.data ? <Loading /> : <Card><CardHeader title="Last 30 days" sub="Every figure is the sum of source records. Click “View sources”." />
        <Table rows={Object.entries<any>(sum.data.metrics)} cols={[{ h: "Metric", r: ([k]) => LABEL[k] }, { h: "Records", r: ([, v]) => v.count, right: true }, { h: "Amount", r: ([, v]) => <b>{money(v.amount, m.currency)}</b>, right: true }, { h: "", r: ([k]) => <Button size="sm" variant="ghost" onClick={() => setTrace(k)}>View sources</Button> }]} /></Card>)}
      {tab === "ledger" && (!ledger.data ? <Loading /> : <>
        <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4"><Stat label="Cash held" value={money(ledger.data.totals.cash, m.currency)} /><Stat label="Owed to sellers" value={money(ledger.data.totals.seller_liability, m.currency)} /><Stat label="Platform revenue" value={money(ledger.data.totals.platform, m.currency)} />
          <Stat label="Invariant (cash − sellers − platform)" value={money(ledger.data.invariant.cash_minus_seller_minus_platform, m.currency)} sub={Number(ledger.data.invariant.cash_minus_seller_minus_platform) === 0 ? "Balanced ✓" : "Unbalanced — investigate"} tone={Number(ledger.data.invariant.cash_minus_seller_minus_platform) === 0 ? undefined : "bad"} /></div>
        <Card><CardHeader title="Append-only financial ledger" sub="Latest 200 entries. Balances are always derived from these rows." /><Table rows={ledger.data.entries} cols={[{ h: "When", r: (r: any) => when(r.created_at) }, { h: "Type", r: (r: any) => <Badge>{r.entry_type}</Badge> }, { h: "Account", r: (r: any) => r.account }, { h: "Seller", r: (r: any) => r.seller ?? "—" }, { h: "Amount", r: (r: any) => <span className={Number(r.amount) < 0 ? "text-red-600" : ""}>{money(r.amount, m.currency)}</span>, right: true }, { h: "Ref", r: (r: any) => <code className="text-xs">{r.ref_id?.slice(0, 12) ?? "—"}</code> }]} /></Card></>)}
      {tab === "reports" && <Card><CardHeader title="Reports" right={<div className="flex gap-2"><Select aria-label="Report type" className="!w-40 !py-1 text-xs" value={report} onChange={(e) => setReport(e.target.value)}>{REPORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</Select><Button size="sm" variant="navy" onClick={download}>Download CSV</Button></div>} sub="Seller statements: open a seller and use its ledger balance, or request /reports/seller_statement?seller_id=…" />
        {!rep.data ? <Loading /> : <Table rows={rep.data.rows} empty="No data for this report." cols={rep.data.columns.map((c: string, i: number) => ({ h: c, r: (r: any[]) => r[i] }))} />}</Card>}
      {trace && <Trace metric={trace} onClose={() => setTrace(null)} />}
    </>
  );
}
