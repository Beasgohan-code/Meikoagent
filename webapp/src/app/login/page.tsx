"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight, KeyRound, Send, ShieldCheck } from "lucide-react";
import { useSession } from "@/lib/session";
import { Field } from "@/components/ui";

declare global {
  interface Window {
    onTelegramAuth?: (user: Record<string, unknown>) => void;
    Telegram?: { WebApp?: { initData?: string; ready?: () => void } };
  }
}

export default function LoginPage() {
  const { signInDev, signInTelegram, signInWebApp, user } = useSession();
  const router = useRouter();
  const [telegramId, setTelegramId] = useState("");
  const [username, setUsername] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [miniAppAvailable, setMiniAppAvailable] = useState(false);

  useEffect(() => {
    if (user) router.replace("/dashboard");
  }, [user, router]);

  useEffect(() => {
    window.onTelegramAuth = async (data) => {
      setBusy(true);
      setError(null);
      try {
        await signInTelegram(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Telegram login failed");
      } finally {
        setBusy(false);
      }
    };
    const miniApp = window.Telegram?.WebApp;
    if (miniApp?.initData) {
      miniApp.ready?.();
      setMiniAppAvailable(true);
    }
  }, [signInTelegram]);

  async function handleDevLogin(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const id = Number(telegramId);
      if (!Number.isFinite(id) || id <= 0) throw new Error("Enter a numeric Telegram id");
      await signInDev(id, username || undefined);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  async function handleMiniApp() {
    const initData = window.Telegram?.WebApp?.initData;
    if (!initData) return;
    setBusy(true);
    setError(null);
    try {
      await signInWebApp(initData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Mini App login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-6 py-16">
      <Link href="/" className="mb-8 flex items-center gap-2.5">
        <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 font-bold text-white">
          M
        </div>
        <span className="text-sm font-semibold tracking-[0.18em] text-slate-200">MEIKO AGENT</span>
      </Link>

      <div className="card p-6">
        <h1 className="text-lg font-semibold text-white">Sign in</h1>
        <p className="mt-1 text-sm text-slate-400">
          Identity is validated server-side against your Telegram account. The browser never decides
          who you are.
        </p>

        {error ? (
          <div className="mt-4 rounded-xl border border-rose-500/25 bg-rose-500/10 px-3 py-2 text-xs text-rose-200">
            {error}
          </div>
        ) : null}

        {miniAppAvailable ? (
          <button className="btn-primary mt-5 w-full" onClick={handleMiniApp} disabled={busy}>
            <Send className="h-4 w-4" /> Continue with Telegram Mini App
          </button>
        ) : null}

        <div className="mt-6 rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
          <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-slate-400">
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-400" /> Telegram Login Widget
          </div>
          <p className="mt-2 text-xs text-slate-500">
            The widget script needs a public bot domain. When the API has{" "}
            <span className="kbd">BOT_TOKEN</span> configured, the widget payload is verified with an
            HMAC signature before a session is issued.
          </p>
        </div>

        <form onSubmit={handleDevLogin} className="mt-6 space-y-3">
          <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-slate-400">
            <KeyRound className="h-3.5 w-3.5 text-violet-300" /> Development login
          </div>
          <Field
            label="Telegram id"
            hint="Only works when AUTH_DEV_MODE is enabled and the environment is not production."
          >
            <input
              className="input"
              inputMode="numeric"
              placeholder="123456789"
              value={telegramId}
              onChange={(event) => setTelegramId(event.target.value)}
            />
          </Field>
          <Field label="Username (optional)">
            <input
              className="input"
              placeholder="meiko"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </Field>
          <button className="btn-primary w-full" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Continue"} <ArrowRight className="h-4 w-4" />
          </button>
        </form>
      </div>

      <p className="mt-6 text-center text-xs text-slate-500">
        Sessions are signed, expiring and revocable. Bot tokens are encrypted at rest with Fernet and
        never leave the backend.
      </p>
    </main>
  );
}
