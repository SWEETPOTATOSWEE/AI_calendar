import type { AuthStatus, CalendarEvent, EventPayload, RecurringEventPayload } from "./types";

type NlpPreviewResponse = Record<string, unknown>;
type NlpDeletePreviewResponse = Record<string, unknown>;
type NlpClassifyResponse = { type?: string };

const API_BASE = (process.env.NEXT_PUBLIC_API_BASE || "/api").replace(/\/$/, "");
const BACKEND_BASE = (process.env.NEXT_PUBLIC_BACKEND_BASE || "").replace(/\/$/, "");

const buildUrl = (base: string, path: string) => {
  if (!base) return path;
  return `${base}${path.startsWith("/") ? "" : "/"}${path}`;
};

const apiUrl = (path: string) => buildUrl(API_BASE, path);
const backendUrl = (path: string) => buildUrl(BACKEND_BASE, path);

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
  const url = BACKEND_BASE ? backendUrl("/auth/google/status") : "/auth/google/status";
  return fetchJson<AuthStatus>(url, { method: "GET" });
};

export const getGoogleStreamUrl = () => apiUrl("/google/stream");

export const listEvents = async (startDate: string, endDate: string, useGoogle: boolean) => {
  if (useGoogle) {
    const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
    const url = apiUrl(`/google/events?${params.toString()}`);
    const data = await fetchJson<CalendarEvent[]>(url, { method: "GET" });
    return (data || []).map((event) => ({
      ...event,
      id: buildGoogleEventKey(event.calendar_id, event.google_event_id ?? event.id),
      source: "google" as const,
      google_event_id: event.google_event_id ? String(event.google_event_id) : String(event.id || ""),
    }));
  }

  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  const url = apiUrl(`/events?${params.toString()}`);
  const data = await fetchJson<CalendarEvent[]>(url, { method: "GET" });
  return (data || []).map((event) => ({
    ...event,
    source: "local" as const,
  }));
};

export const createEvent = async (payload: EventPayload) => {
  const url = apiUrl("/events");
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
      `/google/events/${encodeURIComponent(googleId)}${params.toString() ? `?${params}` : ""}`
    );
    return fetchJson<{ ok: boolean }>(url, {
      method: "PATCH",
      body: JSON.stringify(payload),
    });
  }

  const url = apiUrl(`/events/${encodeURIComponent(String(event.id))}`);
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
  const url = apiUrl(`/recurring-events/${encodeURIComponent(String(event.id))}`);
  const { type: _type, ...body } = payload;
  const updated = await fetchJson<CalendarEvent>(url, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  return { ...updated, source: "local" as const };
};

export const addRecurringException = async (event: CalendarEvent, date: string) => {
  const url = apiUrl(`/recurring-events/${encodeURIComponent(String(event.id))}/exceptions`);
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
      `/google/events/${encodeURIComponent(googleId)}${params.toString() ? `?${params}` : ""}`
    );
    return fetchJson<{ ok: boolean }>(url, { method: "DELETE" });
  }

  const url = apiUrl(`/events/${encodeURIComponent(String(event.id))}`);
  return fetchJson<{ ok: boolean }>(url, { method: "DELETE" });
};

export const deleteGoogleEventById = async (eventId: string, calendarId?: string | null) => {
  const params = new URLSearchParams();
  if (calendarId) params.set("calendar_id", calendarId);
  const url = apiUrl(
    `/google/events/${encodeURIComponent(eventId)}${params.toString() ? `?${params}` : ""}`
  );
  return fetchJson<{ ok: boolean }>(url, { method: "DELETE" });
};

export const deleteEventsByIds = async (ids: number[]) => {
  const url = apiUrl("/delete-by-ids");
  return fetchJson<{ ok: boolean; deleted_ids: number[]; count: number }>(url, {
    method: "POST",
    body: JSON.stringify({ ids }),
  });
};

export const listRecentEvents = async () => {
  const url = apiUrl("/recent-events");
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
  const url = apiUrl("/nlp-preview");
  return fetchJson<NlpPreviewResponse>(url, {
    method: "POST",
    body: JSON.stringify({ text, images, reasoning_effort, model, request_id, context_confirmed }),
  });
};

export const classifyNlp = async (text: string, has_images?: boolean, request_id?: string) => {
  const url = apiUrl("/nlp-classify");
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
  const url = apiUrl("/nlp-delete-preview");
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
  const url = apiUrl("/nlp-delete-events");
  return fetchJson<{ ok: boolean; deleted_ids: number[]; count: number }>(url, {
    method: "POST",
    body: JSON.stringify({ text, start_date, end_date, reasoning_effort, model }),
  });
};

export const resetNlpContext = async () => {
  const url = apiUrl("/nlp-context/reset");
  return fetchJson<{ ok: boolean }>(url, { method: "POST" });
};

export const interruptNlp = async (request_id?: string) => {
  const url = apiUrl("/nlp-interrupt");
  return fetchJson<{ ok: boolean; cancelled?: number }>(url, {
    method: "POST",
    body: JSON.stringify({ request_id }),
  });
};

export const loginGoogle = () => {
  const url = BACKEND_BASE ? backendUrl("/auth/google/login") : "/auth/google/login";
  window.location.href = url;
};

export const enterAdmin = () => {
  const url = BACKEND_BASE ? backendUrl("/admin") : "/admin";
  window.location.href = url;
};

export const exitAdmin = () => {
  const url = BACKEND_BASE ? backendUrl("/admin/exit") : "/admin/exit";
  window.location.href = url;
};

export const logout = () => {
  const url = BACKEND_BASE ? backendUrl("/logout") : "/logout";
  window.location.href = url;
};
