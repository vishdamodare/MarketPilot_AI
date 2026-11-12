"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Badge, Button, Card, ErrorBox, Field, Input, Loading, Modal, PageHead, Select, SimBadge, Stat, StatusBadge, Table } from "@/components/ui";
import { api, money } from "@/lib/api";
import { useAct, useQ, useSession } from "@/lib/session";

const schema = z.object({
  business_name: z.string().min(1, "Business name is required"),
  email: z.string().email("Enter a valid email"),
  commission_pct: z.string().optional().refine((v) => !v || (Number(v) >= 0 && Number(v) <= 100), "0–100"),
});

function AddSeller({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { can } = useSession();
  const f = useForm<z.infer<typeof schema>>({ resolver: zodResolver(schema) });
  const add = useAct((v: z.infer<typeof schema>) => api("/sellers", { body: { ...v, commission_pct: v.commission_pct || null } }));
  return (
    <Modal open={open} onClose={onClose} title="Add seller">
      <form className="space-y-3" noValidate onSubmit={f.handleSubmit(async (v) => { await add.mutateAsync(v); f.reset(); onClose(); })}>
        <Field label="Business name" error={f.formState.errors.business_name?.message}><Input {...f.register("business_name")} /></Field>
        <Field label="Seller email" error={f.formState.errors.email?.message}><Input type="email" {...f.register("email")} /></Field>
        {can("commission.write") && <Field label="Seller-specific commission % (optional)" hint="Leave blank to use the marketplace default" error={f.formState.errors.commission_pct?.message}><Input type="number" step="0.01" {...f.register("commission_pct")} /></Field>}
        <ErrorBox error={add.error} />
        <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button type="submit" disabled={add.isPending}>Add seller</Button></div>
      </form>
    </Modal>
  );
}

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const { can, m } = useSession();
  const q = useQ<any>(`/sellers/${id}`);
  const [pct, setPct] = useState("");
  const patch = useAct((b: object) => api(`/sellers/${id}`, { method: "PATCH", body: b }));
  const onboard = useAct((simulate: boolean) => api(`/sellers/${id}/onboard?simulate=${simulate}`, { body: {} }));
  const s = q.data?.seller;
  return (
    <Modal open onClose={onClose} title={s?.business_name ?? "Seller"} wide>
      {!q.data ? <Loading /> : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Ledger balance owed" value={money(s.balance, m.currency)} />
            <Stat label="Gross sales" value={money(q.data.summary.payments.amount, m.currency)} />
            <Stat label="Commission paid" value={money(q.data.summary.commissions.amount, m.currency)} />
            <Stat label="Paid out" value={money(q.data.summary.payouts.amount, m.currency)} />
          </div>
          <div className="grid gap-3 text-sm sm:grid-cols-2">
            <div className="space-y-1.5 rounded border border-slate-200 p-3">
              <div>Status: <StatusBadge s={s.status} /> {can("sellers.write") && <Select aria-label="Change status" className="ml-2 !w-auto !py-0.5 text-xs" value={s.status} onChange={(e) => patch.mutate({ status: e.target.value })}>{["pending", "active", "suspended"].map((x) => <option key={x}>{x}</option>)}</Select>}</div>
              <div>Onboarding: <StatusBadge s={s.onboarding_status} /></div>
              <div>PayPal merchant ID: <code className="text-xs">{s.paypal_merchant_id ?? "not connected"}</code> {q.data.paypal_accounts.some((a: any) => a.is_simulated) && <SimBadge />}</div>
              <div>Email: {s.email}</div>
            </div>
            <div className="space-y-2 rounded border border-slate-200 p-3">
              <div>Effective commission: <b>{s.effective_commission_pct}%</b>{s.commission_pct === null && <span className="text-xs text-slate-500"> (marketplace default)</span>}</div>
              {can("commission.write") && <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); patch.mutate({ commission_pct: pct || null }); }}><Input aria-label="Commission %" type="number" step="0.01" placeholder="Override %" value={pct} onChange={(e) => setPct(e.target.value)} /><Button size="sm" variant="navy" type="submit">Save</Button></form>}
              {can("sellers.write") && <div className="flex flex-wrap gap-2">
                <Button size="sm" variant="outline" disabled={onboard.isPending} onClick={() => onboard.mutate(m.mode === "demo")}>{m.mode === "demo" ? "Simulate onboarding" : "Start PayPal onboarding"}</Button>
                {m.mode === "sandbox" && <Button size="sm" variant="ghost" onClick={() => onboard.mutate(true)}>Simulate instead</Button>}
              </div>}
              {onboard.data && <p role="status" className="text-xs text-slate-600">{(onboard.data as any).message ?? ((onboard.data as any).action_url && <a className="text-brand underline" href={(onboard.data as any).action_url} target="_blank" rel="noreferrer">Open PayPal onboarding link</a>)}</p>}
            </div>
          </div>
          <ErrorBox error={patch.error ?? onboard.error} />
          <div><h3 className="mb-1 text-sm font-semibold">Order allocations</h3>
            <Table cols={[{ h: "Order", r: (r: any) => r.order_number }, { h: "Gross", r: (r: any) => money(r.gross, m.currency), right: true }, { h: "Commission", r: (r: any) => money(r.commission, m.currency), right: true }, { h: "Seller net", r: (r: any) => money(r.net, m.currency), right: true }]} rows={q.data.transactions} empty="No orders yet." /></div>
          <div><h3 className="mb-1 text-sm font-semibold">Payouts</h3>
            <Table cols={[{ h: "Amount", r: (r: any) => money(r.amount, m.currency) }, { h: "Status", r: (r: any) => <StatusBadge s={r.status} /> }, { h: "Reference", r: (r: any) => r.reference ?? "—" }]} rows={q.data.payouts} empty="No payouts yet." /></div>
        </div>
      )}
    </Modal>
  );
}

export default function Sellers() {
  const { can, m } = useSession();
  const q = useQ<any>("/sellers");
  const [add, setAdd] = useState(false);
  const [sel, setSel] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const rows = q.data.sellers.filter((s: any) => s.business_name.toLowerCase().includes(filter.toLowerCase()));
  return (
    <>
      <PageHead title="Sellers" sub="Directory, onboarding status and ledger balances" right={can("sellers.write") && <Button onClick={() => setAdd(true)}>Add seller</Button>} />
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Total sellers" value={q.data.total} /><Stat label="Active" value={q.data.active} />
        <Stat label="Pending onboarding" value={q.data.pending} tone={q.data.pending ? "warn" : undefined} /><Stat label="Suspended" value={q.data.suspended} />
      </div>
      <Card>
        <div className="border-b border-slate-100 p-3"><Input aria-label="Filter sellers" placeholder="Filter by name…" value={filter} onChange={(e) => setFilter(e.target.value)} className="max-w-xs" /></div>
        <Table onRow={(r: any) => setSel(r.id)} rows={rows} empty="No sellers match."
          cols={[{ h: "Seller", r: (r: any) => <span className="font-medium">{r.business_name}</span> }, { h: "Status", r: (r: any) => <StatusBadge s={r.status} /> }, { h: "Onboarding", r: (r: any) => <StatusBadge s={r.onboarding_status} /> },
            { h: "PayPal merchant", r: (r: any) => r.paypal_merchant_id ? <code className="text-xs">{r.paypal_merchant_id}</code> : <Badge>none</Badge> }, { h: "Commission", r: (r: any) => `${r.effective_commission_pct}%`, right: true }, { h: "Balance owed", r: (r: any) => money(r.balance, m.currency), right: true }]} />
      </Card>
      <AddSeller open={add} onClose={() => setAdd(false)} />
      {sel && <Detail id={sel} onClose={() => setSel(null)} />}
    </>
  );
}
