"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { loadToken } from "@/lib/api";
import { Sidebar } from "@/components/Sidebar";

/**
 * Handles auth-based routing and conditional layout:
 * - /login: renders full-screen login (no sidebar)
 * - All other routes: requires a token; redirects to /login if missing
 */
export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const isLoginPage = pathname === "/login";

  useEffect(() => {
    if (isLoginPage) return;
    if (!loadToken()) {
      router.replace("/login");
    }
  }, [pathname, router, isLoginPage]);

  // Login page gets no sidebar
  if (isLoginPage) {
    return <>{children}</>;
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <main className="flex-1 overflow-auto">{children}</main>
    </div>
  );
}
