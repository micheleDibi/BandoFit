import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { useMe } from "../../hooks/useMe";
import { hasAreaProgettista } from "../../lib/roles";

function FullPageSpinner() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-surface" role="status" aria-label="Caricamento">
      <div className="size-8 animate-spin rounded-full border-3 border-brand-200 border-t-brand-500" />
    </div>
  );
}

export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const location = useLocation();

  if (session === undefined) return <FullPageSpinner />;
  if (session === null) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <>{children}</>;
}

export function AdminRoute({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();

  if (isPending) return <FullPageSpinner />;
  if (me?.profile.role !== "admin") return <Navigate to="/app/bandi" replace />;
  return <>{children}</>;
}

export function ProgettistaRoute({ children }: { children: ReactNode }) {
  const { data: me, isPending } = useMe();

  if (isPending) return <FullPageSpinner />;
  if (!hasAreaProgettista(me?.profile.role)) return <Navigate to="/app/bandi" replace />;
  return <>{children}</>;
}
