"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronRight, FileText, Plus } from "lucide-react";
import { AppShell, PageHeader } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { getHistory } from "@/lib/api";
import type { HistoryItem } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export default function DashboardPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [fetching, setFetching] = useState(true);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

  useEffect(() => {
    if (!user) return;
    getHistory()
      .then(setHistory)
      .catch(() => setHistory([]))
      .finally(() => setFetching(false));
  }, [user]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#f7f9fc]">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan-500 border-t-transparent" />
      </div>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="Dashboard"
        description="Recent medical audits and case history"
        action={
          <Link href="/audit/new">
            <Button>
              <Plus className="h-4 w-4" />
              New audit
            </Button>
          </Link>
        }
      />

      <div className="grid gap-4 sm:grid-cols-3">
        {[
          { label: "Total audits", value: history.length },
          { label: "This session", value: user.email.split("@")[0] },
          { label: "Role", value: user.role },
        ].map((stat) => (
          <Card key={stat.label}>
            <CardContent className="pt-6">
              <p className="text-sm text-slate-500">{stat.label}</p>
              <p className="mt-1 text-2xl font-bold capitalize text-slate-900">{stat.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="mt-6">
        <Card className="border-cyan-200 bg-cyan-50/50">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 py-5">
            <div>
              <p className="font-semibold text-slate-900">How to run an audit</p>
              <p className="text-sm text-slate-600">
                Step-by-step guide — guidelines, uploads, review, and PDF download.
              </p>
            </div>
            <Link href="/instructions">
              <Button variant="secondary">View instructions</Button>
            </Link>
          </CardContent>
        </Card>
      </div>

      <div className="mt-8">
        <h2 className="mb-4 text-lg font-semibold text-slate-900">Recent audits</h2>
        {fetching ? (
          <p className="text-slate-500">Loading history…</p>
        ) : history.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center py-12 text-center">
              <FileText className="mb-3 h-10 w-10 text-slate-600" />
              <p className="text-slate-600">No completed audits yet</p>
              <div className="mt-4 flex flex-wrap justify-center gap-3">
                <Link href="/instructions">
                  <Button variant="secondary">Read instructions</Button>
                </Link>
                <Link href="/audit/new">
                  <Button>Start your first audit</Button>
                </Link>
              </div>
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {history.map((item) => (
              <Link key={item.id} href={`/audit/report?id=${item.id}`}>
                <Card className="transition hover:border-cyan-300 hover:shadow-md">
                  <CardContent className="flex items-center justify-between py-4">
                    <div>
                      <p className="font-medium text-slate-900">
                        {item.patient_name || "Unknown patient"}
                      </p>
                      <p className="text-sm text-slate-500">
                        {item.audit_ref || `Audit #${item.id}`} · {item.created_at} ·{" "}
                        {item.file_count || 0} file(s)
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="success">Completed</Badge>
                      <ChevronRight className="h-5 w-5 text-slate-600" />
                    </div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </div>
    </AppShell>
  );
}
