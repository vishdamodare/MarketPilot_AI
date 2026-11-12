"use client";
import { X } from "lucide-react";
import { forwardRef, useEffect } from "react";

export const cn = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

const btn = {
  primary: "bg-brand text-white hover:bg-[#d9631a] focus-visible:outline-brand",
  navy: "bg-navy text-white hover:bg-[#1c3166] focus-visible:outline-navy",
  outline: "border border-slate-300 bg-white text-navy hover:bg-slate-50 focus-visible:outline-navy",
  danger: "bg-red-600 text-white hover:bg-red-700 focus-visible:outline-red-600",
  ghost: "text-navy hover:bg-slate-100 focus-visible:outline-navy",
};
export function Button({ variant = "primary", size = "md", className, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: keyof typeof btn; size?: "sm" | "md" }) {
  return (
    <button
      {...p}
      className={cn("inline-flex items-center justify-center gap-1.5 rounded-md font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "px-2.5 py-1 text-xs" : "px-3.5 py-2 text-sm", btn[variant], className)}
    />
  );
}

export const Card = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => (
  <div {...p} className={cn("rounded-lg border border-slate-200 bg-white shadow-[0_1px_2px_rgba(19,34,68,.04)]", className)} />
);
export const CardHeader = ({ title, sub, right }: { title: React.ReactNode; sub?: React.ReactNode; right?: React.ReactNode }) => (
  <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-4 py-3">
    <div>
      <h2 className="text-sm font-semibold text-navy">{title}</h2>
      {sub && <p className="mt-0.5 text-xs text-slate-500">{sub}</p>}
    </div>
    {right}
  </div>
);

const tones = {
  gray: "bg-slate-100 text-slate-700", green: "bg-emerald-50 text-emerald-700", red: "bg-red-50 text-red-700",
  amber: "bg-amber-50 text-amber-800", blue: "bg-blue-50 text-blue-700", orange: "bg-orange-50 text-orange-700", navy: "bg-navy text-white",
};
export const Badge = ({ tone = "gray", children, className }: { tone?: keyof typeof tones; children: React.ReactNode; className?: string }) => (
  <span className={cn("inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium", tones[tone], className)}>{children}</span>
);

const STATUS_TONE: Record<string, keyof typeof tones> = {
  active: "green", completed: "green", paid: "green", settled: "green", resolved: "green", COMPLETED: "green", low: "green", contest: "green", info: "blue",
  pending: "amber", pending_approval: "amber", needs_review: "amber", processing: "amber", open: "amber", waiting_merchant: "orange", medium: "amber", warning: "amber", gather_evidence: "amber", review: "amber", consider_accept: "amber",
  failed: "red", rejected: "red", suspended: "red", lost: "red", high: "red", critical: "red", escalated: "red", delayed: "red", overdue: "red", accept: "blue", under_review: "blue",
};
export const StatusBadge = ({ s }: { s?: string | null }) => <Badge tone={STATUS_TONE[s ?? ""] ?? "gray"}>{(s ?? "—").replaceAll("_", " ")}</Badge>;

export const SimBadge = () => <Badge tone="navy" className="uppercase tracking-wide">Simulated</Badge>;

export function Stat({ label, value, sub, tone }: { label: string; value: React.ReactNode; sub?: React.ReactNode; tone?: "warn" | "bad" }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium text-slate-500">{label}</div>
      <div className={cn("mt-1 text-2xl font-semibold tabular-nums", tone === "bad" ? "text-red-600" : tone === "warn" ? "text-amber-600" : "text-navy")}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-slate-500">{sub}</div>}
    </Card>
  );
}

const field = "w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-navy placeholder:text-slate-400 focus:border-navy focus:outline-none focus:ring-1 focus:ring-navy";
export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...p }, ref) => <input ref={ref} {...p} className={cn(field, className)} />);
Input.displayName = "Input";
export const Select = forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(({ className, ...p }, ref) => <select ref={ref} {...p} className={cn(field, className)} />);
Select.displayName = "Select";
export const Textarea = forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(({ className, ...p }, ref) => <textarea ref={ref} {...p} className={cn(field, "font-mono", className)} />);
Textarea.displayName = "Textarea";

export function Field({ label, error, children, hint }: { label: string; error?: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="text-sm">
      <label className="block">
        <span className="mb-1 block font-medium text-navy">{label}</span>
        {children}
      </label>
      {hint && !error && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
      {error && <span role="alert" className="mt-1 block text-xs text-red-600">{error}</span>}
    </div>
  );
}

export function Modal({ open, onClose, title, children, wide }: { open: boolean; onClose: () => void; title: string; children: React.ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-navy/40 p-4 pt-12" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-label={title} className={cn("w-full rounded-lg bg-white shadow-xl", wide ? "max-w-4xl" : "max-w-lg")}>
        <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
          <h2 className="font-semibold text-navy">{title}</h2>
          <button aria-label="Close" onClick={onClose} className="rounded p-1 text-slate-500 hover:bg-slate-100"><X size={16} /></button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

export type Col<T> = { h: string; r: (row: T) => React.ReactNode; right?: boolean };
export function Table<T>({ cols, rows, empty = "Nothing here yet.", onRow }: { cols: Col<T>[]; rows: T[]; empty?: string; onRow?: (r: T) => void }) {
  if (!rows.length) return <div className="px-4 py-10 text-center text-sm text-slate-500">{empty}</div>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-slate-100 text-left text-xs uppercase tracking-wide text-slate-500">
            {cols.map((c) => <th key={c.h} className={cn("px-4 py-2 font-medium", c.right && "text-right")}>{c.h}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} onClick={onRow ? () => onRow(row) : undefined} className={cn("border-b border-slate-50 last:border-0", onRow && "cursor-pointer hover:bg-slate-50")}>
              {cols.map((c) => <td key={c.h} className={cn("px-4 py-2.5 align-top text-navy", c.right && "text-right tabular-nums")}>{c.r(row)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export const Loading = () => <div className="p-6 text-sm text-slate-500" role="status">Loading…</div>;
export const ErrorBox = ({ error }: { error: unknown }) => error ? <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{(error as Error).message}</div> : null;
export const PageHead = ({ title, sub, right }: { title: string; sub?: string; right?: React.ReactNode }) => (
  <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
    <div>
      <h1 className="text-xl font-semibold text-navy">{title}</h1>
      {sub && <p className="mt-0.5 text-sm text-slate-500">{sub}</p>}
    </div>
    {right}
  </div>
);

export function Tabs({ tabs, value, onChange }: { tabs: [string, string][]; value: string; onChange: (v: string) => void }) {
  return (
    <div role="tablist" className="mb-4 flex gap-1 border-b border-slate-200">
      {tabs.map(([k, label]) => (
        <button key={k} role="tab" aria-selected={value === k} onClick={() => onChange(k)}
          className={cn("-mb-px border-b-2 px-3 py-2 text-sm font-medium", value === k ? "border-brand text-navy" : "border-transparent text-slate-500 hover:text-navy")}>{label}</button>
      ))}
    </div>
  );
}
