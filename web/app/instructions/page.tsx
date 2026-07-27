"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  BookOpen,
  CheckCircle2,
  Download,
  FileUp,
  ListChecks,
  LogOut,
  Sparkles,
} from "lucide-react";
import { AppShell, PageHeader } from "@/components/app-shell";
import { useAuth } from "@/components/auth-provider";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const STEPS = [
  {
    icon: ListChecks,
    title: "Select guideline(s)",
    body: "On New audit, choose every clinical guideline PDF that applies to this case (e.g. viral hepatitis, acute pain abdomen).",
  },
  {
    icon: FileUp,
    title: "Upload case documents",
    body: "Upload PDFs — discharge summary, clinical notes, imaging reports, bills, pre-auth, and photos if they are embedded in the PDF pages.",
  },
  {
    icon: Sparkles,
    title: "Run medical audit",
    body: "Click Run medical audit and wait while documents are mapped, guidelines retrieved, and the report is verified. Progress updates live on screen.",
  },
  {
    icon: CheckCircle2,
    title: "Review the report",
    body: "Check Inference, documentation gaps, guideline deviations, observations, timeline, and document analysis. Flagged items may need closer review.",
  },
  {
    icon: Download,
    title: "Download PDF",
    body: "Use Download PDF for a shareable file named with the patient when available. Save it to your case file as needed.",
  },
  {
    icon: LogOut,
    title: "Sign out on shared machines",
    body: "Use Sign out in the sidebar when finished on a shared workstation so the next user cannot see your session.",
  },
];

export default function InstructionsPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

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
        title="How to run an audit"
        description="Step-by-step guide for Glowix Medical Services auditors"
        action={
          <Link href="/audit/new">
            <Button>Start new audit</Button>
          </Link>
        }
      />

      <div className="mb-8 grid gap-4">
        {STEPS.map((step, i) => {
          const Icon = step.icon;
          return (
            <Card key={step.title}>
              <CardContent className="flex gap-4 pt-6">
                <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-500/30">
                  <Icon className="h-5 w-5" />
                </div>
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
                    Step {i + 1}
                  </p>
                  <h3 className="mt-0.5 text-lg font-semibold text-white">{step.title}</h3>
                  <p className="mt-1 text-sm leading-relaxed text-slate-400">{step.body}</p>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card className="border-cyan-500/20 bg-cyan-500/5">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <BookOpen className="h-5 w-5 text-cyan-400" />
            Tips for best results
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm leading-relaxed text-slate-300">
          <p>
            Prefer searchable PDFs where possible. If clinical photos or scans are only images,
            ensure they are inside the PDF pages you upload so the system can analyze them.
          </p>
          <p>
            Choose guidelines that match the case diagnosis — mismatched specialty guidelines are
            blocked before the audit runs.
          </p>
          <p>
            Large scanned packs take longer (OCR + vision). Keep the tab open until progress
            reaches 100%.
          </p>
          <p>
            Admin users can manage accounts and download past audit PDFs from the Admin page.
          </p>
        </CardContent>
      </Card>
    </AppShell>
  );
}
