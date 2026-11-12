"use client";
import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { api, setOrgId } from "@/lib/api";
import type { Me } from "@/lib/session";
import { Button, ErrorBox, Field, Input } from "./ui";

function Frame({ title, sub, children, foot }: { title: string; sub: string; children: React.ReactNode; foot: React.ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-surface px-4 py-10">
      <div className="w-full max-w-md">
        <Link href="/" className="mb-6 block text-center text-lg font-semibold">MarketPilot<span className="text-brand"> AI</span></Link>
        <div className="rounded-lg border border-slate-200 bg-white p-6 shadow-sm">
          <h1 className="text-lg font-semibold">{title}</h1>
          <p className="mb-5 mt-1 text-sm text-slate-500">{sub}</p>
          {children}
        </div>
        <p className="mt-4 text-center text-sm text-slate-500">{foot}</p>
      </div>
    </div>
  );
}

function landing(me: Me, router: ReturnType<typeof useRouter>, isNew = false) {
  const m = me.memberships[0];
  if (m) setOrgId(m.org_id);
  router.replace(isNew ? "/onboarding" : m?.role === "seller" ? "/portal" : "/overview");
}

const loginSchema = z.object({ email: z.string().email("Enter a valid email"), password: z.string().min(1, "Password is required") });
export function LoginForm() {
  const router = useRouter();
  const [err, setErr] = useState<unknown>(null);
  const f = useForm<z.infer<typeof loginSchema>>({ resolver: zodResolver(loginSchema) });
  return (
    <Frame title="Log in" sub="Welcome back." foot={<>New here? <Link className="font-medium text-brand" href="/signup">Create an account</Link></>}>
      <form onSubmit={f.handleSubmit(async (v) => { setErr(null); try { landing(await api<Me>("/auth/login", { body: v }), router); } catch (e) { setErr(e); } })} className="space-y-4" noValidate>
        <Field label="Email" error={f.formState.errors.email?.message}><Input type="email" autoComplete="email" {...f.register("email")} /></Field>
        <Field label="Password" error={f.formState.errors.password?.message}><Input type="password" autoComplete="current-password" {...f.register("password")} /></Field>
        <ErrorBox error={err} />
        <Button type="submit" className="w-full" disabled={f.formState.isSubmitting}>{f.formState.isSubmitting ? "Signing in…" : "Log in"}</Button>
        <div className="text-center text-sm"><Link className="text-slate-500 hover:text-navy" href="/forgot">Forgot password?</Link></div>
      </form>
    </Frame>
  );
}

const signupSchema = z.object({
  name: z.string().min(1, "Your name is required"),
  email: z.string().email("Enter a valid email"),
  password: z.string().min(10, "Use at least 10 characters"),
  org_name: z.string().min(1, "Organization name is required"),
  mode: z.enum(["demo", "sandbox"]),
});
function SignupInner() {
  const router = useRouter();
  const preset = useSearchParams().get("mode") === "demo" ? "demo" : "sandbox";
  const [err, setErr] = useState<unknown>(null);
  const f = useForm<z.infer<typeof signupSchema>>({ resolver: zodResolver(signupSchema), defaultValues: { mode: preset } });
  const mode = f.watch("mode");
  return (
    <Frame title="Create your organization" sub="Your account and first marketplace workspace." foot={<>Already registered? <Link className="font-medium text-brand" href="/login">Log in</Link></>}>
      <form onSubmit={f.handleSubmit(async (v) => { setErr(null); try { landing(await api<Me>("/auth/signup", { body: v }), router, true); } catch (e) { setErr(e); } })} className="space-y-4" noValidate>
        <Field label="Your name" error={f.formState.errors.name?.message}><Input autoComplete="name" {...f.register("name")} /></Field>
        <Field label="Work email" error={f.formState.errors.email?.message}><Input type="email" autoComplete="email" {...f.register("email")} /></Field>
        <Field label="Password" hint="At least 10 characters" error={f.formState.errors.password?.message}><Input type="password" autoComplete="new-password" {...f.register("password")} /></Field>
        <Field label="Organization / marketplace name" error={f.formState.errors.org_name?.message}><Input {...f.register("org_name")} /></Field>
        <fieldset className="space-y-2 text-sm">
          <legend className="mb-1 font-medium">Workspace type</legend>
          <label className="flex gap-2 rounded-md border border-slate-200 p-2.5"><input type="radio" value="demo" {...f.register("mode")} /><span><b>Demo</b> — seeded &ldquo;Marketplace Financial Crisis&rdquo; with <u>simulated</u> data. Never calls PayPal.</span></label>
          <label className="flex gap-2 rounded-md border border-slate-200 p-2.5"><input type="radio" value="sandbox" {...f.register("mode")} /><span><b>PayPal Sandbox</b> — real sandbox API calls (needs credentials in the backend env).</span></label>
        </fieldset>
        <ErrorBox error={err} />
        <Button type="submit" className="w-full" disabled={f.formState.isSubmitting}>{f.formState.isSubmitting ? (mode === "demo" ? "Seeding demo data…" : "Creating…") : "Create account"}</Button>
      </form>
    </Frame>
  );
}
export const SignupForm = () => <Suspense><SignupInner /></Suspense>;

const forgotSchema = z.object({ email: z.string().email("Enter a valid email") });
export function ForgotForm() {
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const f = useForm<z.infer<typeof forgotSchema>>({ resolver: zodResolver(forgotSchema) });
  return (
    <Frame title="Reset your password" sub="We'll send a reset link if the account exists." foot={<Link className="font-medium text-brand" href="/login">Back to log in</Link>}>
      <form onSubmit={f.handleSubmit(async (v) => { setErr(null); try { setMsg((await api<{ message: string }>("/auth/forgot", { body: v })).message); } catch (e) { setErr(e); } })} className="space-y-4" noValidate>
        <Field label="Email" error={f.formState.errors.email?.message}><Input type="email" {...f.register("email")} /></Field>
        <ErrorBox error={err} />
        {msg && <p role="status" className="rounded-md bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{msg}</p>}
        <Button type="submit" className="w-full" disabled={f.formState.isSubmitting}>Send reset link</Button>
      </form>
    </Frame>
  );
}

const resetSchema = z.object({ password: z.string().min(10, "Use at least 10 characters") });
function ResetInner() {
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";
  const [err, setErr] = useState<unknown>(null);
  const f = useForm<z.infer<typeof resetSchema>>({ resolver: zodResolver(resetSchema) });
  return (
    <Frame title="Choose a new password" sub="Reset links expire after 15 minutes." foot={<Link className="font-medium text-brand" href="/login">Back to log in</Link>}>
      <form onSubmit={f.handleSubmit(async (v) => { setErr(null); try { await api("/auth/reset", { body: { token, password: v.password } }); router.replace("/login"); } catch (e) { setErr(e); } })} className="space-y-4" noValidate>
        <Field label="New password" error={f.formState.errors.password?.message}><Input type="password" autoComplete="new-password" {...f.register("password")} /></Field>
        <ErrorBox error={err} />
        <Button type="submit" className="w-full" disabled={!token || f.formState.isSubmitting}>Update password</Button>
      </form>
    </Frame>
  );
}
export const ResetForm = () => <Suspense><ResetInner /></Suspense>;
