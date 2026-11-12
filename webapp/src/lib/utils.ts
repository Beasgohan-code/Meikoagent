import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const statusTone: Record<string, string> = {
  running: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  enabled: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  configured: "border-cyan-500/30 bg-cyan-500/10 text-cyan-300",
  starting: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  installed: "border-slate-500/30 bg-slate-500/10 text-slate-300",
  available: "border-slate-500/20 bg-slate-500/5 text-slate-400",
  stopped: "border-slate-500/30 bg-slate-500/10 text-slate-400",
  draft: "border-slate-500/30 bg-slate-500/10 text-slate-400",
  disabled: "border-slate-600/30 bg-slate-700/10 text-slate-500",
  error: "border-rose-500/30 bg-rose-500/10 text-rose-300",
  failed: "border-rose-500/30 bg-rose-500/10 text-rose-300",
  pending: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  warning: "border-amber-500/30 bg-amber-500/10 text-amber-300",
  info: "border-indigo-500/30 bg-indigo-500/10 text-indigo-300",
  ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
  degraded: "border-amber-500/30 bg-amber-500/10 text-amber-300",
};

export function tone(status: string | undefined | null): string {
  return statusTone[status ?? ""] ?? "border-white/10 bg-white/[0.03] text-slate-300";
}

export function initials(name: string | undefined | null): string {
  if (!name) return "M";
  return name
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
}
