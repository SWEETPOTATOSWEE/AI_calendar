import type { AuthStatus, CalendarEvent, EventPayload, RecurringEventPayload, GoogleTask, TaskPayload, TaskUpdate } from "./types";

type NlpPreviewResponse = Record<string, unknown>;
type NlpDeletePreviewResponse = Record<string, unknown>;
type NlpClassifyResponse = { type?: string };

const apiUrl = (path: string) => {
  // 프론트 프록시 라우트를 통해 백엔드로 전달하므로 상대 경로 사용
  const url = path.startsWith("/") ? path : `/${path}`;
  console.log("[apiUrl] path:", path, "=> url:", url);
  return url;
};

// SSE 스트리밍용 URL (프론트 프록시 라우트 사용)
const streamingUrl = (path: string) => {
  return path.startsWith("/") ? path : `/${path}`;
};

const buildGoogleEventKey = (calendarId?: string | null, eventId?: string | number | null) => {
  if (!eventId) return "";
  if (!calendarId) return String(eventId);
  return `${calendarId}::${eventId}`;
};

const resolveGoogleEventId = (event: CalendarEvent) =>
  event.google_event_id ?? (event.id ? String(event.id) : "");

const fetchJson = async <T>(url: string, options?: RequestInit): Promise<T> => {
  const res = await fetch(url, {
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers || {}),
    },
    ...options,
  });
  const text = await res.text();
  if (!res.ok) {
    const message = text || `요청에 실패했습니다: ${res.status}`;
    throw new Error(message);
  }
  return text ? (JSON.parse(text) as T) : ({} as T);
};

export const fetchAuthStatus = async (): Promise<AuthStatus> => {
  const url = apiUrl("/auth/google/status");
  console.log("[fetchAuthStatus] URL:", url);
  const status = await fetchJson<AuthStatus>(url, { method: "GET" });
  console.log("[fetchAuthStatus] Status:", status);
  return status;
};

export const getGoogleStreamUrl = () => apiUrl("/api/google/stream");

export const listEvents = async (startDate: string, endDate: string, useGoogle: boolean) => {
  console.log("[listEvents] useGoogle:", useGoogle, "startDate:", startDate, "endDate:", endDate);
  if (useGoogle) {
    const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
    const url = apiUrl(`/api/google/events?${params.toString()}`);
    console.log("[listEvents] Fetching Google events from:", url);
    const data = await fetchJson<CalendarEvent[]>(url, { method: "GET" });
    console.log("[listEvents] Google events count:", data?.length || 0);
    return (data || []).map((event) => ({
      ...event,
      id: buildGoogleEventKey(event.calendar_id, event.google_event_id ?? event.id),
      source: "google" as const,
      google_event_id: event.google_event_id ? String(event.google_event_id) : String(event.id || ""),
    }));
  }

  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  const url = apiUrl(`/api/events?${params.toString()}`);
  console.log("[listEvents] Fetching local events from:", url);
  const data = await fetchJson<CalendarEvent[]>(url, { method: "GET" });
  console.log("[listEvents] Local events count:", data?.length || 0);
  return (data || []).map((event) => ({
    ...event,
    source: "local" as const,
  }));
};

export const listGoogleTasks = async () => {
  const url = apiUrl("/api/google/tasks");
  const data = await fetchJson<any[]>(url, { method: "GET" });
  return (data || []).map((task) => ({
    id: `task:${task.id}`,
    title: task.title,
    start: task.due || new Date().toISOString(),
    end: task.due || null,
    description: task.notes || "",
    source: "google_task" as const,
    google_event_id: task.id,
    all_day: task.due ? !task.due.includes("T") : true,
  }));
};

export const createEvent = async (payload: EventPayload) => {
  const url = apiUrl("/api/events");
  const created = await fetchJson<CalendarEvent>(url, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return { ...created, source: "local" as const };
};

export const updateEvent = async (event: CalendarEvent, payload: EventPayload) => {
  if (event.source === "google") {
    const params = new URLSearchParams();
    if (event.calendar_id) params.set("calendar_id", event.calendar_id);
    const googleId = resolveGoogleEventId(event);
    const url = apiUrl(
      `/api/google/events/${encodeURIComponent(googleId)}${params.toString() ? `?${params}` : ""}`
    );
    return fetchJson<{ ok: boolean }>(url, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  }

  const url = apiUrl(`/api/events/${encodeURIComponent(String(event.id))}`);
  const updated = await fetchJson<CalendarEvent>(url, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  return { ...updated, source: "local" as const };
};

export const updateRecurringEvent = async (
  event: CalendarEvent,
  payload: RecurringEventPayload
) => {
  const url = apiUrl(`/api/recurring-events/${encodeURIComponent(String(event.id))}`);
  const { type: _type, ...body } = payload;
  const updated = await fetchJson<CalendarEvent>(url, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  return { ...updated, source: "local" as const };
};

export const addRecurringException = async (event: CalendarEvent, date: string) => {
  const url = apiUrl(`/api/recurring-events/${encodeURIComponent(String(event.id))}/exceptions`);
  return fetchJson<{ ok: boolean }>(url, {
    method: "POST",
    body: JSON.stringify({ date }),
  });
};

export const deleteEvent = async (event: CalendarEvent) => {
  if (event.source === "google") {
    const params = new URLSearchParams();
    if (event.calendar_id) params.set("calendar_id", event.calendar_id);
    const googleId = resolveGoogleEventId(event);
    const url = apiUrl(
      `/api/google/events/${encodeURIComponent(googleId)}${params.toString() ? `?${params}` : ""}`
    );
    return fetchJson<{ ok: boolean }>(url, { method: "DELETE" });
  }

  const url = apiUrl(`/api/events/${encodeURIComponent(String(event.id))}`);
  return fetchJson<{ ok: boolean }>(url, { method: "DELETE" });
};

export const deleteGoogleEventById = async (eventId: string, calendarId?: string | null) => {
  const params = new URLSearchParams();
  if (calendarId) params.set("calendar_id", calendarId);
  const url = apiUrl(
    `/api/google/events/${encodeURIComponent(eventId)}${params.toString() ? `?${params}` : ""}`
  );
  return fetchJson<{ ok: boolean }>(url, { method: "DELETE" });
};

export const deleteEventsByIds = async (ids: number[]) => {
  const url = apiUrl("/api/delete-by-ids");
  return fetchJson<{ ok: boolean; deleted_ids: number[]; count: number }>(url, {
    method: "POST",
    body: JSON.stringify({ ids }),
  });
};

export const listRecentEvents = async () => {
  const url = apiUrl("/api/recent-events");
  const data = await fetchJson<CalendarEvent[]>(url, { method: "GET" });
  return (data || []).map((event) => ({ ...event, source: "local" as const }));
};

export const previewNlp = async (
  text: string,
  images?: string[],
  reasoning_effort?: string,
  model?: string,
  request_id?: string,
  context_confirmed?: boolean
) => {
  const url = apiUrl("/api/nlp-preview");
  return fetchJson<NlpPreviewResponse>(url, {
    method: "POST",
    body: JSON.stringify({ text, images, reasoning_effort, model, request_id, context_confirmed }),
  });
};

export const previewNlpStream = async (
  payload: {
    text: string;
    images?: string[];
    reasoning_effort?: string;
    model?: string;
    request_id?: string;
    context_confirmed?: boolean;
  },
  onMessage: (event: string, data: any) => void
) => {
  const url = streamingUrl("/api/nlp-preview-stream");
  console.log("[SSE] Starting stream request to:", url);
  console.log("[SSE] Payload:", { ...payload, images: payload.images ? `${payload.images.length} images` : undefined });
  
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
    credentials: "include",
  });

  console.log("[SSE] Response status:", response.status);
  console.log("[SSE] Response headers:", Object.fromEntries(response.headers.entries()));

  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Streaming failed: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) {
    console.error("[SSE] No reader available");
    return;
  }

  console.log("[SSE] Starting to read stream...");
  const decoder = new TextDecoder();
  let buffer = "";
  let chunkCount = 0;

  while (true) {
    const { done, value } = await reader.read();
    chunkCount++;
    
    if (done) {
      console.log("[SSE] Stream ended. Total chunks:", chunkCount);
      break;
    }

    const decoded = decoder.decode(value, { stream: true });
    console.log(`[SSE] Chunk ${chunkCount} received, length:`, decoded.length, "bytes:", decoded.substring(0, 100));
    
    buffer += decoded;
    const lines = buffer.split("\n\n");
    buffer = lines.pop() || "";

    console.log(`[SSE] Processing ${lines.length} messages from chunk ${chunkCount}`);

    for (const line of lines) {
      if (!line.trim()) continue;
      
      const eventMatch = line.match(/^event: (.*)$/m);
      const dataMatch = line.match(/^data: (.*)$/m);
      
      if (dataMatch) {
        const event = eventMatch ? eventMatch[1] : "message";
        try {
          const data = JSON.parse(dataMatch[1]);
          console.log(`[SSE] Parsed event: ${event}`, data);
          onMessage(event, data);
        } catch (e) {
          console.error("[SSE] Failed to parse SSE data", e, "Raw:", dataMatch[1]);
        }
      }
    }
  }
  
  console.log("[SSE] Stream processing complete");
};

export const classifyNlp = async (text: string, has_images?: boolean, request_id?: string) => {
  const url = apiUrl("/api/nlp-classify");
  return fetchJson<NlpClassifyResponse>(url, {
    method: "POST",
    body: JSON.stringify({ text, has_images, request_id }),
  });
};

export const applyNlpAdd = async (items: Record<string, unknown>[]) => {
  const url = apiUrl("/nlp-apply-add");
  const data = await fetchJson<CalendarEvent[]>(url, {
    method: "POST",
    body: JSON.stringify({ items }),
  });
  return (data || []).map((event) => ({ ...event, source: "local" as const }));
};

export const previewNlpDelete = async (
  text: string,
  start_date: string,
  end_date: string,
  reasoning_effort?: string,
  model?: string,
  request_id?: string,
  context_confirmed?: boolean
) => {
  const url = apiUrl("/api/nlp-delete-preview");
  return fetchJson<NlpDeletePreviewResponse>(url, {
    method: "POST",
    body: JSON.stringify({
      text,
      start_date,
      end_date,
      reasoning_effort,
      model,
      request_id,
      context_confirmed,
    }),
  });
};

export const applyNlpDelete = async (
  text: string,
  start_date: string,
  end_date: string,
  reasoning_effort?: string,
  model?: string
) => {
  const url = apiUrl("/api/nlp-delete-events");
  return fetchJson<{ ok: boolean; deleted_ids: number[]; count: number }>(url, {
    method: "POST",
    body: JSON.stringify({ text, start_date, end_date, reasoning_effort, model }),
  });
};

export const resetNlpContext = async () => {
  const url = apiUrl("/api/nlp-context/reset");
  return fetchJson<{ ok: boolean }>(url, { method: "POST" });
};

export const interruptNlp = async (request_id?: string) => {
  const url = apiUrl("/api/nlp-interrupt");
  return fetchJson<{ ok: boolean; cancelled?: number }>(url, {
    method: "POST",
    body: JSON.stringify({ request_id }),
  });
};

export const loginGoogle = () => {
  // 항상 프론트엔드 프록시 라우트를 통해 백엔드로 전달
  // 브라우저가 8000 포트를 직접 열지 않도록 하여 Codespaces 보안 경고 방지
  window.location.href = "/auth/google/login";
};

export const enterAdmin = () => {
  window.location.href = "/admin";
};

export const exitAdmin = () => {
  window.location.href = "/admin/exit";
};

export const logout = () => {
  window.location.href = "/logout";
};
// -------------------------
// Google Tasks API
// -------------------------

export const listTasks = async () => {
  const url = apiUrl("/api/google/tasks");
  const data = await fetchJson<any[]>(url, { method: "GET" });
  return data || [];
};

export const createTask = async (task: { title: string; notes?: string | null; due?: string | null }) => {
  const url = apiUrl("/api/google/tasks");
  return fetchJson<any>(url, {
    method: "POST",
    body: JSON.stringify(task),
  });
};

export const updateTask = async (
  taskId: string,
  updates: { title?: string | null; notes?: string | null; due?: string | null; status?: string | null }
) => {
  const url = apiUrl(`/api/google/tasks/${taskId}`);
  return fetchJson<any>(url, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
};

export const deleteTask = async (taskId: string) => {
  const url = apiUrl(`/api/google/tasks/${taskId}`);
  return fetchJson<{ ok: boolean }>(url, { method: "DELETE" });
};
