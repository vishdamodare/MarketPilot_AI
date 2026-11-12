"use client";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge, Button, Card, CardHeader, ErrorBox, Loading, Modal, PageHead, SimBadge, Stat, StatusBadge, Table } from "@/components/ui";
import { api, money, when } from "@/lib/api";
import { useAct, useQ, useSession } from "@/lib/session";

function Investigation({ id, onClose }: { id: string; onClose: () => void }) {
  const { can, m } = useSession();
  const q = useQ<any>(`/refunds/${id}`);
  const [confirm, setConfirm] = useState(false);
  const approve = useAct(() => api(`/refunds/${id}/approve`, { body: {} }));
  const reject = useAct(() => api(`/refunds/${id}/reject`, { body: { note: "Rejected by merchant after review" } }));
  const escalate = useAct(() => api(`/refunds/${id}/escalate`, { body: {} }));
  const rerun = useAct(() => api(`/refunds/${id}/investigate`, { body: {} }));
  const r = q.data?.refund, inv = q.data?.investigation, rep = inv?.report;
  const open = r && ["needs_review", "pending_approval", "escalated"].includes(r.status);
  const done = (p: any) => p.then(() => onClose()).catch(() => null);
  return (
    <Modal open onClose={onClose} title={r ? `Refund ${money(r.amount, r.currency)} · ${r.order_number}` : "Refund"} wide>
      {!r ? <Loading /> : <div className="space-y-4 text-sm">
        <div className="flex flex-wrap items-center gap-2"><StatusBadge s={r.status} />{inv && <><span>Risk:</span><StatusBadge s={inv.risk_level} /></>}{r.is_simulated && <SimBadge />}<span className="text-slate-500">requested {when(r.created_at)}</span></div>
        {!inv ? <div className="rounded border border-slate-200 p-3">No investigation yet (no anomalies were flagged at request time).{can("refunds.write") && <Button size="sm" variant="outline" className="ml-3" onClick={() => rerun.mutate()}>Run investigation</Button>}</div> : <>
          <div className="rounded border border-amber-200 bg-amber-50 p-3"><b>Why flagged:</b> {rep.reason}<p className="mt-1 text-xs text-slate-600">{rep.ai_summary}</p><p className="mt-1 text-xs italic text-slate-500">{rep.disclaimer}</p></div>
          <div className="grid gap-3 sm:grid-cols-2">
            <dl className="grid grid-cols-2 gap-x-3 gap-y-1 rounded border border-slate-200 p-3 text-xs">
              {[["Original transaction", rep.original_transaction_id], ["Order", rep.order_number], ["Seller ID", rep.seller_id?.slice(0, 8)], ["Customer ID", rep.customer_id], ["Original amount", money(rep.original_amount, m.currency)], ["Previously refunded", money(rep.previously_refunded, m.currency)], ["Requested now", money(rep.requested_amount, m.currency)]].map(([k, v]) => <div key={k as string} className="contents"><dt className="text-slate-500">{k}</dt><dd className="font-mono">{v}</dd></div>)}
            </dl>
            <div className="rounded border border-slate-200 p-3"><b className="text-xs">Deterministic validation</b>
              <ul className="mt-1 space-y-0.5 text-xs">{rep.checks.map((c: any) => <li key={c.name} className={c.passed ? "text-emerald-700" : "text-red-600"}>{c.passed ? "✓" : "✗"} {c.name.replaceAll("_", " ")} — <span className="text-slate-500">{c.detail}</span></li>)}</ul></div>
          </div>
          {rep.flags.length > 0 && <div className="rounded border border-slate-200 p-3"><b className="text-xs">Anomaly signals</b><ul className="mt-1 space-y-1 text-xs">{rep.flags.map((f: any) => <li key={f.code}><StatusBadge s={f.severity} /> <b>{f.code.replaceAll("_", " ")}</b>: {f.detail}</li>)}</ul></div>}
          <div><b className="text-xs">Refund history on this order</b><Table rows={rep.refund_history} empty="No other refunds on this order." cols={[{ h: "Refund", r: (x: any) => x.refund_id.slice(0, 8) }, { h: "Amount", r: (x: any) => money(x.amount, m.currency) }, { h: "Status", r: (x: any) => <StatusBadge s={x.status} /> }, { h: "When", r: (x: any) => when(x.created_at) }]} /></div>
          <p className="rounded bg-slate-50 p-2 text-xs"><b>Recommended action:</b> {inv.recommended_action}</p>
        </>}
        <ErrorBox error={approve.error ?? reject.error ?? escalate.error ?? rerun.error} />
        {open && <div className="flex flex-wrap justify-end gap-2 border-t border-slate-100 pt-3">
          {can("refunds.write") && <Button variant="outline" disabled={escalate.isPending || r.status === "escalated"} onClick={() => done(escalate.mutateAsync())}>Escalate investigation</Button>}
          {can("refunds.approve") && <><Button variant="danger" disabled={reject.isPending} onClick={() => done(reject.mutateAsync())}>Reject internal request</Button><Button disabled={approve.isPending} onClick={() => setConfirm(true)}>Approve refund…</Button></>}
        </div>}
        <Modal open={confirm} onClose={() => setConfirm(false)} title="Approve this refund?">
          <p className="text-sm">{r && <>Refund <b>{money(r.amount, r.currency)}</b> on order {r.order_number}. {m.mode === "demo" ? "Demo mode: a simulated refund will be recorded." : "This calls the PayPal Refunds API; PayPal's actual refund status decides the outcome."} Your approval is audit-logged.</>}</p>
          <div className="mt-4 flex justify-end gap-2"><Button variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button><Button disabled={approve.isPending} onClick={() => { setConfirm(false); done(approve.mutateAsync()); }}>Yes, approve refund</Button></div>
        </Modal>
      </div>}
    </Modal>
  );
}

export default function RefundGuard() {
  const { m } = useSession();
  const q = useQ<any>("/refunds");
  const [sel, setSel] = useState<string | null>(null);
  const [tab, setTab] = useState("queue");
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const st = q.data.stats, n = (k: string) => st[k]?.count ?? 0;
  const rows = q.data.refunds.filter((r: any) => tab === "all" || ["needs_review", "pending_approval", "escalated", "processing"].includes(r.status));
  return (
    <>
      <PageHead title="RefundGuard" sub="Validate every refund deterministically, surface anomalies, approve with a human in the loop" />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Total refunds" value={q.data.total} sub={money(q.data.total_amount, m.currency)} /><Stat label="Pending" value={n("pending")} sub={money(st.pending?.amount ?? 0, m.currency)} tone={n("pending") ? "warn" : undefined} />
        <Stat label="Completed" value={n("completed")} sub={money(st.completed?.amount ?? 0, m.currency)} /><Stat label="Failed" value={n("failed")} tone={n("failed") ? "bad" : undefined} /><Stat label="Rejected" value={n("rejected")} />
      </div>
      <Card className="mb-4"><CardHeader title="Refund trend" sub="Amount requested per day" /><div className="h-44 p-3"><ResponsiveContainer width="100%" height="100%"><BarChart data={q.data.trend}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="day" fontSize={11} /><YAxis fontSize={11} width={44} /><Tooltip formatter={(v) => money(Number(v), m.currency)} /><Bar dataKey="amount" fill="#F0721E" /></BarChart></ResponsiveContainer></div></Card>
      <Card>
        <CardHeader title={tab === "queue" ? "Investigation & approval queue" : "All refunds"} right={<div className="flex gap-1">{[["queue", "Needs action"], ["all", "All"]].map(([k, l]) => <Button key={k} size="sm" variant={tab === k ? "navy" : "outline"} onClick={() => setTab(k)}>{l}</Button>)}</div>} />
        <Table onRow={(r: any) => setSel(r.id)} rows={rows} empty="Nothing waiting for review."
          cols={[{ h: "Order", r: (r: any) => <b>{r.order_number}</b> }, { h: "Amount", r: (r: any) => money(r.amount, m.currency), right: true }, { h: "Status", r: (r: any) => <StatusBadge s={r.status} /> }, { h: "Risk", r: (r: any) => r.risk ? <StatusBadge s={r.risk} /> : <Badge>—</Badge> },
            { h: "PayPal refund", r: (r: any) => <span>{r.paypal_refund_id ?? "—"} {r.is_simulated && <SimBadge />}</span> }, { h: "Requested", r: (r: any) => when(r.created_at) }, { h: "", r: () => <span className="text-xs text-brand">Review →</span> }]} />
      </Card>
      {sel && <Investigation id={sel} onClose={() => setSel(null)} />}
    </>
  );
}
