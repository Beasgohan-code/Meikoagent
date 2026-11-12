"use client";

import { Check, Clipboard, Search } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/utils";

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: T; label: string; count?: number }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="flex gap-1 overflow-x-auto rounded-xl border border-white/[0.06] bg-charcoal-850/60 p-1">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => onChange(tab.id)}
          className={cn(
            "flex items-center gap-1.5 whitespace-nowrap rounded-lg px-3 py-1.5 text-sm transition",
            value === tab.id ? "bg-violet-500/15 text-violet-200" : "text-slate-400 hover:text-slate-200",
          )}
        >
          {tab.label}
          {typeof tab.count === "number" ? (
            <span className="rounded-full bg-white/[0.06] px-1.5 text-[10px] text-slate-300">{tab.count}</span>
          ) : null}
        </button>
      ))}
    </div>
  );
}

export function SearchInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="relative">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
      <input
        className="input pl-9"
        value={value}
        placeholder={placeholder ?? "Search…"}
        onChange={(event) => onChange(event.target.value)}
      />
    </div>
  );
}

export function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="btn-ghost"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1600);
        } catch {
          setCopied(false);
        }
      }}
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-300" /> : <Clipboard className="h-3.5 w-3.5" />}
      {label ?? (copied ? "Copied" : "Copy")}
    </button>
  );
}

export function CodeBlock({ children, title }: { children: string; title?: string }) {
  return (
    <div className="overflow-hidden rounded-xl border border-white/[0.07] bg-black/40">
      {title ? (
        <div className="flex items-center justify-between border-b border-white/[0.06] px-3 py-2">
          <span className="font-mono text-[11px] uppercase tracking-wider text-slate-400">{title}</span>
          <CopyButton value={children} label="Copy" />
        </div>
      ) : null}
      <pre className="overflow-x-auto px-3 py-3 font-mono text-[11.5px] leading-relaxed text-slate-300">
        {children}
      </pre>
    </div>
  );
}
