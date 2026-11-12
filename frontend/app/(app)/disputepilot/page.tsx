"use client";
import { useEffect, useState } from "react";
import { Badge, Button, Card, CardHeader, ErrorBox, Loading, Modal, PageHead, SimBadge, Stat, StatusBadge, Table, Textarea } from "@/components/ui";
import { api, money, when } from "@/lib/api";
import { useAct, useQ, useSession } from "@/lib/session";

const URG: Record<string, "red" | "amber" | "gray" | "green" | "orange"> = { overdue: "red", critical: "red", soon: "amber", normal: "green", unknown: "gray", closed: "gray" };
const Urgency = ({ u }: { u: any }) => <Badge tone={URG[u.level]}>{u.level === "unknown" ? "deadline unknown" : u.level === "closed" ? "closed" : u.hours_left < 0 ? "overdue" : `${u.level} · ${u.hours_left < 48 ? `${Math.round(u.hours_left)}h` : `${Math.round(u.hours_left / 24)}d`} left`}</Badge>;

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const { can, m } = useSession();
  const q = useQ<any>(`/disputes/${id}`);
  const [body, setBody] = useState("");
  const [action, setAction] = useState("contest");
  const [confirm, setConfirm] = useState(false);
  const resp = q.data?.response;
  useEffect(() => { if (resp) { setBody(resp.body); setAction(resp.action); } }, [resp?.id, resp?.body]);
  const gen = useAct(() => api(`/disputes/${id}/generate`, { body: {} }));
  const url = resp ? `/disputes/${id}/responses/${resp.id}` : "";
  const save = useAct(() => api(url, { method: "PUT", body: { body, action } }));
  const approve = useAct(() => api(`${url}/approve`, { body: {} }));
  const submit = useAct(() => api(`${url}/submit`, { body: {} }));
  const a = q.data?.analysis, d = q.data?.dispute;
  const dirty = resp && (body !== resp.body || action !== resp.action);
  return (
    <Modal open onClose={onClose} title={d ? `Dispute ${d.paypal_dispute_id}` : "Dispute"} wide>
      {!a ? <Loading /> : <div className="space-y-4 text-sm">
        <div className="flex flex-wrap items-center gap-2"><StatusBadge s={d.status} /><b>{money(d.amount, d.currency)}</b><span className="text-slate-500">{d.reason.replaceAll("_", " ").toLowerCase()}</span><Urgency u={a.deadline} />{q.data.simulated && <SimBadge />}
          <span className="text-xs text-slate-500">Due: {d.response_due_date ? when(d.response_due_date) : "not provided by PayPal"}</span></div>

        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded border border-slate-200 p-3"><div className="flex items-baseline justify-between"><b>Evidence completeness</b><span className="text-2xl font-semibold">{a.score}<span className="text-sm text-slate-400">/100</span></span></div>
            <p className="text-xs italic text-slate-500">{a.score_note}</p>
            <ul className="mt-2 space-y-1.5 text-xs">{Object.entries<any>(a.score_breakdown).map(([k, v]) => <li key={k}><div className="flex justify-between"><span>{k.replace("_", " ")}</span><span>{v.score}/{v.max}</span></div><div className="h-1.5 rounded bg-slate-100"><div className="h-1.5 rounded bg-navy" style={{ width: `${(v.score / v.max) * 100}%` }} /></div><span className="text-slate-500">{v.basis}</span></li>)}</ul></div>
          <div className="rounded border border-slate-200 p-3"><b>Evidence checklist</b>
            <ul className="mt-2 space-y-1 text-xs">{a.evidence.map((e: any) => <li key={e.kind} className={e.available ? "" : "text-red-600"}>{e.available ? "✓" : "✗"} <b>{e.label}</b>{a.required.includes(e.kind) && <Badge className="ml-1">required</Badge>} — <span className={e.available ? "text-slate-600" : ""}>{e.available ? e.detail : "MISSING: " + e.detail}</span></li>)}</ul></div>
        </div>
        {a.contradictions.length > 0 && <div className="rounded border border-red-200 bg-red-50 p-3 text-xs"><b>Contradictions in the records</b><ul className="list-disc pl-4">{a.contradictions.map((c: string) => <li key={c}>{c}</li>)}</ul></div>}
        <div className="rounded border border-slate-200 bg-slate-50 p-3"><b>AI recommendation: </b><StatusBadge s={a.recommendation} /><p className="mt-1 text-xs text-slate-600">{a.recommendation_reason}</p></div>

        <div className="space-y-2"><div className="flex items-center justify-between"><b>Response</b>
          {can("disputes.write") && <Button size="sm" variant="outline" disabled={gen.isPending || resp?.status === "submitted"} onClick={() => gen.mutate()}>{resp ? "Regenerate draft" : "Generate draft from evidence"}</Button>}</div>
          {!resp ? <p className="text-xs text-slate-500">No draft yet. The generator uses only verified evidence and states what is missing.</p> : <>
            <div className="flex items-center gap-2 text-xs"><StatusBadge s={resp.status} /><label>Action: <select aria-label="Response action" disabled={resp.status === "submitted"} className="rounded border border-slate-300 px-1 py-0.5" value={action} onChange={(e) => setAction(e.target.value)}><option value="contest">Contest (submit evidence)</option><option value="accept">Accept claim</option></select></label></div>
            <Textarea aria-label="Response text" rows={14} value={body} readOnly={resp.status === "submitted" || !can("disputes.write")} onChange={(e) => setBody(e.target.value)} />
            {resp.status === "submitted" && <p role="status" className="rounded bg-emerald-50 p-2 text-xs text-emerald-800">Submitted {when(resp.submitted_at)}. {resp.result?.simulated ? resp.result.note : "PayPal accepted the submission."}</p>}
            {resp.status === "failed" && <p role="alert" className="rounded bg-red-50 p-2 text-xs text-red-700">Submission failed: {resp.result?.error}</p>}
            {resp.status !== "submitted" && <div className="flex flex-wrap justify-end gap-2">
              {can("disputes.write") && <Button variant="outline" disabled={!dirty || save.isPending} onClick={() => save.mutate()}>Save edits</Button>}
              {can("disputes.submit") && <><Button variant="navy" disabled={dirty || resp.status === "approved" || approve.isPending} onClick={() => approve.mutate()}>{resp.status === "approved" ? "Approved ✓" : "Approve response"}</Button>
                <Button disabled={resp.status !== "approved" || dirty || submit.isPending} onClick={() => setConfirm(true)}>Submit to PayPal…</Button></>}
            </div>}
            {dirty && <p className="text-xs text-amber-700">You have unsaved edits. Saving resets approval; you must approve again before submitting.</p>}
          </>}
        </div>
        <ErrorBox error={gen.error ?? save.error ?? approve.error ?? submit.error} />
        <Modal open={confirm} onClose={() => setConfirm(false)} title={action === "accept" ? "Accept this claim?" : "Submit evidence to PayPal?"}>
          <p className="text-sm">{action === "accept" ? "You will accept liability for this dispute." : "The approved response is sent through the PayPal Disputes API."} {m.mode === "demo" || q.data.simulated ? "This is a simulated dispute: nothing is sent to PayPal." : "This cannot be undone."}</p>
          <div className="mt-4 flex justify-end gap-2"><Button variant="ghost" onClick={() => setConfirm(false)}>Cancel</Button><Button disabled={submit.isPending} onClick={async () => { await submit.mutateAsync().catch(() => null); setConfirm(false); }}>Confirm</Button></div>
        </Modal>
      </div>}
    </Modal>
  );
}

export default function DisputePilot() {
  const { m, can } = useSession();
  const q = useQ<any>("/disputes");
  const [sel, setSel] = useState<string | null>(null);
  const sync = useAct(() => api<{ synced: number }>("/disputes/sync", { body: {} }));
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const st = q.data.stats, n = (k: string) => st[k]?.count ?? 0;
  const amt = (...k: string[]) => money(k.reduce((s, x) => s + Number(st[x]?.amount ?? 0), 0), m.currency);
  const urgent = q.data.disputes.filter((d: any) => ["critical", "overdue"].includes(d.urgency.level));
  return (
    <>
      <PageHead title="DisputePilot" sub="Evidence-first dispute investigation. Nothing is submitted without your approval."
        right={can("disputes.write") && m.mode === "sandbox" && <Button variant="outline" disabled={sync.isPending} onClick={() => sync.mutate()}>Sync from PayPal</Button>} />
      <ErrorBox error={sync.error} />{sync.data && <p role="status" className="mb-2 text-sm text-emerald-700">Synced {sync.data.synced} disputes from PayPal.</p>}
      {urgent.length > 0 && <div role="alert" className="mb-4 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-800"><b>{urgent.length} dispute(s) need immediate attention:</b> {urgent.map((d: any) => d.paypal_dispute_id).join(", ")}</div>}
      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Open" value={n("open")} sub={amt("open")} /><Stat label="Waiting for merchant" value={n("waiting_merchant")} sub={amt("waiting_merchant")} tone={n("waiting_merchant") ? "warn" : undefined} /><Stat label="Under review" value={n("under_review")} sub={amt("under_review")} />
        <Stat label="Resolved" value={n("resolved")} sub={amt("resolved")} /><Stat label="Lost" value={n("lost")} sub={amt("lost")} tone={n("lost") ? "bad" : undefined} />
      </div>
      <Card><CardHeader title="Disputes" sub="Sorted by response deadline (deadlines come from PayPal; unknown deadlines are never guessed)" />
        <Table onRow={(r: any) => setSel(r.id)} rows={q.data.disputes} empty="No disputes."
          cols={[{ h: "Dispute", r: (r: any) => <span><b>{r.paypal_dispute_id}</b> {r.is_simulated && <SimBadge />}</span> }, { h: "Reason", r: (r: any) => r.reason.replaceAll("_", " ").toLowerCase() }, { h: "Amount", r: (r: any) => money(r.amount, r.currency), right: true },
            { h: "Status", r: (r: any) => <StatusBadge s={r.status} /> }, { h: "Deadline", r: (r: any) => <Urgency u={r.urgency} /> }, { h: "", r: () => <span className="text-xs text-brand">Investigate →</span> }]} />
      </Card>
      {sel && <Detail id={sel} onClose={() => setSel(null)} />}
    </>
  );
}
