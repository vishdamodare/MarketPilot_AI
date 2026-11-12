"use client";
import { useState } from "react";
import { Button, Card, CardHeader, ErrorBox, Field, Input, Loading, Modal, PageHead, Select, SimBadge, Stat, StatusBadge, Table } from "@/components/ui";
import { api, money, when } from "@/lib/api";
import { useAct, useQ, useSession } from "@/lib/session";

export default function SmartSplit() {
  const { m, can } = useSession();
  const q = useQ<any>("/smartsplit");
  const sellers = useQ<any>("/sellers");
  const [pct, setPct] = useState("");
  const [seller, setSeller] = useState("");
  const [confirm, setConfirm] = useState<any>(null);
  const setDefault = useAct(() => api("/org", { method: "PATCH", body: { default_commission_pct: pct } }));
  const request = useAct(() => api("/payouts/request", { body: { seller_id: seller } }));
  const approve = useAct((id: string) => api(`/payouts/${id}/approve`, { body: {} }));
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const st = q.data.settlements;
  return (
    <>
      <PageHead title="SmartSplit" sub="Commission rules, seller allocations, settlements and payouts. Settlement ≠ payout: each is its own record." />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Default commission" value={`${q.data.default_commission_pct}%`} />
        <Stat label="Pending settlements" value={money(st.pending?.amount ?? 0, m.currency)} sub={`${st.pending?.count ?? 0} allocations awaiting payout`} tone="warn" />
        <Stat label="Settled" value={money(st.settled?.amount ?? 0, m.currency)} sub={`${st.settled?.count ?? 0} allocations`} />
        <Stat label="Payouts awaiting approval" value={q.data.payouts.filter((p: any) => p.status === "pending_approval").length} />
      </div>
      {can("commission.write") && <Card className="mb-4 p-4"><form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); setDefault.mutate(); }}>
        <Field label="Marketplace default commission (%)" hint="Seller-specific overrides are set on the Sellers page"><Input type="number" step="0.01" min="0" max="100" value={pct} onChange={(e) => setPct(e.target.value)} placeholder={q.data.default_commission_pct} /></Field>
        <Button variant="navy" disabled={!pct || setDefault.isPending}>Update default</Button>{setDefault.isSuccess && <span role="status" className="text-sm text-emerald-700">Saved (applies to new orders)</span>}<ErrorBox error={setDefault.error} /></form></Card>}
      <Card className="mb-4">
        <CardHeader title="Payouts" sub="Money leaves only after explicit approval" right={can("payouts.approve") && <div className="flex gap-2">
          <Select aria-label="Seller to pay" className="!w-48 !py-1 text-xs" value={seller} onChange={(e) => setSeller(e.target.value)}><option value="">Select seller…</option>{sellers.data?.sellers.filter((s: any) => Number(s.balance) > 0).map((s: any) => <option key={s.id} value={s.id}>{s.business_name} ({money(s.balance, m.currency)})</option>)}</Select>
          <Button size="sm" disabled={!seller || request.isPending} onClick={() => request.mutate()}>Request payout</Button></div>} />
        <ErrorBox error={request.error ?? approve.error} />
        <Table rows={q.data.payouts} empty="No payouts yet." cols={[{ h: "Seller", r: (r: any) => r.seller }, { h: "Amount", r: (r: any) => money(r.amount, m.currency), right: true }, { h: "Adjustment", r: (r: any) => Number(r.adjustment) ? money(r.adjustment, m.currency) : "—", right: true }, { h: "Status", r: (r: any) => <StatusBadge s={r.status} /> },
          { h: "Reference", r: (r: any) => <span>{r.reference ?? "—"} {r.is_simulated && <SimBadge />}</span> }, { h: "Created", r: (r: any) => when(r.created_at) },
          { h: "", r: (r: any) => r.status === "pending_approval" && can("payouts.approve") ? <Button size="sm" variant="navy" onClick={() => setConfirm(r)}>Approve…</Button> : null }]} />
      </Card>
      <Card><CardHeader title="Seller allocations" sub="Gross → commission → seller net, per order (latest 100)" />
        <Table rows={q.data.allocations} cols={[{ h: "Order", r: (r: any) => r.order_number }, { h: "Seller", r: (r: any) => r.seller }, { h: "Gross", r: (r: any) => money(r.gross, m.currency), right: true }, { h: "Commission", r: (r: any) => money(r.commission, m.currency), right: true }, { h: "Seller net", r: (r: any) => money(r.net, m.currency), right: true }]} /></Card>
      <Modal open={!!confirm} onClose={() => setConfirm(null)} title="Approve payout">
        {confirm && <div className="space-y-3 text-sm"><p>Pay <b>{money(confirm.amount, m.currency)}</b> to <b>{confirm.seller}</b>? {m.mode === "demo" ? "This is a simulated payout (demo mode)." : "This sends a PayPal Payout from your account."} The approval is recorded in the audit log under your name.</p>
          <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button><Button disabled={approve.isPending} onClick={async () => { await approve.mutateAsync(confirm.id).catch(() => null); setConfirm(null); }}>Approve payout</Button></div></div>}
      </Modal>
    </>
  );
}
