"use client";

import { useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, clearSession, getStoredUser, getToken, setSession } from "./api";
import type { User } from "./types";

type SessionState = {
  user: User | null;
  loading: boolean;
  signInDev: (telegramId: number, username?: string) => Promise<void>;
  signInTelegram: (payload: Record<string, unknown>) => Promise<void>;
  signInWebApp: (initData: string) => Promise<void>;
  signOut: () => Promise<void>;
  refresh: () => Promise<void>;
};

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const refresh = useCallback(async () => {
    if (!getToken()) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await api.me();
      setUser(me);
    } catch {
      clearSession();
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const handler = () => void refresh();
    window.addEventListener("meiko:session", handler);
    return () => window.removeEventListener("meiko:session", handler);
  }, [refresh]);

  const accept = useCallback(
    (payload: { token: string; user: unknown }) => {
      setSession(payload.token, payload.user);
      setUser(payload.user as User);
      router.push("/dashboard");
    },
    [router],
  );

  const value = useMemo<SessionState>(
    () => ({
      user,
      loading,
      refresh,
      signInDev: async (telegramId, username) => accept(await api.loginDev(telegramId, username)),
      signInTelegram: async (payload) => accept(await api.loginTelegram(payload)),
      signInWebApp: async (initData) => accept(await api.loginWebApp(initData)),
      signOut: async () => {
        try {
          await api.logout();
        } catch {
          /* session may already be gone */
        }
        clearSession();
        setUser(null);
        router.push("/login");
      },
    }),
    [user, loading, refresh, accept, router],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionState {
  const context = useContext(SessionContext);
  if (!context) throw new Error("useSession must be used inside SessionProvider");
  return context;
}
