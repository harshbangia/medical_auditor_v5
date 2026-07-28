"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AppShell, PageHeader } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { createUser, getAdminMetrics, setUserActive } from "@/lib/api";
import type { AdminMetrics } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function AdminPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [metrics, setMetrics] = useState<AdminMetrics | null>(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
    if (!loading && user && user.role !== "admin") router.replace("/dashboard");
  }, [user, loading, router]);

  useEffect(() => {
    if (user?.role === "admin") {
      getAdminMetrics().then(setMetrics).catch(() => setMetrics(null));
    }
  }, [user]);

  async function addUser(e: React.FormEvent) {
    e.preventDefault();
    setMsg("");
    try {
      await createUser(email, password);
      setEmail("");
      setPassword("");
      setMsg("User created");
      const m = await getAdminMetrics();
      setMetrics(m);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Failed");
    }
  }

  if (loading || !user || user.role !== "admin") {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#f7f9fc]">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan-500 border-t-transparent" />
      </div>
    );
  }

  return (
    <AppShell>
      <PageHeader title="Admin" description="Users, metrics, and platform overview" />

      <div className="mb-8 grid gap-4 sm:grid-cols-4">
        {[
          { label: "Users", value: metrics?.total_users },
          { label: "Logins", value: metrics?.total_logins },
          { label: "Cases started", value: metrics?.total_cases },
          { label: "Completed", value: metrics?.completed_cases },
        ].map((s) => (
          <Card key={s.label}>
            <CardContent className="pt-6">
              <p className="text-sm text-slate-500">{s.label}</p>
              <p className="text-2xl font-bold text-slate-900">{s.value ?? "—"}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardContent className="pt-6">
            <h3 className="mb-4 font-semibold text-slate-900">Create user</h3>
            <form onSubmit={addUser} className="space-y-3">
              <Input
                placeholder="Email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
              <Input
                placeholder="Password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
              <Button type="submit">Create user</Button>
              {msg && <p className="text-sm text-cyan-700">{msg}</p>}
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-6">
            <h3 className="mb-4 font-semibold text-slate-900">Users</h3>
            <div className="max-h-80 space-y-2 overflow-y-auto">
              {(metrics?.per_user || []).map((u) => (
                <div
                  key={u.id}
                  className="flex items-center justify-between rounded-xl border border-slate-200 px-3 py-2"
                >
                  <div>
                    <p className="text-sm text-slate-900">{u.email}</p>
                    <p className="text-xs text-slate-500">
                      {u.cases_completed} completed · {u.login_count} logins
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge variant={u.is_active ? "success" : "danger"}>
                      {u.is_active ? "Active" : "Disabled"}
                    </Badge>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={async () => {
                        await setUserActive(u.id, !u.is_active);
                        const m = await getAdminMetrics();
                        setMetrics(m);
                      }}
                    >
                      Toggle
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </AppShell>
  );
}
