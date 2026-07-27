"use client";

import { Download, FileText } from "lucide-react";
import { useState } from "react";
import { generatePdf } from "@/lib/api";
import type { AuditReport } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

function str(v: unknown) {
  return v == null || v === "" ? "—" : String(v);
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-cyan-400/90">{title}</h2>
      {children}
    </section>
  );
}

function KV({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-4">
      <dt className="w-44 shrink-0 text-sm text-slate-500">{label}</dt>
      <dd className="text-sm text-slate-200">{str(value)}</dd>
    </div>
  );
}

export function ReportView({ data }: { data: AuditReport }) {
  const [downloading, setDownloading] = useState(false);
  const patient = (data.patient_details as Record<string, unknown>) || {};
  const insurance = (data.insurance_details as Record<string, unknown>) || {};
  const claim = (data.claim_details as Record<string, unknown>) || {};
  const fraud = (data.fraud_abuse as Record<string, unknown>) || {};
  const verification = (data.verification as Record<string, unknown>) || {};
  const docAnalysis = (data.document_analysis as Array<Record<string, unknown>>) || [];
  const timeline = (data.timeline as Array<Record<string, unknown>>) || [];
  const observations = (data.observations as Array<Record<string, unknown>>) || [];
  const deviations = (data.guideline_deviations as Array<Record<string, unknown>>) || [];
  const summary = (data.report_summary as string[]) || [];

  async function downloadPdf() {
    setDownloading(true);
    try {
      const blob = await generatePdf(data);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const name = str(patient.name).replace(/\s+/g, "_");
      a.download = name !== "—" ? `${name}_audit.pdf` : "audit_report.pdf";
      a.click();
      URL.revokeObjectURL(url);
    } finally {
      setDownloading(false);
    }
  }

  const verdict = str(data.compliance_verdict);
  const verdictVariant =
    verdict.toLowerCase().includes("non") || verdict.toLowerCase().includes("not")
      ? "danger"
      : verdict.toLowerCase().includes("partial")
        ? "warning"
        : "success";

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <p className="text-sm text-slate-500">
            Ref: {str(data.audit_ref || data.report_ref)} ·{" "}
            {str(data.report_date || data.audit_date)}
          </p>
          <h2 className="mt-1 text-2xl font-bold text-white">{str(patient.name)}</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            <Badge variant={verdictVariant as "success"}>{verdict}</Badge>
            {str(fraud.risk_level) !== "—" && (
              <Badge variant={str(fraud.risk_level) === "High" ? "danger" : "warning"}>
                Fraud risk: {str(fraud.risk_level)}
              </Badge>
            )}
          </div>
        </div>
        <Button onClick={downloadPdf} disabled={downloading}>
          <Download className="h-4 w-4" />
          {downloading ? "Generating…" : "Download PDF"}
        </Button>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle>Patient</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <KV label="Name" value={patient.name} />
            <KV label="Age" value={patient.age} />
            <KV label="Sex" value={patient.sex} />
          </CardContent>
        </Card>
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle>Claim</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <KV label="Hospital" value={claim.hospital} />
            <KV label="Diagnosis" value={claim.diagnosis} />
            <KV label="Admission" value={claim.date_of_admission} />
            <KV label="Discharge" value={claim.date_of_discharge} />
          </CardContent>
        </Card>
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle>Insurance</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <KV label="Company" value={insurance.insurance_company} />
            <KV label="Policy" value={insurance.policy_number} />
            <KV label="Claim #" value={insurance.claim_incident_number} />
          </CardContent>
        </Card>
      </div>

      {summary.length > 0 && (
        <Section title="Executive summary">
          <Card>
            <CardContent className="pt-6">
              <ul className="space-y-2 text-sm text-slate-300">
                {summary.map((b, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="text-cyan-400">•</span>
                    {b}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </Section>
      )}

      {docAnalysis.length > 0 && (
        <Section title="Document analysis">
          <div className="grid gap-4 md:grid-cols-2">
            {docAnalysis.map((row, i) => (
              <Card key={i}>
                <CardContent className="pt-5">
                  <div className="mb-2 flex items-start gap-2">
                    <FileText className="mt-0.5 h-4 w-4 shrink-0 text-cyan-400" />
                    <div>
                      <p className="font-medium text-white">{str(row.document)}</p>
                      <p className="text-xs text-slate-500">{str(row.document_type)}</p>
                    </div>
                  </div>
                  <p className="text-sm text-slate-400">{str(row.key_content)}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </Section>
      )}

      {timeline.length > 0 && (
        <Section title="Clinical timeline">
          <Card>
            <CardContent className="pt-6">
              <ol className="relative space-y-4 border-l border-slate-700 pl-6">
                {timeline.map((ev, i) => (
                  <li key={i} className="relative">
                    <span className="absolute -left-[1.62rem] top-1.5 h-2.5 w-2.5 rounded-full bg-cyan-400 ring-4 ring-[#070b14]" />
                    <p className="text-xs text-cyan-400/80">{str(ev.date)}</p>
                    <p className="text-sm text-slate-200">{str(ev.event)}</p>
                  </li>
                ))}
              </ol>
            </CardContent>
          </Card>
        </Section>
      )}

      {deviations.length > 0 && (
        <Section title="Guideline deviations">
          <div className="space-y-3">
            {deviations.map((dev, i) => (
              <Card key={i} className="border-l-4 border-l-rose-500/60">
                <CardContent className="pt-5">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <p className="font-medium text-white">{str(dev.issue)}</p>
                    <Badge variant="danger">{str(dev.severity)}</Badge>
                  </div>
                  <p className="text-sm text-slate-400">
                    <span className="text-slate-500">Guideline: </span>
                    {str(dev.guideline_expectation)}
                  </p>
                  <p className="mt-2 text-sm text-slate-300">
                    <span className="text-slate-500">Evidence: </span>
                    {str(dev.case_evidence)}
                  </p>
                </CardContent>
              </Card>
            ))}
          </div>
        </Section>
      )}

      {observations.length > 0 && (
        <Section title="Observations">
          <div className="space-y-3">
            {observations.map((obs, i) => {
              const answer = str(obs.answer);
              const supported = obs.evidence_supported !== false;
              return (
                <Card key={i}>
                  <CardContent className="pt-5">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <p className="font-medium text-white">Q{i + 1}: {str(obs.question)}</p>
                      <Badge
                        variant={
                          answer.toLowerCase().includes("not supported")
                            ? "danger"
                            : answer.toLowerCase().includes("partial")
                              ? "warning"
                              : "success"
                        }
                      >
                        {answer}
                      </Badge>
                      {!supported && <Badge variant="warning">Weak citation</Badge>}
                    </div>
                    <p className="text-sm text-slate-300">{str(obs.analysis)}</p>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </Section>
      )}

      {(verification.notes as string[])?.length > 0 && (
        <Section title="Verification notes">
          <Card>
            <CardContent className="pt-6">
              <ul className="space-y-1 text-sm text-amber-200/90">
                {(verification.notes as string[]).map((n, i) => (
                  <li key={i}>• {n}</li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </Section>
      )}

      <Section title="Auditor conclusion">
        <Card>
          <CardContent className="pt-6">
            <p className="whitespace-pre-line text-sm leading-relaxed text-slate-300">
              {str(data.inference || data.auditor_conclusion)}
            </p>
          </CardContent>
        </Card>
      </Section>
    </div>
  );
}
