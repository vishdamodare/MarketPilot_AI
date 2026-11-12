"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Badge, Button, Card, CardHeader, ErrorBox, Field, Input, Loading, PageHead, Select, Table, Tabs } from "@/components/ui";
import { api, when } from "@/lib/api";
import { useAct, useQ, useSession } from "@/lib/session";

const orgSchema = z.object({ name: z.string().min(1), currency: z.string().regex(/^[A-Z]{3}$/), default_commission_pct: z.coerce.number().min(0).max(100) });
const memberSchema = z.object({ name: z.string().min(1, "Required"), email: z.string().email("Valid email"), password: z.string().min(10, "10+ characters"), role: z.enum(["admin", "finance_manager"]) });

function Org() {
  const { m } = useSession();
  const org = useQ<any>("/org");
  const f = useForm({ resolver: zodResolver(orgSchema), values: org.data && { name: org.data.name, currency: org.data.currency, default_commission_pct: Number(org.data.default_commission_pct) } });
  const save = useAct((v: z.infer<typeof orgSchema>) => api("/org", { method: "PATCH", body: v }));
  const seed = useAct(() => api("/demo/seed", { body: {} }));
  if (!org.data) return <Loading />;
  return (
    <div className="space-y-4">
      <Card className="max-w-xl p-4"><form className="space-y-3" onSubmit={f.handleSubmit((v) => save.mutate(v as any))}>
        <Field label="Organization name" error={f.formState.errors.name?.message as string | undefined}><Input {...f.register("name")} /></Field>
        <div className="grid grid-cols-2 gap-3"><Field label="Currency"><Select {...f.register("currency")}>{["USD", "EUR", "GBP", "INR", "AUD", "CAD"].map((c) => <option key={c}>{c}</option>)}</Select></Field>
          <Field label="Default commission %" error={f.formState.errors.default_commission_pct?.message as string | undefined}><Input type="number" step="0.01" {...f.register("default_commission_pct")} /></Field></div>
        <p className="text-xs text-slate-500">Mode <Badge tone={m.mode === "demo" ? "orange" : "blue"}>{m.mode}</Badge> is fixed for this workspace so demo and PayPal Sandbox data never mix. Changing the commission is audit-logged and applies to new orders only.</p>
        <ErrorBox error={save.error} />{save.isSuccess && <p role="status" className="text-sm text-emerald-700">Saved.</p>}<Button disabled={save.isPending}>Save changes</Button></form></Card>
      {m.mode === "demo" && <Card className="max-w-xl p-4"><b className="text-sm">Demo scenario</b><p className="my-1 text-xs text-slate-500">Wipes this demo workspace and reloads “Marketplace Financial Crisis” (all simulated).</p>
        <Button variant="outline" disabled={seed.isPending} onClick={() => seed.mutate()}>{seed.isPending ? "Resetting…" : "Reset demo data"}</Button>
        {seed.data && <p role="status" className="mt-2 text-xs text-emerald-700">Reset complete. Seller portal login: <code>{(seed.data as any).seller_login.email}</code> / <code>{(seed.data as any).seller_login.password}</code></p>}<ErrorBox error={seed.error} /></Card>}
    </div>
  );
}

function Team() {
  const q = useQ<any[]>("/team");
  const f = useForm<z.infer<typeof memberSchema>>({ resolver: zodResolver(memberSchema), defaultValues: { role: "finance_manager" } });
  const add = useAct((v: z.infer<typeof memberSchema>) => api("/team", { body: v }));
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card><CardHeader title="Members" /><Table rows={q.data ?? []} cols={[{ h: "Name", r: (r: any) => r.name }, { h: "Email", r: (r: any) => r.email }, { h: "Role", r: (r: any) => <Badge>{r.role.replace("_", " ")}</Badge> }]} /></Card>
      <Card className="p-4"><b className="text-sm">Add team member</b><form className="mt-2 space-y-3" onSubmit={f.handleSubmit(async (v) => { await add.mutateAsync(v); f.reset(); })}>
        <Field label="Name" error={f.formState.errors.name?.message}><Input {...f.register("name")} /></Field><Field label="Email" error={f.formState.errors.email?.message}><Input type="email" {...f.register("email")} /></Field>
        <Field label="Initial password" error={f.formState.errors.password?.message}><Input type="password" autoComplete="new-password" {...f.register("password")} /></Field>
        <Field label="Role"><Select {...f.register("role")}><option value="finance_manager">Finance manager</option><option value="admin">Admin</option></Select></Field>
        <ErrorBox error={add.error} /><Button disabled={add.isPending}>Add member</Button></form></Card>
    </div>
  );
}

function Prefs() {
  const q = useQ<Record<string, boolean>>("/settings/notifications");
  const save = useAct((v: Record<string, boolean>) => api("/settings/notifications", { method: "PUT", body: v }));
  const items: [string, string][] = [["refund_flags", "Refunds flagged for review"], ["dispute_deadlines", "Dispute deadlines within 48 hours"], ["recon_mismatches", "Reconciliation mismatches"], ["payout_issues", "Payout failures or delays"]];
  if (!q.data) return <Loading />;
  return <Card className="max-w-xl p-4"><ul className="space-y-3">{items.map(([k, l]) => <li key={k}><label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!q.data![k]} onChange={(e) => save.mutate({ ...q.data!, [k]: e.target.checked })} />{l}</label></li>)}</ul>
    <p className="mt-3 text-xs text-slate-500">In-app notifications are always stored. Email delivery requires SMTP configuration on the backend (not configured by default).</p><ErrorBox error={save.error} /></Card>;
}

function Audit() {
  const q = useQ<any[]>("/audit");
  return <Card><CardHeader title="Audit history" sub="Append-only record of sensitive actions, including every AI tool call" /><Table rows={q.data ?? []} cols={[{ h: "When", r: (r: any) => when(r.created_at) }, { h: "Actor", r: (r: any) => <Badge tone={r.actor_type === "ai" ? "orange" : "gray"}>{r.actor_type}</Badge> }, { h: "Action", r: (r: any) => <code className="text-xs">{r.action}</code> }, { h: "Entity", r: (r: any) => r.entity ? `${r.entity} ${r.entity_id?.slice(0, 8) ?? ""}` : "—" }, { h: "Details", r: (r: any) => <span className="text-xs text-slate-500">{r.details ? JSON.stringify(r.details).slice(0, 90) : ""}</span> }]} /></Card>;
}

function Developers() {
  const q = useQ<any[]>("/settings/api-keys");
  const [name, setName] = useState("");
  const [fresh, setFresh] = useState<any>(null);
  const create = useAct((n: string) => api<any>("/settings/api-keys", { body: { name: n } }));
  const revoke = useAct((id: string) => api(`/settings/api-keys/${id}`, { method: "DELETE" }));
  const base = typeof window === "undefined" ? "" : window.location.origin;
  return (
    <div className="space-y-4">
      <Card className="max-w-3xl p-4">
        <b className="text-sm">Storefront API keys</b>
        <p className="mb-3 mt-1 text-xs text-slate-500">Your store sends orders, shipments and customer messages with <code>Authorization: Bearer &lt;key&gt;</code>. Keys are stored hashed and shown once.</p>
        <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); setFresh(await create.mutateAsync(name)); setName(""); }}>
          <Input aria-label="Key name" placeholder="e.g. Production storefront" required value={name} onChange={(e) => setName(e.target.value)} />
          <Button disabled={create.isPending || !name.trim()}>Create key</Button>
        </form>
        <ErrorBox error={create.error ?? revoke.error} />
        {fresh && (
          <div role="status" className="mt-3 rounded border border-amber-300 bg-amber-50 p-3 text-xs">
            <b>Copy this key now — it will not be shown again.</b>
            <div className="mt-1 flex items-center gap-2"><code data-testid="new-key" className="break-all rounded bg-white px-2 py-1">{fresh.key}</code>
              <Button size="sm" variant="outline" type="button" onClick={() => navigator.clipboard?.writeText(fresh.key)}>Copy</Button></div>
            <pre className="mt-2 overflow-x-auto rounded bg-navy p-2 text-[11px] text-slate-100">{`curl -X POST ${base}/api/store/v1/orders \\
  -H "Authorization: Bearer ${fresh.key}" -H "Content-Type: application/json" \\
  -d '{"external_id":"SHOP-1001","customer_id":"c1","customer_email":"buyer@example.com",
       "lines":[{"product_id":"<product id from GET /api/store/v1/products>","qty":1}]}'`}</pre>
          </div>
        )}
      </Card>
      <Card><CardHeader title="Keys" /><Table rows={q.data ?? []} empty="No API keys yet." cols={[
        { h: "Name", r: (r: any) => r.name }, { h: "Key", r: (r: any) => <code className="text-xs">{r.prefix}…</code> }, { h: "Last used", r: (r: any) => when(r.last_used_at) },
        { h: "Status", r: (r: any) => r.revoked_at ? <Badge tone="red">revoked</Badge> : <Badge tone="green">active</Badge> },
        { h: "", r: (r: any) => !r.revoked_at && <Button size="sm" variant="danger" disabled={revoke.isPending} onClick={() => revoke.mutate(r.id)}>Revoke</Button> }]} /></Card>
    </div>
  );
}

const Security = () => (
  <Card className="max-w-2xl p-4 text-sm"><ul className="list-disc space-y-1.5 pl-5 text-slate-700">
    <li>Sessions use an httpOnly, SameSite=Lax cookie; no tokens are readable by JavaScript.</li><li>Every API call verifies organization membership and role permission on the server; mutating calls also require a CSRF header.</li>
    <li>Passwords are hashed with bcrypt (10+ characters required). Login, signup and reset are rate-limited.</li><li>PayPal credentials exist only in backend environment variables.</li>
    <li>Refunds, payouts, dispute submissions and commission changes need explicit human approval and are audit-logged with the approver.</li></ul></Card>
);

export default function Settings() {
  const [tab, setTab] = useState("org");
  return (<><PageHead title="Settings" /><Tabs tabs={[["org", "Organization"], ["team", "Team"], ["security", "Security"], ["notif", "Notification preferences"], ["audit", "Audit history"], ["dev", "Developers"]]} value={tab} onChange={setTab} />
    {tab === "org" && <Org />}{tab === "team" && <Team />}{tab === "security" && <Security />}{tab === "notif" && <Prefs />}{tab === "audit" && <Audit />}{tab === "dev" && <Developers />}</>);
}
