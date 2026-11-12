"use client";
import { BarChart3, Bell, Bot, GitMerge, LayoutDashboard, LogOut, Menu, Plug, Receipt, Scale, Settings, ShieldAlert, Split, Store, UserRound } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useQ, useSession } from "@/lib/session";
import { Badge, cn, Select } from "./ui";

const NAV = [
  { href: "/overview", label: "Overview", icon: LayoutDashboard, perm: "dashboard" },
  { href: "/agent", label: "AI Financial Agent", icon: Bot, perm: "agent.use" },
  { href: "/sellers", label: "Sellers", icon: Store, perm: "sellers.read" },
  { href: "/transactions", label: "Transactions", icon: Receipt, perm: "transactions.read" },
  { href: "/smartsplit", label: "SmartSplit", icon: Split, perm: "sellers.read" },
  { href: "/refundguard", label: "RefundGuard", icon: ShieldAlert, perm: "refunds.read" },
  { href: "/disputepilot", label: "DisputePilot", icon: Scale, perm: "disputes.read" },
  { href: "/moneymatch", label: "MoneyMatch", icon: GitMerge, perm: "reconcile.read" },
  { href: "/analytics", label: "Analytics", icon: BarChart3, perm: "reports.read" },
  { href: "/notifications", label: "Notifications", icon: Bell, perm: "notifications.read" },
  { href: "/integrations", label: "Integrations", icon: Plug, perm: "dashboard" },
  { href: "/settings", label: "Settings", icon: Settings, perm: "settings.manage" },
  { href: "/portal", label: "Seller Portal", icon: UserRound, perm: "portal.read" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const { me, m, can, switchOrg, logout } = useSession();
  const path = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const notes = useQ<{ read: boolean }[]>("/notifications");
  const unread = notes.data?.filter((n) => !n.read).length ?? 0;
  const items = NAV.filter((n) => can(n.perm));

  useEffect(() => {
    if (m.role === "seller" && !["/portal", "/notifications"].includes(path)) router.replace("/portal");
  }, [m.role, path, router]);
  useEffect(() => setOpen(false), [path]);

  return (
    <div className="flex min-h-screen">
      <aside className={cn("fixed inset-y-0 left-0 z-40 w-60 shrink-0 bg-navy text-slate-200 transition-transform md:static md:translate-x-0", open ? "translate-x-0" : "-translate-x-full")}>
        <div className="px-5 py-5 text-lg font-semibold tracking-tight text-white">MarketPilot<span className="text-brand"> AI</span></div>
        <nav aria-label="Main" className="space-y-0.5 px-2">
          {items.map(({ href, label, icon: Icon }) => (
            <Link key={href} href={href} aria-current={path === href ? "page" : undefined}
              className={cn("flex items-center gap-2.5 rounded-md px-3 py-2 text-sm", path === href ? "bg-white/10 font-medium text-white" : "hover:bg-white/5")}>
              <Icon size={16} className={path === href ? "text-brand" : ""} />
              <span className="flex-1">{label}</span>
              {href === "/notifications" && unread > 0 && <span className="rounded-full bg-brand px-1.5 text-[10px] font-semibold text-white">{unread}</span>}
            </Link>
          ))}
        </nav>
      </aside>
      {open && <button aria-label="Close menu" className="fixed inset-0 z-30 bg-navy/40 md:hidden" onClick={() => setOpen(false)} />}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex flex-wrap items-center gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
          <button aria-label="Open menu" className="rounded p-1.5 hover:bg-slate-100 md:hidden" onClick={() => setOpen(true)}><Menu size={18} /></button>
          {m.mode === "demo" ? <Badge tone="orange" className="px-2 py-1 text-xs font-bold tracking-wide">DEMO MODE · simulated data</Badge> : <Badge tone="blue" className="px-2 py-1 text-xs">PayPal Sandbox</Badge>}
          <div className="ml-auto flex items-center gap-3">
            {me.memberships.length > 1 && (
              <Select aria-label="Switch organization" className="!w-auto !py-1 text-xs" value={m.org_id} onChange={(e) => switchOrg(e.target.value)}>
                {me.memberships.map((x) => <option key={x.org_id} value={x.org_id}>{x.org_name} ({x.mode})</option>)}
              </Select>
            )}
            <span className="hidden text-xs text-slate-500 sm:block">{me.user.name} · {m.role.replace("_", " ")}</span>
            <button onClick={logout} className="flex items-center gap-1 rounded p-1.5 text-xs text-slate-600 hover:bg-slate-100" aria-label="Log out"><LogOut size={15} /></button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">{children}</main>
      </div>
    </div>
  );
}
