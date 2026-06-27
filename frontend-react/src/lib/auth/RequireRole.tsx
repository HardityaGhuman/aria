import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./AuthContext";

export function RequireRole({ role, children }: { role: string; children: ReactNode }) {
  const { user, ready } = useAuth();
  if (!ready) return <div className="p-8 text-text-secondary">Loading…</div>;
  if (user?.role !== role) return <Navigate to="/" replace />;
  return <>{children}</>;
}
