"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { filestore, type Shop } from "@/lib/modules";

/** The shop selector shared by every FileStore section. */
export function useShop(botId: number) {
  const shops = useQuery({ queryKey: ["filestore-bots"], queryFn: () => filestore.bots() });
  const [slug, setSlug] = useState<string>("");
  useEffect(() => {
    const own = (shops.data?.shops ?? []).find((shop) => shop.bot_id === botId);
    if (own) setSlug(own.slug);
    else setSlug("");
  }, [shops.data, botId]);
  const shop: Shop | null = (shops.data?.shops ?? []).find((row) => row.slug === slug) ?? null;
  return { shops: shops.data?.shops ?? [], shop, slug, setSlug, loading: shops.isLoading, error: shops.error as Error | null, refetch: shops.refetch };
}

export function ShopPicker({
  shops,
  value,
  onChange,
}: {
  shops: Shop[];
  value: string;
  onChange: (slug: string) => void;
}) {
  if (shops.length === 0) return null;
  return (
    <select className="input max-w-xs" value={value} onChange={(event) => onChange(event.target.value)}>
      {shops.map((shop) => (
        <option key={shop.slug} value={shop.slug}>
          {shop.title} · bot {shop.bot_id}
        </option>
      ))}
    </select>
  );
}
