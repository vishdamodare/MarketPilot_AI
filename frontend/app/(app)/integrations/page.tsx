"use client";
import { CheckCircle2, CircleAlert, CircleOff } from "lucide-react";
import { useState } from "react";
import { Badge, Button, Card, CardHeader, ErrorBox, Loading, PageHead, Table } from "@/components/ui";
import { api, when } from "@/lib/api";
import { useAct, useQ, useSession } from "@/lib/session";

export default function Integrations() {
  const { m, can } = useSession();
  const q = useQ<any>("/integrations/paypal/status");
  const events = useQ<any[]>(can("integrations.manage") && m.mode === "sandbox" ? "/webhooks/events" : null);
  const test = useAct(() => api<{ ok: boolean; error?: string }>("/integrations/paypal/test", { body: {} }));
  const [days, setDays] = useState(7);
  const search = useAct(() => api<any>(`/paypal/transactions?days=${days}`));
  const jobs = useQ<any>("/jobs/status");
  const runNow = useAct(() => api<any>("/jobs/run", { body: {} }));
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const s = q.data;
  const state = m.mode === "demo" ? "demo" : s.connected ? "ok" : s.configured ? "warn" : "off";
  return (
    <>
      <PageHead title="Integrations" sub="PayPal connection, webhooks and sandbox tools" />
      <Card className="mb-4">
        <CardHeader title="PayPal" right={can("integrations.manage") && m.mode === "sandbox" && <Button size="sm" disabled={test.isPending || !s.configured} onClick={() => test.mutate()}>Test connection</Button>} />
        <div className="grid gap-4 p-4 text-sm sm:grid-cols-2">
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-base font-medium">{state === "ok" ? <CheckCircle2 className="text-emerald-600" size={18} /> : state === "off" ? <CircleOff className="text-slate-400" size={18} /> : <CircleAlert className="text-amber-500" size={18} />}
              {state === "demo" ? "Demo mode — PayPal is never called" : state === "ok" ? "Connected" : state === "warn" ? "Credentials set, not yet verified" : "Disconnected (no credentials)"}</div>
            <div>Environment: <Badge tone={s.environment === "LIVE" ? "red" : "blue"}>{s.environment}</Badge> <code className="text-xs text-slate-500">{s.base_url}</code></div>
            <div>Last successful sync: <b>{when(s.last_sync_at)}</b></div>
            <div>Last integration error: {s.last_error ? <span className="text-red-600">{s.last_error}</span> : <span className="text-slate-500">none</span>}</div>
            <div>Multiparty / partner fees: {s.multiparty_ready ? <Badge tone="green">partner id set</Badge> : <Badge tone="amber">not enabled: orders are platform-collected</Badge>}</div>
          </div>
          <div className="space-y-2">
            <div>Webhook ID: {s.webhook.webhook_id_configured ? <Badge tone="green">configured</Badge> : <Badge tone="amber">not set (events are rejected)</Badge>}</div>
            <div>Last webhook received: <b>{when(s.webhook.last_event_at)}</b></div>
            <div className="text-xs text-slate-500">Register this path in the PayPal developer dashboard (your public base URL + path):<br /><code className="break-all">{s.webhook.url_path}</code></div>
            {test.data && <p role="status" className={test.data.ok ? "text-emerald-700" : "text-red-600"}>{test.data.ok ? "OAuth token obtained from PayPal." : test.data.error}</p>}
          </div>
        </div>
        <ul className="list-disc space-y-1 border-t border-slate-100 px-8 py-3 text-xs text-slate-600">{s.eligibility_notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
      </Card>
      {m.mode === "sandbox" && can("reconcile.run") && <Card className="mb-4"><CardHeader title="Transaction Search cross-check" sub="Finds PayPal transactions that have no matching record here" />
        <div className="flex items-center gap-2 p-4"><label className="text-sm">Last <input aria-label="Days" type="number" min={1} max={31} value={days} onChange={(e) => setDays(Number(e.target.value))} className="mx-1 w-16 rounded border border-slate-300 px-2 py-1" /> days</label><Button size="sm" variant="navy" disabled={search.isPending} onClick={() => search.mutate()}>Search</Button></div>
        <ErrorBox error={search.error} />{search.data && <div className="px-4 pb-4 text-sm"><p>{search.data.count} PayPal transactions; <b>{search.data.unmatched.length}</b> unmatched.</p><Table rows={search.data.unmatched} empty="Everything matches." cols={[{ h: "PayPal transaction", r: (r: any) => <code className="text-xs">{r.id}</code> }, { h: "Amount", r: (r: any) => `${r.amount?.value ?? ""} ${r.amount?.currency_code ?? ""}` }, { h: "Status", r: (r: any) => r.status }]} /></div>}</Card>}
      <Card className="mb-4"><CardHeader title="Background sync" sub="Pulls PayPal disputes, polls pending refunds, reconciles, and raises deadline alerts"
        right={can("integrations.manage") && <Button size="sm" disabled={runNow.isPending} onClick={() => runNow.mutate()}>{runNow.isPending ? "Running…" : "Run sync now"}</Button>} />
        <div className="space-y-1 p-4 text-sm">
          <div>Scheduler: {jobs.data?.scheduler_enabled ? <Badge tone="green">every {Math.round(jobs.data.interval_seconds / 60)} min</Badge> : <Badge tone="amber">off (set SYNC_INTERVAL_SECONDS or use cron)</Badge>}</div>
          <div>Last run: <b>{jobs.data?.last_run ? when(jobs.data.last_run.at) : "never"}</b></div>
          {(runNow.data ?? jobs.data?.last_run?.result) && <ul className="list-disc pl-5 text-xs text-slate-600">{Object.entries<any>(runNow.data ?? jobs.data.last_run.result).filter(([k]) => !["org", "mode"].includes(k)).map(([k, v]) => <li key={k}>{k.replaceAll("_", " ")}: <b>{String(v)}</b></li>)}</ul>}
          <ErrorBox error={runNow.error} />
        </div></Card>
      {events.data && <Card><CardHeader title="Recent webhook events" sub="Deduplicated by PayPal event id; only signature-verified events are stored" /><Table rows={events.data} empty="No events received yet." cols={[{ h: "Event", r: (r: any) => <code className="text-xs">{r.event_id}</code> }, { h: "Type", r: (r: any) => r.event_type }, { h: "Received", r: (r: any) => when(r.received_at) }, { h: "Processed", r: (r: any) => r.error ? <Badge tone="red">error</Badge> : r.processed_at ? <Badge tone="green">yes</Badge> : <Badge>no</Badge> }]} /></Card>}
    </>
  );
}
