"use client";
import { Bot, ChevronDown, Send, User } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Badge, Button, Card, ErrorBox, Input, PageHead } from "@/components/ui";
import { api } from "@/lib/api";
import { useQ } from "@/lib/session";

const LINK: Record<string, string> = { refund: "/refundguard", dispute: "/disputepilot", payout: "/moneymatch", order: "/transactions", capture: "/moneymatch" };
const SUGGEST = ["What is putting my marketplace money at risk?", "Prepare all my open disputes", "Show me sellers whose payouts are delayed", "How much did refunds cost us this week?", "Which transactions have not been reconciled?", "How much did we earn this month?"];

type Finding = { title: string; detail: string; action: string; severity: string; refs: { type: string; id: string; label: string }[] };
type Reply = { mode: string; answer: string; findings: Finding[]; tool_calls: { tool: string; args: object; ok: boolean }[] };
type Msg = { role: "user"; text: string } | ({ role: "assistant" } & Reply);

function Rich({ text }: { text: string }) {
  return <div className="space-y-1.5 text-sm leading-relaxed">{text.split("\n").filter(Boolean).map((l, i) => <p key={i} dangerouslySetInnerHTML={{ __html: l.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/\*\*(.+?)\*\*/g, "<b>$1</b>") }} />)}</div>;
}

export default function Agent() {
  const status = useQ<{ mode: string; note: string | null; tools: string[] }>("/agent/status");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const end = useRef<HTMLDivElement>(null);
  // Braces matter: in real browsers scrollIntoView's result must not be returned, or React treats it as the cleanup and the page crashes.
  useEffect(() => { end.current?.scrollIntoView?.({ behavior: "smooth" }); }, [msgs, busy]);

  const ask = async (q: string): Promise<void> => {
    if (!q.trim() || busy) return;
    setErr(null); setBusy(true); setText("");
    setMsgs((m) => [...m, { role: "user", text: q }]);
    try {
      const reply = await api<Reply>("/agent/ask", { body: { question: q } });
      setMsgs((m) => [...m, { role: "assistant", ...reply }]);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  };

  return (
    <>
      <PageHead title="AI Financial Agent" sub="Investigates refunds, disputes, reconciliation and payouts using audited read tools. It can draft, but never moves money, approves, or submits."
        right={status.data && <Badge tone={status.data.mode === "llm" ? "green" : "amber"}>{status.data.mode === "llm" ? "LLM tool-calling" : "Deterministic investigator"}</Badge>} />
      {status.data?.note && <p className="mb-3 text-xs text-slate-500">{status.data.note}</p>}
      <Card className="flex h-[calc(100vh-15rem)] min-h-[420px] flex-col">
        <div className="flex-1 space-y-5 overflow-y-auto p-4" aria-live="polite">
          {msgs.length === 0 && (
            <div className="mx-auto max-w-xl py-8 text-center">
              <Bot className="mx-auto text-brand" size={28} />
              <p className="mt-2 font-medium">Ask about your marketplace money</p>
              <div className="mt-4 flex flex-wrap justify-center gap-2">{SUGGEST.map((s) => <button key={s} onClick={() => ask(s)} className="rounded-full border border-slate-200 px-3 py-1.5 text-xs hover:border-brand hover:text-brand">{s}</button>)}</div>
            </div>
          )}
          {msgs.map((m, i) => m.role === "user" ? (
            <div key={i} className="flex justify-end gap-2"><div className="max-w-[80%] rounded-lg bg-navy px-3.5 py-2 text-sm text-white">{m.text}</div><User size={16} className="mt-2 text-slate-400" /></div>
          ) : (
            <div key={i} className="flex gap-2"><Bot size={18} className="mt-1 shrink-0 text-brand" />
              <div className="min-w-0 max-w-[90%] space-y-3">
                <div className="rounded-lg border border-slate-200 bg-slate-50 px-3.5 py-3"><Rich text={m.answer} /></div>
                {m.findings.map((f) => (
                  <div key={f.title} className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
                    <div className="flex items-center gap-2"><Badge tone={f.severity === "high" ? "red" : "amber"}>{f.severity}</Badge><b>{f.title}</b></div>
                    <p className="mt-1 text-xs text-slate-600">{f.detail}</p>
                    <p className="mt-1 text-xs"><b>Recommended:</b> {f.action}</p>
                    <div className="mt-2 flex flex-wrap gap-1.5">{f.refs.slice(0, 12).map((r) => <Link key={r.id + r.type} href={LINK[r.type] ?? "/overview"} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] hover:bg-orange-50">{r.label}</Link>)}{f.refs.length > 12 && <span className="text-[11px] text-slate-500">+{f.refs.length - 12} more</span>}</div>
                  </div>
                ))}
                <details className="text-xs text-slate-500"><summary className="flex cursor-pointer items-center gap-1"><ChevronDown size={12} />Investigation trace ({m.tool_calls.length} tool calls)</summary>
                  <ul className="mt-1 space-y-0.5 font-mono">{m.tool_calls.map((t, k) => <li key={k}>{t.ok ? "✓" : "✗"} {t.tool}({Object.keys(t.args).length ? JSON.stringify(t.args) : ""})</li>)}</ul></details>
              </div>
            </div>
          ))}
          {busy && <p className="text-sm text-slate-500" role="status">Investigating your records…</p>}
          <div ref={end} />
        </div>
        <div className="border-t border-slate-100 p-3">
          <ErrorBox error={err} />
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
            <Input aria-label="Ask the agent" placeholder="e.g. Why did we lose money this week?" value={text} onChange={(e) => setText(e.target.value)} />
            <Button type="submit" disabled={busy || !text.trim()}><Send size={14} />Ask</Button>
          </form>
        </div>
      </Card>
    </>
  );
}
