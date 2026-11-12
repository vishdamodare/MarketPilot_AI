"use client";
import { useState } from "react";
import { Badge, Button, Card, ErrorBox, Field, Input, Loading, Modal, PageHead, Select, SimBadge, StatusBadge, Table, Tabs } from "@/components/ui";
import { api, money, uid, when } from "@/lib/api";
import { useAct, useQ, useSession } from "@/lib/session";

function NewOrder({ open, onClose }: { open: boolean; onClose: () => void }) {
  const products = useQ<any[]>(open ? "/products" : null);
  const [pid, setPid] = useState("");
  const [qty, setQty] = useState(1);
  const [email, setEmail] = useState("buyer@example.com");
  const create = useAct(() => api("/orders", { body: { customer_id: `CUST-${email.split("@")[0]}`, customer_email: email, address: "1 Market St", lines: [{ product_id: pid, qty }] } }));
  return (
    <Modal open={open} onClose={onClose} title="Create marketplace order">
      <form className="space-y-3" onSubmit={async (e) => { e.preventDefault(); await create.mutateAsync(); onClose(); }}>
        <Field label="Product" hint="Prices and seller allocation are computed on the server"><Select required value={pid} onChange={(e) => setPid(e.target.value)}><option value="">Select…</option>{products.data?.map((p) => <option key={p.id} value={p.id}>{p.seller} — {p.name} ({p.price})</option>)}</Select></Field>
        <Field label="Quantity"><Input type="number" min={1} value={qty} onChange={(e) => setQty(Number(e.target.value))} /></Field>
        <Field label="Customer email"><Input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
        <ErrorBox error={create.error} />
        <div className="flex justify-end gap-2"><Button type="button" variant="ghost" onClick={onClose}>Cancel</Button><Button disabled={!pid || create.isPending}>Create order</Button></div>
      </form>
    </Modal>
  );
}

function OrderDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { m, can } = useSession();
  const q = useQ<any>(`/orders/${id}`);
  const [key] = useState(uid);  // one idempotency key per dialog: double-clicks / retries cannot create a second refund
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const pay = useAct(() => api(`/orders/${id}/simulate-payment`, { body: {} }));
  const ppCreate = useAct(() => api<{ approve_url: string; paypal_order_id: string; mode: string; note: string }>("/paypal/orders", { body: { order_id: id } }));
  const ppCapture = useAct((poid: string) => api(`/paypal/orders/${poid}/capture`, { body: {} }));
  const refund = useAct(() => api("/refunds", { body: { order_id: id, amount, reason, idempotency_key: key } }));
  const o = q.data?.order;
  const err: any = refund.error;
  return (
    <Modal open onClose={onClose} title={o ? `Order ${o.number}` : "Order"} wide>
      {!o ? <Loading /> : <div className="space-y-4 text-sm">
        <div className="flex flex-wrap items-center gap-3"><span className="text-lg font-semibold">{money(o.total, o.currency)}</span><StatusBadge s={o.status} />{q.data.captures[0]?.is_simulated && <SimBadge />}<span className="text-slate-500">{o.customer_email}</span></div>
        <Table rows={q.data.allocations} cols={[{ h: "Seller", r: (r: any) => r.seller }, { h: "Gross", r: (r: any) => money(r.gross, o.currency), right: true }, { h: "Commission", r: (r: any) => money(r.commission, o.currency), right: true }, { h: "Seller net", r: (r: any) => money(r.net, o.currency), right: true }]} />
        <div>Captures: {q.data.captures.length ? q.data.captures.map((c: any) => <Badge key={c.id} className="mr-1">{c.capture_id} · {c.status}</Badge>) : <span className="text-slate-500">none (payment not captured)</span>}</div>
        {o.status === "created" && can("orders.write") && (
          <div className="space-y-2 rounded border border-slate-200 p-3">
            <b>Collect payment</b>
            {m.mode === "demo" ? <div><Button variant="navy" size="sm" disabled={pay.isPending} onClick={() => pay.mutate()}>Record simulated payment</Button><p className="mt-1 text-xs text-slate-500">Creates a clearly labelled SIMULATED capture. Nothing is sent to PayPal.</p></div> : <>
              <Button size="sm" disabled={ppCreate.isPending} onClick={() => ppCreate.mutate()}>Create PayPal Sandbox order</Button>
              {ppCreate.data && <div className="space-y-1 text-xs"><p>{ppCreate.data.note} Mode: <b>{ppCreate.data.mode.replace("_", " ")}</b></p><a className="text-brand underline" href={ppCreate.data.approve_url} target="_blank" rel="noreferrer">Approve as sandbox buyer ↗</a><div><Button size="sm" variant="outline" onClick={() => ppCapture.mutate(ppCreate.data!.paypal_order_id)}>Capture after approval</Button></div></div>}
            </>}
            <ErrorBox error={pay.error ?? ppCreate.error ?? ppCapture.error} />
          </div>)}
        {["paid", "refunded"].includes(o.status) && can("refunds.write") && (
          <form className="space-y-2 rounded border border-slate-200 p-3" onSubmit={(e) => { e.preventDefault(); refund.mutate(); }}>
            <b>Request refund</b> <span className="text-xs text-slate-500">(creates a request; a merchant approves it in RefundGuard)</span>
            <div className="flex gap-2"><Input aria-label="Refund amount" placeholder="Amount" type="number" step="0.01" min="0.01" required value={amount} onChange={(e) => setAmount(e.target.value)} className="max-w-32" /><Input aria-label="Reason" placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} /><Button disabled={refund.isPending}>Submit</Button></div>
            {refund.isSuccess && <p role="status" className="text-emerald-700">Refund request created ({(refund.data as any).status.replace("_", " ")}).</p>}
            {err && <div role="alert" className="text-red-600">{err.message}{err.detail?.checks && <ul className="mt-1 list-disc pl-4 text-xs">{err.detail.checks.filter((c: any) => !c.passed).map((c: any) => <li key={c.name}>{c.detail}</li>)}</ul>}</div>}
          </form>)}
        {q.data.refunds.length > 0 && <div><b>Refunds:</b> {q.data.refunds.map((r: any) => <Badge key={r.id} className="mr-1">{money(r.amount, o.currency)} · {r.status}</Badge>)}</div>}
      </div>}
    </Modal>
  );
}

export default function Transactions() {
  const { can, m } = useSession();
  const [tab, setTab] = useState("orders");
  const [sel, setSel] = useState<string | null>(null);
  const [neu, setNeu] = useState(false);
  const orders = useQ<any[]>("/orders");
  const tx = useQ<any[]>(tab === "ledger" ? "/transactions" : null);
  return (
    <>
      <PageHead title="Transactions" sub="Marketplace orders, PayPal captures and internal transaction records" right={can("orders.write") && <Button onClick={() => setNeu(true)}>New order</Button>} />
      <Tabs tabs={[["orders", "Orders & payments"], ["ledger", "Transaction records"]]} value={tab} onChange={setTab} />
      <Card>
        {tab === "orders" ? (orders.isLoading ? <Loading /> : <Table onRow={(r: any) => setSel(r.id)} rows={orders.data ?? []} empty="No orders yet. Create one to start."
          cols={[{ h: "Order", r: (r: any) => <span className="font-medium">{r.number}</span> }, { h: "Customer", r: (r: any) => r.customer_id }, { h: "Total", r: (r: any) => money(r.total, m.currency), right: true }, { h: "Status", r: (r: any) => <StatusBadge s={r.status} /> },
            { h: "PayPal capture", r: (r: any) => r.capture_id ? <span><code className="text-xs">{r.capture_id}</code> {r.simulated && <SimBadge />}</span> : "—" }, { h: "Created", r: (r: any) => when(r.created_at) }]} />)
          : <Table rows={tx.data ?? []} cols={[{ h: "Type", r: (r: any) => <Badge>{r.type}</Badge> }, { h: "Amount", r: (r: any) => money(r.amount, m.currency), right: true }, { h: "Status", r: (r: any) => <StatusBadge s={r.status} /> }, { h: "External ref", r: (r: any) => <span><code className="text-xs">{r.external_ref ?? "—"}</code> {r.is_simulated && <SimBadge />}</span> }, { h: "Date", r: (r: any) => when(r.created_at) }]} />}
      </Card>
      <NewOrder open={neu} onClose={() => setNeu(false)} />
      {sel && <OrderDetail id={sel} onClose={() => setSel(null)} />}
    </>
  );
}
