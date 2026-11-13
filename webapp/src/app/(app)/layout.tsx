"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Activity,
  BarChart3,
  Boxes,
  BookOpen,
  Bot,
  ChevronRight,
  Cpu,
  Crown,
  Database,
  Download,
  Film,
  FolderTree,
  Gauge,
  HardDrive,
  Headphones,
  KeyRound,
  Layers,
  LayoutDashboard,
  Link2,
  ListMusic,
  ListOrdered,
  LogOut,
  Menu,
  Music2,
  Radio,
  Save,
  Server,
  Settings2,
  Shield,
  Sliders,
  Sparkles,
  Terminal,
  Users,
  Workflow,
  X,
} from "lucide-react";
import { useSession } from "@/lib/session";
import { cn, initials } from "@/lib/utils";

type NavItem = { href: string; label: string; icon: React.ElementType; badge?: string };
type NavGroup = { title: string; items: NavItem[] };

/** Owner Control Center sections. The links carry a tab query; the API is the
 *  authority, so a non-owner who types the URL still gets a 403. */
const OWNER_NAV: { href: string; label: string; icon: React.ElementType }[] = [
  { href: "/owner", label: "Overview", icon: Crown },
  { href: "/owner?tab=users", label: "Users", icon: Users },
  { href: "/owner?tab=bots", label: "Bots", icon: Bot },
  { href: "/owner?tab=plugins", label: "Plugins & global scope", icon: Boxes },
  { href: "/owner?tab=marketplace", label: "Marketplace admin", icon: Download },
  { href: "/owner?tab=infrastructure", label: "Infrastructure", icon: Server },
  { href: "/owner?tab=capabilities", label: "Capabilities", icon: Sliders },
  { href: "/owner?tab=audit", label: "Audit history", icon: Activity },
  { href: "/owner?tab=security", label: "Security", icon: Shield },
];

const NAV: NavGroup[] = [
  {
    title: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
      { href: "/activity", label: "Live Activity", icon: Activity },
      { href: "/analytics", label: "Analytics", icon: BarChart3 },
    ],
  },
  {
    title: "Bot Management",
    items: [
      { href: "/bots", label: "My Bots", icon: Bot },
      { href: "/bots/new", label: "Create Bot", icon: Sparkles },
      { href: "/templates", label: "Templates", icon: Boxes },
    ],
  },
  {
    title: "Moderation",
    items: [
      { href: "/moderation", label: "Moderation Center", icon: Shield },
      { href: "/groups", label: "Groups", icon: Users },
      { href: "/moderation?tab=antispam", label: "Anti-Spam", icon: Sliders },
      { href: "/moderation?tab=rules", label: "Rules & Filters", icon: ListOrdered },
    ],
  },
  {
    title: "Music",
    items: [
      { href: "/music", label: "Player", icon: Music2 },
      { href: "/music/lounge", label: "Lounge", icon: Headphones },
      { href: "/music/queue", label: "Queue", icon: ListMusic },
      { href: "/music/playlists", label: "Playlists", icon: ListOrdered },
      { href: "/music/rooms", label: "Rooms", icon: Radio },
      { href: "/music/analytics", label: "Analytics", icon: BarChart3 },
    ],
  },
  {
    title: "Saver",
    items: [
      { href: "/saver", label: "Backup Jobs", icon: Save },
      { href: "/saver/library", label: "Media Library", icon: HardDrive },
      { href: "/saver/transfer", label: "Import / Export", icon: Download },
      { href: "/saver/settings", label: "Settings", icon: Settings2 },
      { href: "/saver/history", label: "History", icon: Activity },
    ],
  },
  {
    title: "Encoder",
    items: [
      { href: "/encoder", label: "Studio", icon: Film },
      { href: "/encoder/presets", label: "Presets", icon: Layers },
      { href: "/encoder/jobs", label: "Active Jobs", icon: Gauge },
      { href: "/encoder/history", label: "History", icon: Activity },
      { href: "/encoder/settings", label: "Settings", icon: Settings2 },
    ],
  },
  {
    title: "Filestore",
    items: [
      { href: "/filestore", label: "My Bots", icon: FolderTree },
      { href: "/filestore/files", label: "File Manager", icon: HardDrive },
      { href: "/filestore/links", label: "Link Generator", icon: Link2 },
      { href: "/filestore/storage", label: "Storage", icon: Database },
      { href: "/filestore/settings", label: "Bot Settings", icon: Settings2 },
    ],
  },
  {
    title: "Linkshare",
    items: [
      { href: "/linkshare", label: "Create Link", icon: Link2 },
      { href: "/linkshare/batch", label: "Batch Sharing", icon: Layers },
      { href: "/linkshare/manager", label: "Link Manager", icon: ListOrdered },
      { href: "/linkshare/analytics", label: "Analytics", icon: BarChart3 },
    ],
  },
  {
    title: "Leech",
    items: [
      { href: "/leech", label: "Transfer Dashboard", icon: Gauge },
      { href: "/leech/downloads", label: "Downloads", icon: Download },
      { href: "/leech/uploads", label: "Uploads", icon: Server },
      { href: "/leech/queue", label: "Queue", icon: ListMusic },
      { href: "/leech/destinations", label: "Destinations", icon: Cpu },
      { href: "/leech/history", label: "History", icon: Activity },
    ],
  },
  {
    title: "Platform",
    items: [
      { href: "/platform", label: "Shared Workers", icon: Cpu },
      { href: "/platform/storage", label: "Storage Usage", icon: Database },
      { href: "/platform/modules", label: "Module Management", icon: Boxes },
      { href: "/platform/resources", label: "Resource Monitoring", icon: Gauge },
      { href: "/platform/logs", label: "System Logs", icon: Activity },
    ],
  },
  {
    title: "Plugins",
    items: [
      { href: "/plugins", label: "Marketplace", icon: Boxes },
      { href: "/plugins?tab=installed", label: "Installed", icon: Cpu },
      { href: "/plugins?tab=logs", label: "Plugin Logs", icon: Activity },
    ],
  },
  {
    title: "Automation",
    items: [
      { href: "/automation", label: "Automation Studio", icon: Workflow },
      { href: "/automation?tab=scheduler", label: "Scheduler", icon: Gauge },
      { href: "/developer?tab=commands", label: "Command Studio", icon: ListOrdered },
    ],
  },
  {
    title: "Developer",
    items: [
      { href: "/developer", label: "API Keys", icon: KeyRound },
      { href: "/developer?tab=webhooks", label: "Webhooks", icon: Server },
      { href: "/docs", label: "Documentation", icon: BookOpen },
    ],
  },
  {
    title: "Infrastructure",
    items: [
      { href: "/infrastructure", label: "Runtime", icon: Server },
      { href: "/infrastructure?tab=workers", label: "Workers", icon: Cpu },
      { href: "/infrastructure?tab=logs", label: "Logs", icon: Activity },
      { href: "/infrastructure?tab=monitoring", label: "Monitoring", icon: Gauge },
    ],
  },
  {
    title: "Account",
    items: [{ href: "/account", label: "Profile & Security", icon: Users }],
  },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading, signOut } = useSession();
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [loading, user, router]);

  useEffect(() => setOpen(false), [pathname]);

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <div className="flex items-center gap-3 text-sm text-slate-400">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-violet-500 border-t-transparent" />
          Loading console…
        </div>
      </div>
    );
  }
  if (!user) return null;

  const nav = (
    <nav className="flex flex-1 flex-col gap-6 overflow-y-auto px-3 py-4">
      {NAV.map((group) => (
        <div key={group.title}>
          <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-600">
            {group.title}
          </p>
          <div className="space-y-0.5">
            {group.items.map((item) => {
              const [path, query] = item.href.split("?");
              const active =
                pathname === path && (query ? false : true) && !item.href.includes("?");
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={cn("nav-link", active && "nav-link-active")}
                >
                  <item.icon className="h-4 w-4" />
                  <span className="flex-1">{item.label}</span>
                </Link>
              );
            })}
          </div>
        </div>
      ))}
      {user.is_owner ? (
        <div>
          <p className="mb-1.5 px-3 text-[10px] font-semibold uppercase tracking-[0.16em] text-slate-600">
            Owner Control Center
          </p>
          {OWNER_NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={cn("nav-link", pathname === "/owner" && "nav-link-active")}
            >
              <item.icon className="h-4 w-4 text-amber-300" />
              <span className="flex-1">{item.label}</span>
            </Link>
          ))}
        </div>
      ) : null}
    </nav>
  );

  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-[248px] shrink-0 flex-col border-r border-white/[0.06] bg-charcoal-950/70 backdrop-blur lg:flex">
        <Link href="/dashboard" className="flex items-center gap-2.5 px-5 py-5">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-violet-500 to-indigo-600 text-xs font-bold text-white">
            M
          </div>
          <div>
            <p className="text-[13px] font-semibold tracking-[0.14em] text-white">MEIKO</p>
            <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Agent console</p>
          </div>
        </Link>
        {nav}
        <div className="border-t border-white/[0.06] p-3">
          <div className="flex items-center gap-2.5 rounded-xl px-2 py-2">
            <div className="grid h-8 w-8 place-items-center rounded-full bg-violet-500/20 text-xs font-semibold text-violet-200">
              {initials(user.first_name || user.username || "Meiko")}
            </div>
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-medium text-slate-200">
                {user.first_name || user.username || "Operator"}
              </p>
              <p className="truncate text-[10px] text-slate-500">
                {user.is_owner ? "Platform owner" : user.role}
              </p>
            </div>
            <button
              onClick={() => void signOut()}
              className="rounded-lg p-1.5 text-slate-500 hover:bg-white/5 hover:text-rose-300"
              aria-label="Sign out"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-white/[0.06] bg-charcoal-900/80 px-4 py-3 backdrop-blur lg:px-6">
          <button className="btn-ghost lg:hidden" onClick={() => setOpen((value) => !value)} aria-label="Menu">
            {open ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </button>
          <div className="flex items-center gap-1.5 text-xs text-slate-500">
            <span className="hidden sm:inline">Console</span>
            <ChevronRight className="hidden h-3 w-3 sm:inline" />
            <span className="text-slate-300">{pathname.replace("/", "") || "dashboard"}</span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <span className="badge border-emerald-500/25 bg-emerald-500/10 text-emerald-300">
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-emerald-400" /> live
            </span>
            <Link href="/docs" className="btn-ghost hidden sm:inline-flex">
              API docs
            </Link>
          </div>
        </header>
        {open ? (
          <div className="border-b border-white/[0.06] bg-charcoal-950/95 lg:hidden">{nav}</div>
        ) : null}
        <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
