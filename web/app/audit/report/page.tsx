import { Suspense } from "react";
import ReportPageClient from "./report-client";

export default function ReportPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-[#070b14]">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-cyan-500 border-t-transparent" />
        </div>
      }
    >
      <ReportPageClient />
    </Suspense>
  );
}
