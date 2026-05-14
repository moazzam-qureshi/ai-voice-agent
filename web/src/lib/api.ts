/**
 * Typed client for our FastAPI backend.
 *
 * In production NEXT_PUBLIC_API_BASE_URL is set to the api subdomain
 * (e.g. https://api.voicegen-ai.example). In local dev it's
 * http://localhost:8000. Empty string is also valid — relative paths
 * hit the same origin (useful if api and web share a domain).
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

export type FitScore = "strong" | "partial" | "weak";

export interface CallStartResponse {
  call_id: string;
  deepgram_token: string;
  deepgram_token_expires_in: number;
  call_session_token: string;
  settings_json: Record<string, unknown>;
}

export interface SearchPassage {
  source: string;
  page: number;
  summary: string;
  content: string;
}

export interface SearchResponse {
  passages: SearchPassage[];
}

export interface WrapUpInput {
  visitor_name: string;
  project_brief: string;
  fit_score: FitScore;
  fit_reasoning: string;
  action_items: string[];
}

export interface CallStatus {
  call_id: string;
  status: string;
  visitor_name: string | null;
  project_brief: string | null;
  fit_score: FitScore | null;
  fit_reasoning: string | null;
  action_items: string[] | null;
  duration_seconds: number | null;
  artifacts: {
    summary_pdf: string | null;
    recording_mp3: string | null;
  };
}

async function request<T>(
  path: string,
  init: RequestInit,
): Promise<T> {
  const url = API_BASE ? `${API_BASE}${path}` : path;
  const resp = await fetch(url, init);
  if (!resp.ok) {
    let detail = "";
    try {
      const body = await resp.json();
      detail = body?.detail ?? "";
    } catch {
      // body wasn't JSON; fall through
    }
    throw new ApiError(resp.status, detail || resp.statusText);
  }
  return (await resp.json()) as T;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

export async function startCall(turnstileToken: string): Promise<CallStartResponse> {
  return request<CallStartResponse>("/call/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ turnstile_token: turnstileToken }),
  });
}

export async function agentSearch(
  callSessionToken: string,
  query: string,
  topK = 3,
): Promise<SearchResponse> {
  return request<SearchResponse>("/agent/search", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Call-Session-Token": callSessionToken,
    },
    body: JSON.stringify({ query, top_k: topK }),
  });
}

export async function agentWrapUp(
  callSessionToken: string,
  input: WrapUpInput,
): Promise<{ acknowledged: boolean }> {
  return request<{ acknowledged: boolean }>("/agent/wrap-up", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Call-Session-Token": callSessionToken,
    },
    body: JSON.stringify(input),
  });
}

export async function uploadRecording(
  callId: string,
  callSessionToken: string,
  blob: Blob,
): Promise<{ download_token: string; size_bytes: number }> {
  const form = new FormData();
  // Filename hint helps the backend's mime sniffing; the actual
  // Content-Type comes from the Blob.
  const ext = blob.type.includes("webm")
    ? "webm"
    : blob.type.includes("mp4")
      ? "m4a"
      : blob.type.includes("ogg")
        ? "ogg"
        : "audio";
  form.append("file", blob, `recording.${ext}`);

  return request<{ download_token: string; size_bytes: number }>(
    `/calls/${encodeURIComponent(callId)}/recording`,
    {
      method: "POST",
      headers: { "X-Call-Session-Token": callSessionToken },
      body: form,
    },
  );
}

export async function getCallStatus(callId: string): Promise<CallStatus> {
  return request<CallStatus>(`/calls/${encodeURIComponent(callId)}`, {
    method: "GET",
  });
}
