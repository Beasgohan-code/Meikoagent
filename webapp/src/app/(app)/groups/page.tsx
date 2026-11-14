"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { ChevronRight, Users } from "lucide-react";
import { api, relativeTime } from "@/lib/api";
import type { Group, Page, Bot } from "@/lib/types";
import { Badge, Card, EmptyState, ErrorNote, SectionHeader, Skeleton } from "@/components/ui";
import { SearchInput } from "@/components/patterns";

export default function GroupsPage() {
  const [search, setSearch] = useState("");
  const groups = useQuery({ queryKey: ["groups"], queryFn: () => api.get<Group[]>("/groups") });
  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Page<Bot>>("/bots") });

  const botName = (id: number) => bots.data?.items.find((bot) => bot.id === id)?.name ?? `bot ${id}`;
  const filtered = (groups.data ?? []).filter((group) =>
    [group.title, group.username ?? "", String(group.chat_id)].join(" ").toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Groups"
        description="Every chat the bots have observed, with its settings, members, rules and audit history."
        action={<div className="w-64"><SearchInput value={search} onChange={setSearch} placeholder="Search groups…" /></div>}
      />
      {groups.isError ? <ErrorNote message="Could not load groups." onRetry={() => groups.refetch()} /> : null}

      {groups.isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-16" />
          <Skeleton className="h-16" />
        </div>
      ) : filtered.length ? (
        <Card className="p-0">
          <table className="table-base">
            <thead>
              <tr>
                <th>Group</th>
                <th>Bot</th>
                <th>Members</th>
                <th>Bot role</th>
                <th>Last activity</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.map((group) => (
                <tr key={group.id} className="transition hover:bg-white/[0.02]">
                  <td>
                    <Link href={`/groups/${group.id}`} className="text-slate-100 hover:text-violet-200">
                      {group.title || `chat ${group.chat_id}`}
                    </Link>
                    <p className="text-[11px] text-slate-500">{group.username ? `@${group.username}` : group.chat_type}</p>
                  </td>
                  <td className="text-xs text-slate-400">{botName(group.bot_id)}</td>
                  <td>{group.member_count}</td>
                  <td>
                    <Badge status={group.bot_status === "administrator" ? "enabled" : group.bot_status === "left" ? "error" : "installed"}>
                      {group.bot_status}
                    </Badge>
                  </td>
                  <td className="text-xs text-slate-500">{relativeTime(group.last_activity_at)}</td>
                  <td className="text-right">
                    <Link href={`/groups/${group.id}`} className="inline-flex text-slate-500 hover:text-violet-300">
                      <ChevronRight className="h-4 w-4" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : (
        <EmptyState
          title={search ? "No matching groups" : "No groups connected yet"}
          description={
            search
              ? "Try a different name, username or chat id."
              : "Groups are recorded automatically the first time a bot receives an update from the chat."
          }
          icon={<Users className="h-6 w-6" />}
          action={
            <Link href="/bots" className="btn-primary">
              Manage bots
            </Link>
          }
        />
      )}
    </div>
  );
}
