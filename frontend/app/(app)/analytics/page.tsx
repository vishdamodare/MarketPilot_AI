"use client";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardHeader, ErrorBox, Loading, PageHead } from "@/components/ui";
import { money } from "@/lib/api";
import { useQ, useSession } from "@/lib/session";

export default function Analytics() {
  const { m, can } = useSession();
  const o = useQ<any>("/overview");
  const r = useQ<any>(can("refunds.read") ? "/refunds" : null);
  const d = useQ<any>(can("disputes.read") ? "/disputes" : null);
  const s = useQ<any>(can("sellers.read") ? "/sellers" : null);
  if (o.isLoading) return <Loading />;
  if (o.error) return <ErrorBox error={o.error} />;
  const disputes = d.data ? Object.entries<any>(d.data.stats).map(([k, v]) => ({ status: k.replaceAll("_", " "), amount: Number(v.amount), count: v.count })) : [];
  const top = s.data ? [...s.data.sellers].sort((a: any, b: any) => Number(b.balance) - Number(a.balance)).slice(0, 8).map((x: any) => ({ seller: x.business_name, balance: Number(x.balance) })) : [];
  const fmt = (v: unknown) => money(Number(v), m.currency);
  return (
    <>
      <PageHead title="Analytics" sub="Trends computed from your ledger and records" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card><CardHeader title="Payments & commission (14 days)" /><div className="h-64 p-3"><ResponsiveContainer width="100%" height="100%"><LineChart data={o.data.chart}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="day" fontSize={11} /><YAxis fontSize={11} width={48} /><Tooltip formatter={fmt} /><Legend /><Line dataKey="payments" stroke="#132244" dot={false} name="Payments" /><Line dataKey="commission" stroke="#F0721E" dot={false} name="Commission" /></LineChart></ResponsiveContainer></div></Card>
        <Card><CardHeader title="Refund amounts per day" /><div className="h-64 p-3"><ResponsiveContainer width="100%" height="100%"><BarChart data={r.data?.trend ?? []}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="day" fontSize={11} /><YAxis fontSize={11} width={48} /><Tooltip formatter={fmt} /><Bar dataKey="amount" fill="#C6963B" name="Refunds" /></BarChart></ResponsiveContainer></div></Card>
        <Card><CardHeader title="Disputed amount by status" /><div className="h-64 p-3"><ResponsiveContainer width="100%" height="100%"><BarChart data={disputes}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="status" fontSize={11} /><YAxis fontSize={11} width={48} /><Tooltip formatter={fmt} /><Bar dataKey="amount" fill="#132244" name="Disputed" /></BarChart></ResponsiveContainer></div></Card>
        <Card><CardHeader title="Largest seller balances owed" /><div className="h-64 p-3"><ResponsiveContainer width="100%" height="100%"><BarChart data={top} layout="vertical"><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis type="number" fontSize={11} /><YAxis type="category" dataKey="seller" fontSize={11} width={110} /><Tooltip formatter={fmt} /><Bar dataKey="balance" fill="#F0721E" name="Balance owed" /></BarChart></ResponsiveContainer></div></Card>
      </div>
    </>
  );
}
