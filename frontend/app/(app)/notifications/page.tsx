"use client";
import Link from "next/link";
import { Button, Card, ErrorBox, Loading, PageHead, StatusBadge } from "@/components/ui";
import { api, when } from "@/lib/api";
import { useAct, useQ } from "@/lib/session";

export default function Notifications() {
  const q = useQ<any[]>("/notifications");
  const readAll = useAct(() => api("/notifications/read-all", { body: {} }));
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorBox error={q.error} />;
  const unread = q.data!.filter((n) => !n.read).length;
  return (
    <>
      <PageHead title="Notifications" sub={`${unread} unread`} right={<Button variant="outline" disabled={!unread || readAll.isPending} onClick={() => readAll.mutate()}>Mark all read</Button>} />
      <Card><ul className="divide-y divide-slate-100">
        {q.data!.length === 0 && <li className="p-8 text-center text-sm text-slate-500">You're all caught up.</li>}
        {q.data!.map((n) => (
          <li key={n.id} className={`flex items-start gap-3 p-4 text-sm ${n.read ? "opacity-60" : ""}`}><StatusBadge s={n.severity} />
            <div className="flex-1"><b>{n.title}</b><p className="text-slate-600">{n.body}</p>{n.link && <Link href={n.link} className="text-xs font-medium text-brand">Open →</Link>}</div><span className="text-xs text-slate-500">{when(n.created_at)}</span></li>
        ))}</ul></Card>
    </>
  );
}
