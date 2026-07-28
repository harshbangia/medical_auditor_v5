"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Upload, X } from "lucide-react";
import { AppShell, PageHeader } from "@/components/app-shell";
import { AuditProgress } from "@/components/audit-progress";
import { ReportView } from "@/components/report-view";
import { useAuth } from "@/components/auth-provider";
import { getAuditStatus, listGuidelines, startAudit } from "@/lib/api";
import type { AuditReport } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

export default function NewAuditPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [files, setFiles] = useState<File[]>([]);
  const [available, setAvailable] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [phase, setPhase] = useState("");
  const [progress, setProgress] = useState(0);
  const [message, setMessage] = useState("");
  const [report, setReport] = useState<AuditReport | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

  useEffect(() => {
    if (!user) return;
    listGuidelines()
      .then(setAvailable)
      .catch(() => setAvailable([]));
  }, [user]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const dropped = Array.from(e.dataTransfer.files).filter((f) =>
      f.name.toLowerCase().endsWith(".pdf"),
    );
    setFiles((prev) => [...prev, ...dropped]);
  }, []);

  async function runAudit() {
    if (!files.length) {
      setError("Upload at least one PDF");
      return;
    }
    if (!selected.length) {
      setError("Select at least one guideline");
      return;
    }
    setError("");
    setSubmitting(true);
    setReport(null);
    try {
      const { job_id } = await startAudit(files, selected);
      setJobId(job_id);
      setPhase("queued");
      setProgress(5);
      setMessage("Audit queued…");

      const poll = async () => {
        const status = await getAuditStatus(job_id);
        setPhase(status.phase || status.status);
        setProgress(status.progress || 0);
        setMessage(status.message || "");

        if (status.status === "completed" && status.result) {
          setReport(status.result);
          sessionStorage.setItem("last_audit_report", JSON.stringify(status.result));
          const sid = String(status.result.session_id || "");
          if (sid) sessionStorage.setItem("last_audit_session_id", sid);
          setSubmitting(false);
          return;
        }
        if (status.status === "failed") {
          setError(status.error || status.message || "Audit failed");
          setSubmitting(false);
          return;
        }
        setTimeout(poll, 2500);
      };
      poll();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start audit");
      setSubmitting(false);
    }
  }

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#f7f9fc]">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan-500 border-t-transparent" />
      </div>
    );
  }

  if (report) {
    const sessionId =
      String(report.session_id || "") ||
      (typeof window !== "undefined"
        ? sessionStorage.getItem("last_audit_session_id")
        : null);
    return (
      <AppShell>
        <PageHeader title="Audit report" description="Review findings and download PDF" />
        <ReportView
          data={report}
          sessionId={sessionId}
          onReportChange={setReport}
        />
      </AppShell>
    );
  }

  return (
    <AppShell>
      <PageHeader
        title="New audit"
        description="Upload case documents and select clinical guidelines"
      />

      {submitting && jobId ? (
        <AuditProgress phase={phase} progress={progress} message={message} />
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <Card>
            <CardContent className="pt-6">
              <h3 className="mb-4 font-semibold text-slate-900">Case documents</h3>
              <div
                onDragOver={(e) => e.preventDefault()}
                onDrop={onDrop}
                className="flex min-h-[200px] cursor-pointer flex-col items-center justify-center rounded-2xl border-2 border-dashed border-slate-300 bg-slate-50 p-8 transition hover:border-cyan-400"
                onClick={() => document.getElementById("file-input")?.click()}
              >
                <Upload className="mb-3 h-10 w-10 text-cyan-600" />
                <p className="text-sm font-medium text-slate-700">Drop PDFs here or click to browse</p>
                <p className="mt-1 text-xs text-slate-500">Discharge summary, bills, pre-auth, labs…</p>
                <input
                  id="file-input"
                  type="file"
                  accept=".pdf"
                  multiple
                  className="hidden"
                  onChange={(e) => {
                    const picked = Array.from(e.target.files || []);
                    setFiles((prev) => [...prev, ...picked]);
                  }}
                />
              </div>
              {files.length > 0 && (
                <ul className="mt-4 space-y-2">
                  {files.map((f, i) => (
                    <li
                      key={`${f.name}-${i}`}
                      className="flex items-center justify-between rounded-lg bg-slate-100 px-3 py-2 text-sm"
                    >
                      <span className="truncate text-slate-700">{f.name}</span>
                      <button
                        type="button"
                        onClick={() => setFiles((prev) => prev.filter((_, j) => j !== i))}
                        className="text-slate-400 hover:text-rose-600"
                      >
                        <X className="h-4 w-4" />
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardContent className="pt-6">
              <h3 className="mb-4 font-semibold text-slate-900">Guidelines</h3>
              <div className="max-h-[280px] space-y-2 overflow-y-auto pr-1">
                {available.length === 0 ? (
                  <p className="text-sm text-slate-500">Loading guidelines…</p>
                ) : (
                  available.map((g) => {
                    const on = selected.includes(g);
                    return (
                      <label
                        key={g}
                        className={`flex cursor-pointer items-start gap-3 rounded-xl border px-3 py-2.5 text-sm transition ${
                          on
                            ? "border-cyan-300 bg-cyan-50 text-cyan-800"
                            : "border-slate-200 text-slate-600 hover:border-slate-300 hover:bg-slate-50"
                        }`}
                      >
                        <input
                          type="checkbox"
                          className="mt-1"
                          checked={on}
                          onChange={() =>
                            setSelected((prev) =>
                              on ? prev.filter((x) => x !== g) : [...prev, g],
                            )
                          }
                        />
                        <span className="break-all">{g}</span>
                      </label>
                    );
                  })
                )}
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {error && (
        <p className="mt-4 rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</p>
      )}

      {!submitting && (
        <div className="mt-6">
          <Button size="lg" onClick={runAudit} disabled={!files.length || !selected.length}>
            Run medical audit
          </Button>
        </div>
      )}
    </AppShell>
  );
}
