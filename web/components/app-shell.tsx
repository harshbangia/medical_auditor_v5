"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  FileSearch,
  LayoutDashboard,
  LogOut,
  PlusCircle,
  Shield,
} from "lucide-react";
import { useAuth } from "@/components/auth-provider";
import { cn } from "@/lib/utils";

const nav = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/audit/new", label: "New audit", icon: PlusCircle },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { user, logout } = useAuth();
  const isAdmin = user?.role === "admin";

  return (
    <div className="flex min-h-screen bg-[#070b14] text-slate-100">
      <aside className="fixed inset-y-0 left-0 z-30 flex w-64 flex-col border-r border-slate-800/80 bg-slate-950/90 backdrop-blur-xl">
        <div className="flex items-center gap-3 border-b border-slate-800/80 px-5 py-6">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-400 to-blue-600 shadow-lg shadow-cyan-500/30">
            <Activity className="h-5 w-5 text-white" />
          </div>
          <div>
            <p className="text-sm font-bold tracking-tight text-white">Glowix</p>
            <p className="text-xs text-slate-500">Medical Auditor</p>
          </div>
        </div>

        <nav className="flex-1 space-y-1 p-4">
          {nav.map(({ href, label, icon: Icon }) => {
            const active = pathname === href || pathname.startsWith(href + "/");
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition",
                  active
                    ? "bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-500/30"
                    : "text-slate-400 hover:bg-slate-800/60 hover:text-white",
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            );
          })}
          {isAdmin && (
            <Link
              href="/admin"
              className={cn(
                "flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition",
                pathname.startsWith("/admin")
                  ? "bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-500/30"
                  : "text-slate-400 hover:bg-slate-800/60 hover:text-white",
              )}
            >
              <Shield className="h-4 w-4" />
              Admin
            </Link>
          )}
        </nav>

        <div className="border-t border-slate-800/80 p-4">
          <div className="mb-3 rounded-xl bg-slate-900/60 px-3 py-2">
            <p className="truncate text-xs text-slate-500">Signed in as</p>
            <p className="truncate text-sm font-medium text-slate-200">{user?.email}</p>
          </div>
          <button
            type="button"
            onClick={logout}
            className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-slate-400 transition hover:bg-slate-800 hover:text-white"
          >
            <LogOut className="h-4 w-4" />
            Sign out
          </button>
        </div>
      </aside>

      <main className="ml-64 flex-1">
        <div className="pointer-events-none fixed inset-0 ml-64 overflow-hidden">
          <div className="absolute -left-20 top-0 h-96 w-96 rounded-full bg-cyan-500/10 blur-3xl" />
          <div className="absolute right-0 top-1/3 h-80 w-80 rounded-full bg-blue-600/10 blur-3xl" />
        </div>
        <div className="relative mx-auto max-w-7xl px-8 py-8">{children}</div>
      </main>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="mb-8 flex flex-wrap items-end justify-between gap-4">
      <div>
        <div className="mb-2 flex items-center gap-2 text-cyan-400/80">
          <FileSearch className="h-4 w-4" />
          <span className="text-xs font-semibold uppercase tracking-widest">Compliance</span>
        </div>
        <h1 className="text-3xl font-bold tracking-tight text-white">{title}</h1>
        {description && <p className="mt-1 text-slate-400">{description}</p>}
      </div>
      {action}
    </div>
  );
}
