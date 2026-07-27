"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { AppShell, PageHeader } from "@/components/app-shell";
import { ReportView } from "@/components/report-view";
import { useAuth } from "@/components/auth-provider";
import { getHistory } from "@/lib/api";
import type { AuditReport } from "@/lib/types";
import { Button } from "@/components/ui/button";

export default function ReportPageClient() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const id = params.get("id");
  const [report, setReport] = useState<AuditReport | null>(null);
  const [fetching, setFetching] = useState(true);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

  useEffect(() => {
    if (!user) return;

    async function load() {
      if (id) {
        const history = await getHistory();
        const item = history.find((h) => String(h.id) === id);
        if (item?.report) {
          setReport(item.report);
          setFetching(false);
          return;
        }
      }
      const cached = sessionStorage.getItem("last_audit_report");
      if (cached) {
        try {
          setReport(JSON.parse(cached));
        } catch {
          /* ignore */
        }
      }
      setFetching(false);
    }

    load().catch(() => setFetching(false));
  }, [user, id]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#070b14]">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan-500 border-t-transparent" />
      </div>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="Audit report"
        description={id ? `Audit #${id}` : "Latest completed audit"}
        action={
          <Link href="/dashboard">
            <Button variant="secondary">Back to dashboard</Button>
          </Link>
        }
      />
      {fetching ? (
        <p className="text-slate-500">Loading report…</p>
      ) : report ? (
        <ReportView data={report} />
      ) : (
        <p className="text-slate-500">Report not found.</p>
      )}
    </AppShell>
  );
}
