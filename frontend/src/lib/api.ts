const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// ---------------------------------------------------------------------------
// Token management (localStorage)
// ---------------------------------------------------------------------------
export function saveToken(token: string): void {
  if (typeof window !== "undefined") localStorage.setItem("auth_token", token);
}

export function loadToken(): string | null {
  return typeof window !== "undefined" ? localStorage.getItem("auth_token") : null;
}

export function clearToken(): void {
  if (typeof window !== "undefined") localStorage.removeItem("auth_token");
}

function getAuthHeaders(): Record<string, string> {
  const token = loadToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export interface Patient {
  id: string;
  name: string;
  phone: string;
  email?: string;
  age?: number;
  gender?: string;
  primary_diagnosis?: string;
  surgical_history?: Record<string, unknown>[];
  medications?: Record<string, unknown>[];
  allergies?: string[];
  vital_signs?: Record<string, unknown>;
  post_op_instructions?: string[];
  emergency_contact?: Record<string, unknown>;
  previous_calls_context?: Record<string, unknown>[];
  next_appointment?: string;
  severity_grade?: string;
  status: string;
  agent_persona?: string;
  conversation_goal?: string;
  system_prompt?: string;
  escalation_keywords?: string[];
  voice_id?: string;
  created_at: string;
  // Populated on detail endpoint for voice UI
  patient_data?: Record<string, unknown>;
}

export interface PatientCreate {
  name: string;
  phone: string;
  email?: string;
  age?: number;
  gender?: string;
  primary_diagnosis?: string;
  surgical_history?: Record<string, unknown>[];
  medications?: Record<string, unknown>[];
  allergies?: string[];
  vital_signs?: Record<string, unknown>;
  post_op_instructions?: string[];
  emergency_contact?: Record<string, unknown>;
  previous_calls_context?: Record<string, unknown>[];
  next_appointment?: string;
  severity_grade?: string;
  agent_persona?: string;
  conversation_goal?: string;
  system_prompt?: string;
  escalation_keywords?: string[];
  voice_id?: string;
}

export interface Conversation {
  id: string;
  patient_id: string;
  status: "active" | "inactive";
  start_time: string;
  end_time: string | null;
  history: { role: string; content: string }[];
}

export interface CallRecord {
  call_id: string;
  id?: string;
  conversation_id: string;
  patient_id: string;
  status: string;
  started_at: string;
  ended_at: string;
  transcript: { role: string; content: string }[];
  summary: string;
  sentiment_score: number;
  detected_flags: string[];
  recommended_action: string;
  escalation_id: string | null;
}

export interface Escalation {
  id: string;
  call_id: string;
  patient_id: string;
  priority: "high" | "medium" | "low";
  status: "open" | "acknowledged";
  reason: string;
  detected_flags: string[];
  created_at: string;
  acknowledged_at: string | null;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
      ...(options?.headers as Record<string, string> | undefined),
    },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------
export interface DoctorCreate {
  email: string;
  password: string;
  name: string;
  specialty?: string;
}

export interface DoctorResponse {
  id: string;
  email: string;
  name: string;
  specialty?: string;
  role: string;
  created_at: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

export const login = (email: string, password: string): Promise<TokenResponse> =>
  request<TokenResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });

export const register = (data: DoctorCreate): Promise<DoctorResponse> =>
  request<DoctorResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const getMe = (): Promise<DoctorResponse> =>
  request<DoctorResponse>("/auth/me");

// Patients
export const createPatient = (data: PatientCreate) =>
  request<Patient>("/patients", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const listPatients = () => request<Patient[]>("/patients");

export const getPatient = (id: string) =>
  request<Patient>(`/patients/${id}`);

export const confirmPatient = (id: string) =>
  request<Patient>(`/patients/${id}/confirm`, { method: "PATCH" });

// Conversations
export const createConversation = (patientId: string) =>
  request<Conversation>(`/patients/conversations/create?patient_id=${patientId}`, {
    method: "POST",
  });

export const sendTurn = (patientId: string, conversationId: string, message: string) =>
  request<string>(`/patients/${patientId}/${conversationId}?message=${encodeURIComponent(message)}`, {
    method: "POST",
  });

export const endCall = (patientId: string, conversationId: string) =>
  request<CallRecord>(`/patients/${patientId}/${conversationId}/end`, {
    method: "POST",
  });

// Calls
// Backend may return `id` instead of `call_id` — normalize before exposing to UI.
type RawCallRecord = Omit<CallRecord, "call_id"> & { call_id?: string };

function normalizeCall(c: RawCallRecord): CallRecord {
  return { ...c, call_id: c.call_id ?? c.id ?? "" };
}

export const listCalls = async () => {
  const raw = await request<RawCallRecord[]>("/calls");
  return raw.map(normalizeCall);
};

export const getCall = async (id: string) => {
  const raw = await request<RawCallRecord>(`/calls/${id}`);
  return normalizeCall(raw);
};

// Escalations
export const listEscalations = () => request<Escalation[]>("/escalations");

export const acknowledgeEscalation = (id: string) =>
  request<Escalation>(`/escalations/${id}/acknowledge`, { method: "PATCH" });
