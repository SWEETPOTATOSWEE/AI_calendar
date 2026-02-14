import type {
  AuthStatus,
  CalendarEvent,
  EventPayload,
  RecurringEventPayload,
  GoogleTask,
} from "./types";

export type RevisionState = {
  revision?: number;
  events_revision?: number;
  tasks_revision?: number;
};

export type RevisionedItems<T> = RevisionState & {
  items: T[];
};

export type MutationMeta = {
  new_revision?: number;
  op_id?: string | null;
};

type AgentStepResult = {
  step_id: string;
  intent: string;
  ok: boolean;
  data?: Record<string, unknown>;
  error?: string | null;
};

export type AgentTrace = {
  branch?: string;
  node_outputs?: Record<string, unknown>;
  debug?: {
    enabled?: boolean;
    current_node?: string | null;
  };
  node_timeline?: Array<Record<string, unknown>>;
  llm_outputs?: Array<Record<string, unknown>>;
};

export type AgentRunResponse = {
  version?: string;
  status?: "needs_clarification" | "planned" | "completed" | "failed";
  input_as_text?: string;
  now_iso?: string;
  timezone?: string;
  language?: string;
  confidence?: number;
  question?: string;
  response_text?: string;
  plan?: Array<Record<string, unknown>>;
  issues?: Array<Record<string, unknown>>;
  results?: AgentStepResult[];
  trace?: AgentTrace;
  revision?: number;
  new_revision?: number;
};

export type AgentStreamDeltaEvent = {
  type?: string;
  node?: string;
  delta?: string;
  at?: string;
};

export type AgentStreamStatusEvent = {
  type?: string;
  node?: string;
  status?: string;
  detail?: Record<string, unknown>;
  at?: string;
};

export type AgentStreamHandlers = {
  onDelta?: (event: AgentStreamDeltaEvent) => void;
  onStatus?: (event: AgentStreamStatusEvent) => void;
  onResult?: (result: AgentRunResponse) => void;
};

export type AgentDebugStatus = {
  enabled?: boolean;
  run_id?: string | null;
  status?: string | null;
  current_node?: string | null;
  branch?: string | null;
  node_timeline?: Array<Record<string, unknown>>;
  llm_outputs?: Array<Record<string, unknown>>;
  node_outputs?: Record<string, unknown>;
  error?: string | null;
  started_at?: string | null;
  updated_at?: string | null;
};

const apiUrl = (path: string) => {
  const base = process.env.NEXT_PUBLIC_BACKEND_DIRECT || "";
  const normalizedBase = base.endsWith("/") ? base.slice(0, -1) : base;
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${normalizedBase}${normalizedPath}`;
};

const parseRevisionEnvelope = <T>(
  payload: unknown,
): RevisionedItems<T> => {
  if (Array.isArray(payload)) {
    return { items: payload as T[], revision: 0 };
  }
  if (!payload || typeof payload !== "object") {
    return { items: [], revision: 0 };
  }
  const obj = payload as Record<string, unknown>;
  const items = Array.isArray(obj.items) ? (obj.items as T[]) : [];
  return {
    items,
    revision: typeof obj.revision === "number" ? obj.revision : 0,
    events_revision:
      typeof obj.events_revision === "number" ? obj.events_revision : undefined,
    tasks_revision:
      typeof obj.tasks_revision === "number" ? obj.tasks_revision : undefined,
  };
};

const buildGoogleEventKey = (
  calendarId?: string | null,
  eventId?: string | number | null,
) => {
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
    const message = text || `Request failed: ${res.status}`;
    throw new Error(message);
  }
  return text ? (JSON.parse(text) as T) : ({} as T);
};

export const fetchAuthStatus = async (): Promise<AuthStatus> => {
  const url = apiUrl("/auth/google/status");
  return fetchJson<AuthStatus>(url, { method: "GET" });
};

export const getGoogleStreamUrl = () => apiUrl("/api/google/stream");

export const listEvents = async (
  startDate: string,
  endDate: string,
  _useGoogle?: boolean,
) => {
  const params = new URLSearchParams({ start_date: startDate, end_date: endDate });
  const url = apiUrl(`/api/google/events?${params.toString()}`);
  const raw = await fetchJson<unknown>(url, { method: "GET" });
  const parsed = parseRevisionEnvelope<CalendarEvent>(raw);
  const items = (parsed.items || []).map((event) => ({
    ...event,
    id: buildGoogleEventKey(event.calendar_id, event.google_event_id ?? event.id),
    source: "google" as const,
    google_event_id: event.google_event_id
      ? String(event.google_event_id)
      : String(event.id || ""),
  }));
  return {
    ...parsed,
    items,
  } satisfies RevisionedItems<CalendarEvent>;
};

export const listGoogleTasks = async () => {
  const url = apiUrl("/api/google/tasks");
  const raw = await fetchJson<unknown>(url, { method: "GET" });
  const parsed = parseRevisionEnvelope<Record<string, unknown>>(raw);
  const items: CalendarEvent[] = (parsed.items || []).flatMap((task) => {
    const taskId = typeof task.id === "string" ? task.id.trim() : "";
    if (!taskId) return [];
    const due = typeof task.due === "string" && task.due.trim() ? task.due.trim() : "";
    return [
      {
        id: `task:${taskId}`,
        title: String(task.title || ""),
        start: due || new Date().toISOString(),
        end: due || null,
        description: (typeof task.notes === "string" && task.notes) || "",
        source: "google_task",
        google_event_id: taskId,
        all_day: due ? !due.includes("T") : true,
        task_id: taskId,
        task_status:
          task.status === "completed" || task.status === "needsAction"
            ? task.status
            : "needsAction",
        task_notes: (typeof task.notes === "string" && task.notes) || null,
        task_due: due || null,
        task_completed:
          (typeof task.completed === "string" && task.completed) || null,
        task_updated: (typeof task.updated === "string" && task.updated) || null,
      },
    ];
  });
  return {
    ...parsed,
    items,
  } satisfies RevisionedItems<CalendarEvent>;
};

export const createEvent = async (payload: EventPayload) => {
  const url = apiUrl("/api/events");
  const created = await fetchJson<CalendarEvent & MutationMeta>(url, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  const googleId = created.google_event_id
    ? String(created.google_event_id)
    : String(created.id || "");
  return {
    ...created,
    id: googleId || created.id,
    source: "google" as const,
    google_event_id: googleId || created.google_event_id || null,
  };
};

export const createRecurringEvent = async (payload: RecurringEventPayload) => {
  const url = apiUrl("/api/google/recurring-events");
  return fetchJson<{ ok: boolean; google_event_id?: string } & MutationMeta>(url, {
    method: "POST",
    body: JSON.stringify(payload),
  });
};

export const updateEvent = async (event: CalendarEvent, payload: EventPayload) => {
  const params = new URLSearchParams();
  if (event.calendar_id) params.set("calendar_id", event.calendar_id);
  const googleId = resolveGoogleEventId(event);
  const url = apiUrl(
    `/api/google/events/${encodeURIComponent(googleId)}${
      params.toString() ? `?${params}` : ""
    }`,
  );
  return fetchJson<{ ok: boolean } & MutationMeta>(url, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
};

export const updateRecurringEvent = async (
  event: CalendarEvent,
  payload: RecurringEventPayload,
): Promise<{ ok: boolean } & MutationMeta> => {
  const _ = { event, payload };
  throw new Error("Recurring events are not supported in this mode.");
};

export const addRecurringException = async (event: CalendarEvent, date: string) => {
  const _ = { event, date };
  throw new Error("Recurring events are not supported in this mode.");
};

export const deleteEvent = async (event: CalendarEvent) => {
  const params = new URLSearchParams();
  if (event.calendar_id) params.set("calendar_id", event.calendar_id);
  const googleId = resolveGoogleEventId(event);
  const url = apiUrl(
    `/api/google/events/${encodeURIComponent(googleId)}${
      params.toString() ? `?${params}` : ""
    }`,
  );
  return fetchJson<{ ok: boolean } & MutationMeta>(url, { method: "DELETE" });
};

export const deleteGoogleEventById = async (
  eventId: string,
  calendarId?: string | null,
) => {
  const params = new URLSearchParams();
  if (calendarId) params.set("calendar_id", calendarId);
  const url = apiUrl(
    `/api/google/events/${encodeURIComponent(eventId)}${
      params.toString() ? `?${params}` : ""
    }`,
  );
  return fetchJson<{ ok: boolean } & MutationMeta>(url, { method: "DELETE" });
};

export const deleteEventsByIds = async (ids: number[]) => {
  const _ = ids;
  throw new Error("Local delete is not supported.");
};

export const listRecentEvents = async () => {
  const url = apiUrl("/api/recent-events");
  const raw = await fetchJson<unknown>(url, { method: "GET" });
  const parsed = parseRevisionEnvelope<CalendarEvent>(raw);
  const items = (parsed.items || []).map((event) => ({
    ...event,
    source: "google" as const,
    google_event_id: event.google_event_id
      ? String(event.google_event_id)
      : String(event.id || ""),
    id: buildGoogleEventKey(event.calendar_id, event.google_event_id ?? event.id),
  }));
  return {
    ...parsed,
    items,
  } satisfies RevisionedItems<CalendarEvent>;
};

export const runAgent = async (
  input_as_text: string,
  options?: { timezone?: string; dry_run?: boolean; signal?: AbortSignal },
) => {
  const url = apiUrl("/api/agent/run");
  return fetchJson<AgentRunResponse>(url, {
    method: "POST",
    body: JSON.stringify({
      input_as_text,
      timezone: options?.timezone,
      dry_run: Boolean(options?.dry_run),
    }),
    signal: options?.signal,
  });
};

export const runAgentStream = async (
  input_as_text: string,
  options?: {
    timezone?: string;
    dry_run?: boolean;
    signal?: AbortSignal;
    handlers?: AgentStreamHandlers;
  },
): Promise<AgentRunResponse> => {
  const url = apiUrl("/api/agent/run/stream");
  const res = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      input_as_text,
      timezone: options?.timezone,
      dry_run: Boolean(options?.dry_run),
    }),
    signal: options?.signal,
  });
  if (!res.ok) {
    const message = (await res.text()) || `Request failed: ${res.status}`;
    throw new Error(message);
  }
  if (!res.body) {
    throw new Error("Stream response body is empty.");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResult: AgentRunResponse | null = null;

  const processFrame = (frame: string) => {
    const text = frame.trim();
    if (!text) return;
    const lines = text.split("\n");
    let eventType = "message";
    const dataLines: string[] = [];
    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventType = line.slice(6).trim() || "message";
        continue;
      }
      if (line.startsWith("data:")) {
        dataLines.push(line.slice(5).trimStart());
      }
    }
    const dataRaw = dataLines.join("\n").trim();
    if (!dataRaw) return;
    let payload: Record<string, unknown> = {};
    try {
      payload = JSON.parse(dataRaw) as Record<string, unknown>;
    } catch {
      return;
    }

    const payloadType =
      typeof payload.type === "string" && payload.type.trim() ? payload.type.trim() : eventType;
    if (payloadType === "agent_delta") {
      options?.handlers?.onDelta?.({
        type: payloadType,
        node: typeof payload.node === "string" ? payload.node : undefined,
        delta: typeof payload.delta === "string" ? payload.delta : undefined,
        at: typeof payload.at === "string" ? payload.at : undefined,
      });
      return;
    }
    if (payloadType === "agent_status") {
      options?.handlers?.onStatus?.({
        type: payloadType,
        node: typeof payload.node === "string" ? payload.node : undefined,
        status: typeof payload.status === "string" ? payload.status : undefined,
        detail:
          payload.detail && typeof payload.detail === "object" && !Array.isArray(payload.detail)
            ? (payload.detail as Record<string, unknown>)
            : undefined,
        at: typeof payload.at === "string" ? payload.at : undefined,
      });
      return;
    }
    if (payloadType === "agent_result") {
      const result = payload.result;
      if (result && typeof result === "object") {
        finalResult = result as AgentRunResponse;
        options?.handlers?.onResult?.(finalResult);
      }
      return;
    }
    if (payloadType === "agent_error") {
      const message =
        typeof payload.message === "string" && payload.message.trim()
          ? payload.message.trim()
          : "Agent stream failed.";
      throw new Error(message);
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r/g, "");
      let splitIndex = buffer.indexOf("\n\n");
      while (splitIndex !== -1) {
        const frame = buffer.slice(0, splitIndex);
        buffer = buffer.slice(splitIndex + 2);
        processFrame(frame);
        splitIndex = buffer.indexOf("\n\n");
      }
    }
    if (buffer.trim()) {
      processFrame(buffer);
    }
  } finally {
    reader.releaseLock();
  }

  if (finalResult === null) {
    throw new Error("Agent stream completed without final result.");
  }
  return finalResult;
};

export const fetchAgentDebugStatus = async () => {
  const url = apiUrl("/api/agent/debug");
  return fetchJson<AgentDebugStatus>(url, { method: "GET" });
};

export const loginGoogle = () => {
  window.location.href = "/auth/google/login";
};

export const logout = () => {
  window.location.href = "/logout";
};

export const listTasks = async () => {
  const url = apiUrl("/api/google/tasks");
  const raw = await fetchJson<unknown>(url, { method: "GET" });
  const parsed = parseRevisionEnvelope<GoogleTask>(raw);
  return parsed;
};

export const createTask = async (task: {
  title: string;
  notes?: string | null;
  due?: string | null;
}) => {
  const url = apiUrl("/api/google/tasks");
  return fetchJson<GoogleTask & MutationMeta>(url, {
    method: "POST",
    body: JSON.stringify(task),
  });
};

export const updateTask = async (
  taskId: string,
  updates: {
    title?: string | null;
    notes?: string | null;
    due?: string | null;
    status?: string | null;
  },
) => {
  const url = apiUrl(`/api/google/tasks/${taskId}`);
  return fetchJson<GoogleTask & MutationMeta>(url, {
    method: "PATCH",
    body: JSON.stringify(updates),
  });
};

export const deleteTask = async (taskId: string) => {
  const url = apiUrl(`/api/google/tasks/${taskId}`);
  return fetchJson<{ ok: boolean } & MutationMeta>(url, { method: "DELETE" });
};
