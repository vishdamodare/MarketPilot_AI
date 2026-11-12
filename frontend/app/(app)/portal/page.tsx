"use client";
import { Card, CardHeader, ErrorBox, Loading, PageHead, Stat, StatusBadge, Table } from "@/components/ui";
import { money, when } from "@/lib/api";
import { useQ } from "@/lib/session";

export default function Portal() {
  const q = useQ<any>("/portal");
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const p = q.data, c = p.currency;
  return (
    <>
      <PageHead title={p.seller.business_name} sub={`Seller portal · commission ${p.seller.commission_pct}% · status ${p.seller.status}`} />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Balance owed to you" value={money(p.balance, c)} /><Stat label="Gross sales" value={money(p.summary.payments.amount, c)} />
        <Stat label="Commission deducted" value={money(p.summary.commissions.amount, c)} /><Stat label="Paid out" value={money(p.summary.payouts.amount, c)} />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card><CardHeader title="Transactions & commission" /><Table rows={p.transactions} empty="No transactions yet." cols={[{ h: "Order", r: (r: any) => r.order_number }, { h: "Gross", r: (r: any) => money(r.gross, c), right: true }, { h: "Commission", r: (r: any) => money(r.commission, c), right: true }, { h: "Your net", r: (r: any) => money(r.net, c), right: true }]} /></Card>
        <Card><CardHeader title="Payout history" /><Table rows={p.payouts} empty="No payouts yet." cols={[{ h: "Amount", r: (r: any) => money(r.amount, c) }, { h: "Status", r: (r: any) => <StatusBadge s={r.status} /> }, { h: "Reference", r: (r: any) => r.reference ?? "—" }, { h: "Date", r: (r: any) => when(r.at) }]} /></Card>
        <Card><CardHeader title="Refunds" /><Table rows={p.refunds} empty="No refunds." cols={[{ h: "Amount", r: (r: any) => money(r.amount, c) }, { h: "Status", r: (r: any) => <StatusBadge s={r.status} /> }, { h: "Date", r: (r: any) => when(r.at) }]} /></Card>
        <Card><CardHeader title="Disputes" sub="Information only; the marketplace responds on your behalf" /><Table rows={p.disputes} empty="No disputes." cols={[{ h: "Dispute", r: (r: any) => r.id }, { h: "Reason", r: (r: any) => r.reason.replaceAll("_", " ").toLowerCase() }, { h: "Amount", r: (r: any) => money(r.amount, c) }, { h: "Status", r: (r: any) => <StatusBadge s={r.status} /> }]} /></Card>
      </div>
    </>
  );
}
