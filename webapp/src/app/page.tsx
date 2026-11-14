import Link from "next/link";
import { ArrowRight, Bot, Boxes, Gauge, ShieldCheck, Sparkles, Workflow } from "lucide-react";

const features = [
  {
    icon: ShieldCheck,
    title: "Moderation engine",
    body: "Warn, mute, ban, purge and anti-spam rules with EVENT → CONDITION → ACTION → AUDIT and full history.",
  },
  {
    icon: Boxes,
    title: "Real plugin lifecycle",
    body: "AVAILABLE → INSTALLED → ENABLED → CONFIGURED → RUNNING. Disabled plugins register zero handlers.",
  },
  {
    icon: Bot,
    title: "Multi-bot hosting",
    body: "Connect or clone bots, each with isolated runtime, logs, settings, plugins and resource limits.",
  },
  {
    icon: Workflow,
    title: "Automation studio",
    body: "Declarative workflows, scheduled announcements and safe command building — no arbitrary code.",
  },
  {
    icon: Gauge,
    title: "Real observability",
    body: "Live worker heartbeats, runtime events, health probes and analytics built from real data only.",
  },
  {
    icon: Sparkles,
    title: "Bot API 10.4",
    body: "Rich messages, ephemeral replies, premium emoji, live photos, and typed widget support.",
  },
];

export default function LandingPage() {
  return (
    <main className="mx-auto max-w-6xl px-6 pb-24 pt-16">
      <header className="flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <div className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-br from-violet-500 to-indigo-600 text-sm font-bold text-white shadow-glow">
            M
          </div>
          <span className="text-sm font-semibold tracking-[0.18em] text-slate-200">MEIKO AGENT</span>
        </div>
        <nav className="flex items-center gap-2">
          <Link href="/login" className="btn-ghost">
            Sign in
          </Link>
          <Link href="/dashboard" className="btn-primary">
            Open console <ArrowRight className="h-4 w-4" />
          </Link>
        </nav>
      </header>

      <section className="mt-20 max-w-3xl animate-fade-up">
        <span className="badge border-violet-500/30 bg-violet-500/10 text-violet-200">
          Bot API 10.4 · aiogram 3.31 · FastAPI · Next.js
        </span>
        <h1 className="mt-5 text-4xl font-semibold leading-tight tracking-tight text-white sm:text-5xl">
          The Telegram control plane for
          <span className="bg-gradient-to-r from-violet-300 via-indigo-300 to-cyan-300 bg-clip-text text-transparent">
            {" "}
            moderation, plugins and bot hosting
          </span>
        </h1>
        <p className="mt-5 max-w-2xl text-base leading-relaxed text-slate-400">
          One platform where the bot is the execution engine, the API is the control plane, the plugin
          manager is the extension system and the database is the source of truth.
        </p>
        <div className="mt-8 flex flex-wrap gap-3">
          <Link href="/dashboard" className="btn-primary">
            Enter dashboard <ArrowRight className="h-4 w-4" />
          </Link>
          <Link href="/docs" className="btn-ghost">
            API documentation
          </Link>
        </div>
      </section>

      <section className="mt-20 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {features.map((feature) => (
          <div key={feature.title} className="card card-hover p-5">
            <feature.icon className="h-5 w-5 text-violet-300" />
            <h2 className="mt-3 text-sm font-semibold text-white">{feature.title}</h2>
            <p className="mt-1.5 text-sm leading-relaxed text-slate-400">{feature.body}</p>
          </div>
        ))}
      </section>

      <footer className="mt-20 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-6 text-xs text-slate-500">
        <p>MEIKO AGENT · integrated bot, API, plugin runtime and webapp</p>
        <p>
          Premium emoji catalogue from <span className="text-slate-400">Beasgohan-code/Videl0</span>
        </p>
      </footer>
    </main>
  );
}
