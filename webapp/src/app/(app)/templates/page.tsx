"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Boxes, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { Card, EmptyState, ErrorNote, SectionHeader, Skeleton } from "@/components/ui";
import { emoji } from "@/lib/premium-emoji";

export type Template = {
  id: number;
  slug: string;
  name: string;
  description: string;
  category: string;
  icon: string;
  plugins: string[];
  config: Record<string, unknown>;
  is_public: boolean;
};

export default function TemplatesPage() {
  const templates = useQuery({ queryKey: ["templates"], queryFn: () => api.get<Template[]>("/bot-templates"), retry: false });

  return (
    <div className="space-y-5">
      <SectionHeader
        title="Bot templates"
        description="Curated starting points. A template preinstalls plugins and seeds settings — it never bypasses verification."
        action={
          <Link href="/bots/new" className="btn-primary">
            <Sparkles className="h-4 w-4" /> Use a template
          </Link>
        }
      />

      {templates.isError ? <ErrorNote message="Could not load the template catalogue." onRetry={() => templates.refetch()} /> : null}

      {templates.isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <Skeleton className="h-44" />
          <Skeleton className="h-44" />
        </div>
      ) : templates.data && templates.data.length > 0 ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {templates.data.map((template) => (
            <Card key={template.id} hover className="flex flex-col">
              <div className="flex items-center gap-3">
                <div className="grid h-10 w-10 place-items-center rounded-xl bg-violet-500/15 text-lg">
                  {emoji(template.icon)}
                </div>
                <div>
                  <p className="text-sm font-semibold text-white">{template.name}</p>
                  <p className="text-[11px] uppercase tracking-wider text-slate-500">{template.category}</p>
                </div>
              </div>
              <p className="mt-3 flex-1 text-sm text-slate-400">{template.description}</p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {(template.plugins ?? []).map((plugin) => (
                  <span key={plugin} className="badge border-white/10 bg-white/[0.03] text-slate-400">
                    {plugin}
                  </span>
                ))}
              </div>
              <Link href={`/bots/new?template=${template.slug}`} className="btn-ghost mt-4 w-full">
                <Boxes className="h-4 w-4" /> Apply template
              </Link>
            </Card>
          ))}
        </div>
      ) : !templates.isLoading && !templates.isError ? (
        <EmptyState title="No templates seeded" description="Run the platform seed to publish the default templates." />
      ) : null}
    </div>
  );
}
