import type {
  AgentEvent,
  Conversation,
  ConversationDetail,
  DatasetDetail,
  DatasetSummary,
  Health,
  SqlResultUI,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, message);
  }
  return (res.status === 204 ? undefined : await res.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  health: () => request<Health>("/health"),

  listDatasets: () => request<DatasetSummary[]>("/datasets"),
  getDataset: (id: string) => request<DatasetDetail>(`/datasets/${id}`),
  uploadDataset: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<DatasetSummary>("/datasets", { method: "POST", body: form });
  },
  loadSample: () => request<DatasetSummary>("/datasets/sample", { method: "POST" }),
  deleteDataset: (id: string) => request<void>(`/datasets/${id}`, { method: "DELETE" }),
  preview: (id: string, table: string, limit = 50) =>
    request<SqlResultUI>(`/datasets/${id}/preview?table=${encodeURIComponent(table)}&limit=${limit}`),
  suggestions: (id: string) =>
    request<{ suggestions: string[]; source: "llm" | "heuristic" }>(`/datasets/${id}/suggestions`),

  listConversations: (datasetId: string) =>
    request<Conversation[]>(`/conversations?dataset_id=${encodeURIComponent(datasetId)}`),
  createConversation: (datasetId: string) =>
    request<Conversation>("/conversations", { method: "POST", ...json({ dataset_id: datasetId }) }),
  getConversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),
  renameConversation: (id: string, title: string) =>
    request<Conversation>(`/conversations/${id}`, { method: "PATCH", ...json({ title }) }),
  deleteConversation: (id: string) => request<void>(`/conversations/${id}`, { method: "DELETE" }),
};

/** Parse a text/event-stream body into events. Exported for tests. */
export function* parseSSEChunk(buffer: string): Generator<AgentEvent, string> {
  let rest = buffer;
  let idx: number;
  while ((idx = rest.indexOf("\n\n")) !== -1) {
    const frame = rest.slice(0, idx);
    rest = rest.slice(idx + 2);
    const data = frame
      .split("\n")
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trimStart())
      .join("\n");
    if (data) yield JSON.parse(data) as AgentEvent;
  }
  return rest;
}

/** POST a question and yield agent events as they stream in. */
export async function* askQuestion(
  conversationId: string,
  question: string,
  signal: AbortSignal,
): AsyncGenerator<AgentEvent> {
  const res = await fetch(`/api/conversations/${conversationId}/messages`, {
    method: "POST",
    signal,
    ...json({ question }),
  });
  if (!res.ok || !res.body) {
    let message = `${res.status} ${res.statusText}`;
    try {
      message = (await res.json()).detail ?? message;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, message);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value.replace(/\r\n/g, "\n");
    const it = parseSSEChunk(buffer);
    let r = it.next();
    while (!r.done) {
      yield r.value;
      r = it.next();
    }
    buffer = r.value;
  }
}
