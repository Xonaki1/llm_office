"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";

import { api, refreshAccessToken, setAccessToken } from "@/lib/api";
import type { Me, OrgSummary, TokenPair } from "@/lib/types";

const ORG_STORAGE_KEY = "agents-office.org";

interface AuthState {
  me: Me | null;
  org: OrgSummary | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, orgName: string) => Promise<void>;
  logout: () => Promise<void>;
  selectOrg: (orgId: string) => void;
  reload: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [orgId, setOrgId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadMe = useCallback(async () => {
    const profile = await api.get<Me>("/auth/me");
    setMe(profile);
    setOrgId((current) => {
      const stored = current ?? window.localStorage.getItem(ORG_STORAGE_KEY);
      const valid = profile.orgs.some((o) => o.id === stored);
      return valid ? stored : (profile.orgs[0]?.id ?? null);
    });
    return profile;
  }, []);

  // On first paint the access token is gone (it lives in memory), but the
  // refresh cookie may still be valid — so try to restore the session before
  // deciding the user is signed out.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const token = await refreshAccessToken();
      if (cancelled) return;
      if (token) {
        try {
          await loadMe();
        } catch {
          setAccessToken(null);
        }
      }
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [loadMe]);

  const login = useCallback(
    async (email: string, password: string) => {
      const tokens = await api.post<TokenPair>("/auth/login", { email, password });
      setAccessToken(tokens.access_token);
      await loadMe();
      router.push("/runs");
    },
    [loadMe, router],
  );

  const register = useCallback(
    async (email: string, password: string, orgName: string) => {
      const tokens = await api.post<TokenPair>("/auth/register", {
        email,
        password,
        org_name: orgName,
      });
      setAccessToken(tokens.access_token);
      await loadMe();
      router.push("/agents");
    },
    [loadMe, router],
  );

  const logout = useCallback(async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setAccessToken(null);
      setMe(null);
      setOrgId(null);
      window.localStorage.removeItem(ORG_STORAGE_KEY);
      router.push("/login");
    }
  }, [router]);

  const selectOrg = useCallback((next: string) => {
    setOrgId(next);
    window.localStorage.setItem(ORG_STORAGE_KEY, next);
  }, []);

  const org = useMemo(
    () => me?.orgs.find((o) => o.id === orgId) ?? me?.orgs[0] ?? null,
    [me, orgId],
  );

  const value = useMemo<AuthState>(
    () => ({
      me,
      org,
      loading,
      login,
      register,
      logout,
      selectOrg,
      reload: async () => {
        await loadMe();
      },
    }),
    [me, org, loading, login, register, logout, selectOrg, loadMe],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside AuthProvider");
  return context;
}

/** The current org id, for building API paths. Throws if used before sign-in. */
export function useOrgId(): string {
  const { org } = useAuth();
  if (!org) throw new Error("no organisation selected");
  return org.id;
}

export function canManage(role: string | undefined): boolean {
  return role === "owner" || role === "admin";
}
