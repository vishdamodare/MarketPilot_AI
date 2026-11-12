"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Badge, Button, Card, ErrorBox, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import { SessionProvider, useAct, useQ, useSession } from "@/lib/session";

const schema = z.object({
  name: z.string().min(1, "Required"),
  marketplace_type: z.string().min(1),
  currency: z.string().regex(/^[A-Z]{3}$/, "3-letter ISO code"),
  default_commission_pct: z.coerce.number().min(0, "0–100").max(100, "0–100"),
});
type Form = z.infer<typeof schema>;
const STEPS = ["Organization", "Marketplace & commission", "PayPal Sandbox", "Sample data"];

function Wizard() {
  const { m, switchOrg } = useSession();
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [err, setErr] = useState<unknown>(null);
  const f = useForm({ resolver: zodResolver(schema), defaultValues: { name: m.org_name, marketplace_type: "ecommerce", currency: m.currency, default_commission_pct: 10 } });
  const pp = useQ<{ configured: boolean; environment: string; last_error: string | null }>("/integrations/paypal/status");
  const test = useAct(() => api<{ ok: boolean; error?: string }>("/integrations/paypal/test", { body: {} }));
  const save = useAct((v: Form) => api("/org", { method: "PATCH", body: v }));
  const seed = useAct(() => api("/demo/seed", { body: {} }));
  const mkDemo = useAct(() => api<{ org_id: string }>("/orgs", { body: { name: `${m.org_name} (Demo)`, mode: "demo" } }));

  const next = async () => {
    setErr(null);
    if (step === 1) { if (!(await f.trigger())) return; try { await save.mutateAsync(schema.parse(f.getValues())); } catch (e) { return setErr(e); } }
    setStep(step + 1);
  };
  return (
    <div className="mx-auto max-w-2xl px-4 py-10">
      <h1 className="text-xl font-semibold">Set up {m.org_name}</h1>
      <ol className="my-5 flex gap-2 text-xs">{STEPS.map((s, i) => <li key={s} className={`flex-1 rounded px-2 py-1.5 text-center ${i === step ? "bg-navy text-white" : i < step ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-500"}`}>{i + 1}. {s}</li>)}</ol>
      <Card className="space-y-4 p-5">
        {step === 0 && <>
          <Field label="Organization name" error={f.formState.errors.name?.message}><Input {...f.register("name")} /></Field>
          <p className="text-sm text-slate-500">Workspace mode: <Badge tone={m.mode === "demo" ? "orange" : "blue"}>{m.mode}</Badge> (fixed at creation so demo and sandbox records can never mix).</p>
        </>}
        {step === 1 && <>
          <Field label="Marketplace type"><Select {...f.register("marketplace_type")}>{["ecommerce", "travel", "digital products", "saas", "services"].map((x) => <option key={x}>{x}</option>)}</Select></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Currency" error={f.formState.errors.currency?.message}><Select {...f.register("currency")}>{["USD", "EUR", "GBP", "INR", "AUD", "CAD"].map((x) => <option key={x}>{x}</option>)}</Select></Field>
            <Field label="Default commission (%)" error={f.formState.errors.default_commission_pct?.message}><Input type="number" step="0.01" {...f.register("default_commission_pct")} /></Field>
          </div>
        </>}
        {step === 2 && <>
          {m.mode === "demo" ? <p className="text-sm text-slate-600">Demo workspaces never call PayPal. Create a Sandbox workspace later to connect real sandbox credentials.</p> : <>
            <p className="text-sm text-slate-600">Credentials live in the backend environment (<code>PAYPAL_CLIENT_ID</code>, <code>PAYPAL_CLIENT_SECRET</code>), never in the browser.</p>
            <p className="text-sm">Status: {pp.data?.configured ? <Badge tone="green">credentials found ({pp.data.environment})</Badge> : <Badge tone="amber">not configured</Badge>}</p>
            <Button variant="outline" disabled={!pp.data?.configured || test.isPending} onClick={() => test.mutate()}>Test connection</Button>
            {test.data && <p role="status" className={`text-sm ${test.data.ok ? "text-emerald-700" : "text-red-600"}`}>{test.data.ok ? "Connected to PayPal." : test.data.error}</p>}
          </>}
        </>}
        {step === 3 && <>
          {m.mode === "demo" ? <>
            <p className="text-sm text-slate-600">Your demo workspace is already seeded: 25 sellers, 120 orders, 5 disputes, 3 duplicate refund requests, 4 unreconciled records, 2 payout mismatches. All simulated.</p>
            <Button variant="outline" disabled={seed.isPending} onClick={() => seed.mutate()}>{seed.isPending ? "Reseeding…" : "Reset demo data"}</Button>
            {seed.data && <p role="status" className="text-sm text-emerald-700">Demo reset. Seller login for the portal: <code>{(seed.data as any).seller_login.email}</code> / <code>{(seed.data as any).seller_login.password}</code></p>}
          </> : <>
            <p className="text-sm text-slate-600">Sandbox workspaces start empty and only hold real sandbox records. Want to explore first? Create a separate demo workspace.</p>
            <Button variant="outline" disabled={mkDemo.isPending} onClick={async () => { const r = await mkDemo.mutateAsync(); switchOrg(r.org_id); router.replace("/overview"); }}>{mkDemo.isPending ? "Creating demo…" : "Create a demo workspace"}</Button>
          </>}
        </>}
        <ErrorBox error={err ?? test.error ?? seed.error ?? mkDemo.error} />
        <div className="flex justify-between pt-2">
          <Button variant="ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</Button>
          {step < 3 ? <Button onClick={next}>Continue</Button> : <Button onClick={() => router.replace("/overview")}>Go to dashboard</Button>}
        </div>
      </Card>
    </div>
  );
}

export default function Page() {
  return <SessionProvider><Wizard /></SessionProvider>;
}
