import { authHeaders, clearSession } from "./auth";
import type {
  AdminMetrics,
  AuditJobStatus,
  AuditReport,
  HistoryItem,
  LoginResponse,
  User,
} from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") || "/api";

export class ApiError extends Error {
  status: number;
  detail?: string;

  constructor(status: number, message: string, detail?: string) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

async function handle<T>(res: Response): Promise<T> {
  const contentType = res.headers.get("content-type") || "";
  const isJson = contentType.includes("application/json");
  const body = isJson ? await res.json().catch(() => ({})) : {};

  if (res.status === 401) {
    clearSession();
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
    throw new ApiError(401, "Session expired");
  }

  if (!res.ok) {
    const detail =
      (body as { detail?: string }).detail ||
      (body as { error?: string }).error ||
      res.statusText;
    throw new ApiError(res.status, detail || "Request failed", detail);
  }

  return body as T;
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return handle<LoginResponse>(res);
}

export async function getMe(): Promise<User> {
  const res = await fetch(`${API_BASE}/me`, { headers: authHeaders() });
  return handle<User>(res);
}

export async function listGuidelines(refresh = false): Promise<string[]> {
  const res = await fetch(
    `${API_BASE}/guidelines${refresh ? "?refresh=true" : ""}`,
    { headers: authHeaders() },
  );
  const data = await handle<{ guidelines: string[] }>(res);
  return data.guidelines || [];
}

export async function startAudit(
  files: File[],
  guidelines: string[],
): Promise<{ job_id: string }> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  guidelines.forEach((g) => form.append("guidelines", g));

  const res = await fetch(`${API_BASE}/audit`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  const data = await handle<{ job_id: string }>(res);
  return data;
}

export async function getAuditStatus(jobId: string): Promise<AuditJobStatus> {
  const res = await fetch(`${API_BASE}/audit/status/${jobId}`, {
    headers: authHeaders(),
  });
  return handle<AuditJobStatus>(res);
}

export async function getHistory(): Promise<HistoryItem[]> {
  const res = await fetch(`${API_BASE}/history`, { headers: authHeaders() });
  return handle<HistoryItem[]>(res);
}

export async function generatePdf(report: AuditReport): Promise<Blob> {
  const res = await fetch(`${API_BASE}/generate-pdf`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify(report),
  });
  if (!res.ok) {
    throw new ApiError(res.status, "PDF generation failed");
  }
  return res.blob();
}

export async function getAdminMetrics(): Promise<AdminMetrics> {
  const res = await fetch(`${API_BASE}/admin/metrics`, { headers: authHeaders() });
  return handle<AdminMetrics>(res);
}

export async function createUser(
  email: string,
  password: string,
  role = "user",
): Promise<User> {
  const res = await fetch(`${API_BASE}/admin/users`, {
    method: "POST",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, role }),
  });
  return handle<User>(res);
}

export async function setUserActive(userId: number, isActive: boolean) {
  const res = await fetch(`${API_BASE}/admin/users/${userId}`, {
    method: "PATCH",
    headers: { ...authHeaders(), "Content-Type": "application/json" },
    body: JSON.stringify({ is_active: isActive }),
  });
  return handle(res);
}

export async function askFollowUp(
  question: string,
  sessionId: string,
): Promise<{
  mode?: string;
  question?: string;
  answer?: string;
  justification?: string;
  evidence_used?: string[];
  qa_section?: Array<{ question?: string; answer?: string; justification?: string }>;
}> {
  const form = new FormData();
  form.append("question", question);
  form.append("session_id", sessionId);

  const res = await fetch(`${API_BASE}/audit`, {
    method: "POST",
    headers: authHeaders(),
    body: form,
  });
  return handle(res);
}

