"use client";

import { motion } from "framer-motion";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

const PHASE_LABELS: Record<string, string> = {
  extracting: "Reading documents",
  map: "Mapping facts per file",
  plan: "Planning audit stages",
  claim: "Extracting claim fields",
  insurance: "Extracting insurance details",
  profile: "Building case profile",
  alignment: "Checking guidelines",
  rag: "Retrieving guidelines",
  ai_audit: "Running medical audit",
  verify: "Verifying evidence",
  done: "Complete",
  failed: "Failed",
};

export function AuditProgress({
  phase,
  progress = 0,
  message,
}: {
  phase?: string;
  progress?: number;
  message?: string;
}) {
  const label = PHASE_LABELS[phase || ""] || phase || "Processing";
  const pct = Math.min(100, Math.max(0, progress || 0));

  return (
    <Card className="overflow-hidden">
      <CardContent className="p-8">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-cyan-50 ring-1 ring-cyan-200">
            <Loader2 className="h-6 w-6 animate-spin text-cyan-700" />
          </div>
          <div className="flex-1">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <h3 className="text-lg font-semibold text-slate-900">Audit in progress</h3>
              <Badge variant="info">{label}</Badge>
            </div>
            <p className="text-sm text-slate-600">{message || "Please wait…"}</p>
            <div className="mt-5 h-2 overflow-hidden rounded-full bg-slate-200">
              <motion.div
                className="h-full rounded-full bg-gradient-to-r from-cyan-500 to-blue-600"
                initial={{ width: 0 }}
                animate={{ width: `${pct}%` }}
                transition={{ duration: 0.4 }}
              />
            </div>
            <p className="mt-2 text-right text-xs text-slate-500">{pct}%</p>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
