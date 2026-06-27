import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { apiFetch, setAccessToken, setOnAuthFailure } from "../api/client";
import type { MeResponse, TokenResponse } from "../api/schemas";

type AuthValue = {
  user: MeResponse | null;
  ready: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setOnAuthFailure(() => {
      setAccessToken(null);
      setUser(null);
    });
    // Try to restore a session from the refresh cookie.
    (async () => {
      try {
        const refreshed = await apiFetch<TokenResponse>("/auth/refresh", { method: "POST" });
        setAccessToken(refreshed.access_token);
        const me = await apiFetch<MeResponse>("/auth/me", { auth: true });
        setUser(me);
      } catch {
        /* not logged in */
      } finally {
        setReady(true);
      }
    })();
  }, []);

  async function login(email: string, password: string) {
    const tok = await apiFetch<TokenResponse>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    });
    setAccessToken(tok.access_token);
    const me = await apiFetch<MeResponse>("/auth/me", { auth: true });
    setUser(me);
  }

  async function logout() {
    try {
      await apiFetch("/auth/logout", { method: "POST", auth: true });
    } finally {
      setAccessToken(null);
      setUser(null);
    }
  }

  return (
    <AuthContext.Provider value={{ user, ready, login, logout }}>{children}</AuthContext.Provider>
  );
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
