"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { CalendarClock, Plus, Play, Trash2, Workflow as WorkflowIcon, Zap } from "lucide-react";
import { api, formatDate, relativeTime } from "@/lib/api";
import type { Bot, Page, Schedule, Workflow } from "@/lib/types";
import {
  Badge,
  Card,
  EmptyState,
  ErrorNote,
  Field,
  Modal,
  SectionHeader,
  Skeleton,
  Stat,
  Toggle,
} from "@/components/ui";
import { Tabs } from "@/components/patterns";

type Tab = "workflows" | "scheduler";

const TRIGGERS = ["message", "join", "leave", "callback", "scheduled", "reaction"];
const ACTIONS = [
  "send_message",
  "delete_message",
  "pin_message",
  "warn",
  "mute",
  "ban",
  "kick",
  "notify_admin",
  "reply",
  "log",
];
const FACTS = [
  "message.text",
  "message.length",
  "message.has_link",
  "message.has_invite",
  "message.has_forward",
  "message.has_media",
  "message.mentions",
  "user.is_new_member",
  "user.is_trusted",
  "user.username",
];
const OPS = ["contains", "eq", "ne", "regex", "startswith", "endswith", "gt", "lt", "is_true", "is_false", "exists"];

export default function AutomationPage() {
  return (
    <Suspense fallback={<Skeleton className="h-64" />}>
      <AutomationContent />
    </Suspense>
  );
}

function AutomationContent() {
  const params = useSearchParams();
  const [tab, setTab] = useState<Tab>((params.get("tab") as Tab) || "workflows");
  const [botId, setBotId] = useState<string>("");
  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Page<Bot>>("/bots") });
  const selectedBot = botId || String(bots.data?.items?.[0]?.id ?? "");

  const workflows = useQuery({
    queryKey: ["workflows", selectedBot],
    queryFn: () => api.get<Workflow[]>(`/bots/${selectedBot}/workflows`),
    enabled: Boolean(selectedBot),
  });
  const schedules = useQuery({
    queryKey: ["schedules", selectedBot],
    queryFn: () => api.get<Schedule[]>(`/bots/${selectedBot}/schedules`),
    enabled: Boolean(selectedBot),
  });

  return (
    <div className="space-y-4">
      <SectionHeader
        title="Automation Studio"
        description="Workflows and scheduled tasks are declarative data. The runtime validates every trigger, condition and action before it runs."
        action={
          <select className="input max-w-[240px]" value={selectedBot} onChange={(event) => setBotId(event.target.value)}>
            {(bots.data?.items ?? []).map((bot) => (
              <option key={bot.id} value={String(bot.id)}>
                {bot.name}
              </option>
            ))}
          </select>
        }
      />

      {!bots.isLoading && !bots.data?.items.length ? (
        <EmptyState
          title="Connect a bot first"
          description="Automation is scoped to a bot so identical workflows never cross tenants."
          icon={<WorkflowIcon className="h-6 w-6" />}
        />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Stat label="Workflows" value={workflows.data?.length ?? "…"} icon={<WorkflowIcon className="h-4 w-4" />} />
            <Stat
              label="Enabled"
              value={workflows.data?.filter((workflow) => workflow.enabled).length ?? "…"}
              icon={<Zap className="h-4 w-4" />}
            />
            <Stat label="Scheduled tasks" value={schedules.data?.length ?? "…"} icon={<CalendarClock className="h-4 w-4" />} />
          </div>

          <Tabs
            tabs={[
              { id: "workflows" as Tab, label: "Workflows" },
              { id: "scheduler" as Tab, label: "Scheduler", count: schedules.data?.length },
            ]}
            value={tab}
            onChange={setTab}
          />

          {tab === "workflows" ? (
            <WorkflowsPanel botId={selectedBot} workflows={workflows.data ?? []} loading={workflows.isLoading} />
          ) : (
            <SchedulerPanel botId={selectedBot} schedules={schedules.data ?? []} loading={schedules.isLoading} />
          )}
        </>
      )}
    </div>
  );
}

function WorkflowsPanel({ botId, workflows, loading }: { botId: string; workflows: Workflow[]; loading: boolean }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    name: "",
    event: "message",
    fact: "message.text",
    op: "contains",
    value: "",
    action: "send_message",
    text: "",
  });

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["workflows", botId] });

  const create = useMutation({
    mutationFn: () =>
      api.post<Workflow>(`/bots/${botId}/workflows`, {
        name: form.name,
        description: `Created in Automation Studio: on ${form.event} when ${form.fact} ${form.op} ${form.value}`,
        enabled: true,
        trigger: { event: form.event },
        conditions: [{ fact: form.fact, op: form.op, value: form.value || null }],
        actions: [{ type: form.action, ...(form.text ? { text: form.text } : {}) }],
      }),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      setForm({ ...form, name: "", value: "", text: "" });
      invalidate();
    },
    onError: (err: Error) => setError(err.message),
  });

  const toggle = useMutation({
    mutationFn: (workflow: Workflow) =>
      api.patch(`/bots/${botId}/workflows/${workflow.id}`, {
        name: workflow.name,
        description: workflow.description,
        enabled: !workflow.enabled,
        trigger: workflow.trigger,
        conditions: workflow.conditions,
        actions: workflow.actions,
      }),
    onSuccess: invalidate,
    onError: (err: Error) => setError(err.message),
  });

  const remove = useMutation({
    mutationFn: (id: number) => api.del(`/bots/${botId}/workflows/${id}`),
    onSuccess: invalidate,
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="space-y-3">
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-400">Trigger → condition → action, validated by the automation plugin.</p>
        <button className="btn-primary" onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> New workflow
        </button>
      </div>

      {loading ? (
        <Skeleton className="h-40" />
      ) : workflows.length ? (
        <div className="space-y-2">
          {workflows.map((workflow) => (
            <Card key={workflow.id} className="py-3">
              <div className="flex flex-wrap items-center gap-3">
                <WorkflowIcon className="h-4 w-4 text-violet-300" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-slate-100">{workflow.name}</p>
                  <p className="text-[11px] text-slate-500">
                    on <span className="font-mono">{String(workflow.trigger?.event ?? "—")}</span> ·{" "}
                    {workflow.conditions.length} condition(s) →{" "}
                    {workflow.actions.map((action) => String((action as Record<string, unknown>).type ?? "?")).join(", ")} · v
                    {workflow.version} · {workflow.run_count} runs
                  </p>
                </div>
                <span className="text-[11px] text-slate-500">{relativeTime(workflow.last_run_at)}</span>
                <Toggle checked={workflow.enabled} onChange={() => toggle.mutate(workflow)} label="enabled" />
                <button className="btn-danger px-2 py-1" onClick={() => remove.mutate(workflow.id)} aria-label="Delete workflow">
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            </Card>
          ))}
        </div>
      ) : (
        <EmptyState
          title="No workflows yet"
          description="A workflow reacts to a Telegram event, checks declarative conditions and performs a bounded action."
          icon={<WorkflowIcon className="h-6 w-6" />}
        />
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Create workflow"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button className="btn-primary" onClick={() => create.mutate()} disabled={create.isPending || !form.name}>
              Create
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Name">
            <input className="input" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Welcome boosters" />
          </Field>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Trigger event">
              <select className="input" value={form.event} onChange={(event) => setForm({ ...form, event: event.target.value })}>
                {TRIGGERS.map((trigger) => (
                  <option key={trigger} value={trigger}>
                    {trigger}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Action">
              <select className="input" value={form.action} onChange={(event) => setForm({ ...form, action: event.target.value })}>
                {ACTIONS.map((action) => (
                  <option key={action} value={action}>
                    {action}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Condition fact">
              <select className="input" value={form.fact} onChange={(event) => setForm({ ...form, fact: event.target.value })}>
                {FACTS.map((fact) => (
                  <option key={fact} value={fact}>
                    {fact}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Operator">
              <select className="input" value={form.op} onChange={(event) => setForm({ ...form, op: event.target.value })}>
                {OPS.map((op) => (
                  <option key={op} value={op}>
                    {op}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Value">
              <input className="input" value={form.value} onChange={(event) => setForm({ ...form, value: event.target.value })} />
            </Field>
            <Field label="Message text (send_message)">
              <input className="input" value={form.text} onChange={(event) => setForm({ ...form, text: event.target.value })} placeholder="Thanks for joining, {user}!" />
            </Field>
          </div>
          <p className="text-xs text-slate-500">
            Actions are a fixed, reviewed vocabulary — the studio cannot run arbitrary code.
          </p>
        </div>
      </Modal>
    </div>
  );
}

function SchedulerPanel({ botId, schedules, loading }: { botId: string; schedules: Schedule[]; loading: boolean }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({ name: "Daily announcement", kind: "announcement", cron: "0 9 * * *", text: "" });

  const invalidate = () => void queryClient.invalidateQueries({ queryKey: ["schedules", botId] });

  const create = useMutation({
    mutationFn: () =>
      api.post<Schedule>(`/bots/${botId}/schedules`, {
        name: form.name,
        kind: form.kind,
        cron: form.cron || null,
        payload: form.text ? { text: form.text } : {},
      }),
    onSuccess: () => {
      setOpen(false);
      setError(null);
      invalidate();
    },
    onError: (err: Error) => setError(err.message),
  });

  const cancel = useMutation({
    mutationFn: (id: number) => api.post(`/bots/${botId}/schedules/${id}/cancel`),
    onSuccess: invalidate,
    onError: (err: Error) => setError(err.message),
  });

  return (
    <div className="space-y-3">
      {error ? <ErrorNote message={error} onRetry={() => setError(null)} /> : null}
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-400">
          Cron tasks are claimed with a distributed lock, retried with backoff and marked failed after the attempt limit.
        </p>
        <button className="btn-primary" onClick={() => setOpen(true)}>
          <Plus className="h-4 w-4" /> New task
        </button>
      </div>

      {loading ? (
        <Skeleton className="h-40" />
      ) : schedules.length ? (
        <Card className="p-0">
          <table className="table-base">
            <thead>
              <tr>
                <th>Task</th>
                <th>Kind</th>
                <th>Schedule</th>
                <th>Next run</th>
                <th>Status</th>
                <th>Attempts</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {schedules.map((task) => (
                <tr key={task.id}>
                  <td className="text-slate-100">{task.name}</td>
                  <td className="text-xs">{task.kind}</td>
                  <td className="font-mono text-xs">{task.cron ?? "one-off"}</td>
                  <td className="text-xs text-slate-400">{formatDate(task.next_run_at)}</td>
                  <td>
                    <Badge status={task.status}>{task.status}</Badge>
                  </td>
                  <td className="text-xs">{task.attempts}</td>
                  <td className="text-right">
                    {task.status === "pending" ? (
                      <button className="btn-ghost px-2 py-1 text-xs" onClick={() => cancel.mutate(task.id)}>
                        Cancel
                      </button>
                    ) : (
                      <span className="text-[11px] text-slate-600">{task.last_error ? task.last_error.slice(0, 40) : "—"}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ) : (
        <EmptyState
          title="No scheduled tasks"
          description="Schedules are executed by the scheduler service; a task never runs twice thanks to the per-task lock."
          icon={<CalendarClock className="h-6 w-6" />}
        />
      )}

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title="Schedule a task"
        footer={
          <>
            <button className="btn-ghost" onClick={() => setOpen(false)}>
              Cancel
            </button>
            <button className="btn-primary" onClick={() => create.mutate()} disabled={create.isPending}>
              <Play className="h-4 w-4" /> Schedule
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Name">
            <input className="input" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
          </Field>
          <Field label="Kind">
            <select className="input" value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
              <option value="announcement">announcement</option>
              <option value="workflow">workflow</option>
              <option value="cleanup">cleanup</option>
              <option value="custom">custom</option>
            </select>
          </Field>
          <Field label="Cron expression" hint="Five fields: minute hour day month weekday. Leave empty for a one-off run.">
            <input className="input font-mono" value={form.cron} onChange={(event) => setForm({ ...form, cron: event.target.value })} placeholder="0 9 * * *" />
          </Field>
          <Field label="Announcement text">
            <textarea className="input min-h-[72px]" value={form.text} onChange={(event) => setForm({ ...form, text: event.target.value })} />
          </Field>
        </div>
      </Modal>
    </div>
  );
}
