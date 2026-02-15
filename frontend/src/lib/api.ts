const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface Recipient {
  name: string;
  phone?: string;
  email?: string;
}

export interface Campaign {
  id: string;
  name: string;
  agent_persona: string;
  conversation_goal: string;
  system_prompt: string;
  escalation_keywords: string[];
  recipients: Recipient[];
  created_at: string;
}

export interface CampaignCreate {
  name: string;
  agent_persona: string;
  conversation_goal: string;
  system_prompt: string;
  escalation_keywords: string[];
  recipients: Recipient[];
}

export interface Conversation {
  id: string;
  campaign_id: string;
  status: "active" | "inactive";
  start_time: string;
  end_time: string | null;
  history: { role: string; content: string }[];
}

export interface CallRecord {
  call_id: string;
  id?: string;
  conversation_id: string;
  campaign_id: string;
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
  campaign_id: string;
  priority: "high" | "medium" | "low";
  status: "open" | "acknowledged";
  reason: string;
  detected_flags: string[];
  created_at: string;
  acknowledged_at: string | null;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Request failed");
  }
  return res.json();
}

// Campaigns
export const createCampaign = (data: CampaignCreate) =>
  request<Campaign>("/campaigns/create", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const listCampaigns = () => request<Campaign[]>("/campaigns");

export const getCampaign = (id: string) =>
  request<Campaign>(`/campaigns/${id}`);

// Conversations
export const createConversation = (campaignId: string) =>
  request<Conversation>(`/campaigns/conversations/create?campaign_id=${campaignId}`, {
    method: "POST",
  });

export const sendTurn = (campaignId: string, conversationId: string, message: string) =>
  request<string>(`/campaigns/${campaignId}/${conversationId}?message=${encodeURIComponent(message)}`, {
    method: "POST",
  });

export const endCall = (campaignId: string, conversationId: string) =>
  request<CallRecord>(`/campaigns/${campaignId}/${conversationId}/end`, {
    method: "POST",
  });

// Calls
function normalizeCall(c: any): CallRecord {
  return { ...c, call_id: c.call_id || c.id };
}

export const listCalls = async () => {
  const raw = await request<any[]>("/calls");
  return raw.map(normalizeCall);
};

export const getCall = async (id: string) => {
  const raw = await request<any>(`/calls/${id}`);
  return normalizeCall(raw);
};

// Escalations
export const listEscalations = () => request<Escalation[]>("/escalations");

export const acknowledgeEscalation = (id: string) =>
  request<Escalation>(`/escalations/${id}/acknowledge`, { method: "PATCH" });
