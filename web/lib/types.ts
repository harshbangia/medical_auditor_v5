export type User = {
  id: number;
  email: string;
  role: string;
  is_active: boolean;
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  user: User;
};

export type AuditJobStatus = {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed";
  phase?: string;
  progress?: number;
  message?: string;
  result?: AuditReport;
  error?: string;
};

export type AuditReport = Record<string, unknown>;

export type HistoryItem = {
  id: number;
  audit_ref?: string;
  patient_name?: string;
  created_at?: string;
  file_count?: number;
  report?: AuditReport;
};

export type AdminMetrics = {
  total_users: number;
  total_logins: number;
  total_cases: number;
  completed_cases: number;
  per_user: Array<{
    id: number;
    email: string;
    role: string;
    is_active: boolean;
    login_count: number;
    cases_started: number;
    cases_completed: number;
  }>;
};
