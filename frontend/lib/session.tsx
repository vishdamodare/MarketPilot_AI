"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api, ApiError, getOrgId, setOrgId } from "./api";

export type Membership = { org_id: string; org_name: string; mode: "demo" | "sandbox"; currency: string; role: string; seller_id: string | null; permissions: string[] };
export type Me = { user: { id: string; name: string; email: string }; memberships: Membership[] };

type Ctx = {
  me: Me;
  m: Membership;
  can: (p: string) => boolean;
  switchOrg: (id: string) => void;
  logout: () => Promise<void>;
};
const SessionCtx = createContext<Ctx | null>(null);
export const useSession = () => {
  const c = useContext(SessionCtx);
  if (!c) throw new Error("useSession outside SessionProvider");
  return c;
};

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [orgId, setOrg] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/auth/me"), retry: false });

  useEffect(() => {
    if (q.error instanceof ApiError && q.error.status === 401) router.replace("/login");
  }, [q.error, router]);

  useEffect(() => {
    if (!q.data) return;
    const stored = getOrgId();
    const pick = q.data.memberships.find((x) => x.org_id === stored) ?? q.data.memberships[0];
    if (!pick) return router.replace("/onboarding");
    setOrgId(pick.org_id);
    setOrg(pick.org_id);
  }, [q.data, router]);

  const value = useMemo<Ctx | null>(() => {
    const me = q.data;
    const m = me?.memberships.find((x) => x.org_id === orgId);
    if (!me || !m) return null;
    return {
      me,
      m,
      can: (p) => m.permissions.includes(p),
      switchOrg: (id) => { setOrgId(id); setOrg(id); qc.removeQueries({ predicate: (qq) => qq.queryKey[0] !== "me" }); },
      logout: async () => { await api("/auth/logout", { method: "POST", body: {} }); qc.clear(); router.replace("/login"); },
    };
  }, [q.data, orgId, qc, router]);

  if (!value) return <div className="p-10 text-sm text-slate-500" role="status">Loading workspace…</div>;
  return <SessionCtx.Provider value={value}>{children}</SessionCtx.Provider>;
}

/** Org-scoped query: the key includes the active org so switching orgs never shows another org's data. */
export function useQ<T = any>(path: string | null, opts: { refetchInterval?: number } = {}) {
  const { m } = useSession();
  return useQuery({ queryKey: [m.org_id, path], queryFn: () => api<T>(path!), enabled: !!path, ...opts });
}

/** Mutation that refreshes every query of the active org on success. */
export function useAct<TArgs = void, TOut = any>(fn: (a: TArgs) => Promise<TOut>) {
  const { m } = useSession();
  const qc = useQueryClient();
  return useMutation({ mutationFn: fn, onSuccess: () => qc.invalidateQueries({ queryKey: [m.org_id] }) });
}
