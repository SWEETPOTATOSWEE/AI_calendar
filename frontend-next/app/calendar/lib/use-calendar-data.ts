"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  AuthStatus,
  CalendarEvent,
  EventPayload,
  RecurringEventPayload,
  GoogleTask,
  TaskPayload,
  TaskUpdate,
} from "./types";
import {
  createRecurringEvent,
  createEvent,
  createTask as createGoogleTask,
  deleteEvent,
  deleteTask as deleteGoogleTask,
  deleteGoogleEventById,
  fetchAuthStatus,
  getGoogleStreamUrl,
  listEvents,
  listGoogleTasks,
  type RevisionedItems,
  updateEvent,
  updateTask as updateGoogleTask,
  updateRecurringEvent,
} from "./api";
import { addDays, parseISODateTime, toISODate } from "./date";

const parseDateParts = (value: string | null | undefined) => {
  if (!value) return null;
  const [yearStr, monthStr, dayStr] = value.split("-");
  const year = Number(yearStr);
  const month = Number(monthStr);
  const day = Number(dayStr);
  if (!year || !month || !day) return null;
  return { year, month, day };
};

const toDateOnly = (value: string | null | undefined) => {
  if (!value) return null;
  const datePart = value.split("T")[0];
  const parts = parseDateParts(datePart);
  if (!parts) return null;
  return new Date(parts.year, parts.month - 1, parts.day);
};

const toYearFromIso = (value: string | null | undefined) => {
  if (!value) return null;
  const datePart = value.split("T")[0];
  const parts = parseDateParts(datePart);
  return parts ? parts.year : null;
};

const getYearRange = (year: number) => {
  const start = `${year}-01-01`;
  const end = `${year}-12-31`;
  return { start, end };
};

const getPrefetchYear = (year: number, month: number) => {
  if (month === 1) return year - 1;
  if (month === 12) return year + 1;
  return null;
};

const GOOGLE_CACHE_PREFIX = "google";
const GOOGLE_CACHE_KEY_PREFIX = `${GOOGLE_CACHE_PREFIX}:`;
const SSE_REFRESH_DELAY_MS = 600;
const normalizeId = (value: string | number) => String(value);
const LOCAL_DELTA_GUARD_MS = 5000;

const ERROR_MESSAGES = {
  LOAD_EVENTS: "Failed to load calendar events.",
  CREATE_EVENT: "Failed to create event.",
  UPDATE_EVENT: "Failed to update event.",
  UPDATE_RECURRING: "Failed to update recurring event.",
  DELETE_RECURRING: "Failed to delete recurring event.",
  CREATE_RECURRING: "Failed to create recurring event.",
  DELETE_EVENT: "Failed to delete event.",
  RECURRING_OCCURRENCE_UNSUPPORTED:
    "Recurring occurrence deletion is not supported in Google-only mode.",
} as const;

type EventIdentity = {
  id?: string | number | null;
  google_event_id?: string | number | null;
};

type EventSyncComparable = EventIdentity & {
  title?: string | null;
  start?: string | null;
  end?: string | null;
  location?: string | null;
  description?: string | null;
  attendees?: string[] | null;
  reminders?: number[] | null;
  visibility?: string | null;
  transparency?: string | null;
  timezone?: string | null;
  meeting_url?: string | null;
  color_id?: string | null;
  recurrence?: unknown;
  all_day?: boolean | null;
};

const extractRawEventId = (event: EventIdentity) => {
  const raw = event.google_event_id;
  if ((typeof raw === "string" || typeof raw === "number") && String(raw).trim()) {
    return String(raw).trim();
  }
  const id = event.id;
  if (typeof id === "string" || typeof id === "number") {
    const text = String(id).trim();
    if (!text) return "";
    const splitIdx = text.lastIndexOf("::");
    return splitIdx >= 0 ? text.slice(splitIdx + 2) : text;
  }
  return "";
};

const getMutationKey = (event: EventIdentity) => {
  const raw = extractRawEventId(event);
  if (raw) return raw;
  if (typeof event.id === "string" || typeof event.id === "number") {
    return normalizeId(event.id);
  }
  return "";
};

const buildEventSyncFingerprint = (event: EventSyncComparable) =>
  JSON.stringify({
    title: event.title ?? null,
    start: event.start ?? null,
    end: event.end ?? null,
    location: event.location ?? null,
    description: event.description ?? null,
    attendees: Array.isArray(event.attendees) ? event.attendees : null,
    reminders: Array.isArray(event.reminders) ? event.reminders : null,
    visibility: event.visibility ?? null,
    transparency: event.transparency ?? null,
    timezone: event.timezone ?? null,
    meeting_url: event.meeting_url ?? null,
    color_id: event.color_id ?? null,
    recurrence: event.recurrence ?? null,
    all_day: Boolean(event.all_day),
  });

const toErrorMessage = (error: unknown, fallback: string) =>
  error instanceof Error ? error.message : fallback;

const buildCacheKey = (source: CalendarEvent["source"], year: number) => `${source}:${year}`;
const buildGoogleYearKey = (year: number) => `${GOOGLE_CACHE_PREFIX}:${year}`;

const buildGoogleEventKey = (
  calendarId?: string | null,
  eventId?: string | number | null,
) => {
  if (!eventId && eventId !== 0) return "";
  const idText = String(eventId);
  if (!calendarId) return idText;
  return `${calendarId}::${idText}`;
};
const normalizeGoogleEvent = (raw: unknown): CalendarEvent | null => {
  if (!raw || typeof raw !== "object") return null;
  const item = raw as Record<string, unknown>;
  const start = typeof item.start === "string" ? item.start : "";
  if (!start) return null;
  const eventIdRaw = item.google_event_id ?? item.id;
  if (typeof eventIdRaw !== "string" && typeof eventIdRaw !== "number") return null;
  const eventId = String(eventIdRaw).trim();
  if (!eventId) return null;
  const calendarId = typeof item.calendar_id === "string" ? item.calendar_id : null;
  const key = buildGoogleEventKey(calendarId, eventId);
  if (!key) return null;
  return {
    ...(item as CalendarEvent),
    id: key,
    source: "google",
    calendar_id: calendarId,
    google_event_id: String(eventId),
  };
};

const sortByStart = (events: CalendarEvent[]) =>
  [...events].sort((a, b) => (a.start || "").localeCompare(b.start || ""));

const toTaskFromCalendarEvent = (event: CalendarEvent): GoogleTask | null => {
  if (event.source !== "google_task") return null;
  const taskIdRaw = event.task_id ?? event.google_event_id ?? event.id;
  const taskId = typeof taskIdRaw === "string" ? taskIdRaw.trim() : "";
  if (!taskId) return null;
  const status =
    event.task_status === "completed" || event.task_status === "needsAction"
      ? event.task_status
      : "needsAction";
  return {
    id: taskId,
    title: event.title || "",
    notes:
      event.task_notes != null
        ? event.task_notes
        : event.description ?? null,
    due:
      event.task_due != null
        ? event.task_due
        : event.start ?? null,
    status,
    completed: event.task_completed ?? null,
    updated: event.task_updated ?? null,
  };
};

const extractTasksFromEvents = (events: CalendarEvent[]): GoogleTask[] => {
  const map = new Map<string, GoogleTask>();
  for (const event of events) {
    const task = toTaskFromCalendarEvent(event);
    if (!task) continue;
    map.set(task.id, task);
  }
  return Array.from(map.values()).sort((a, b) => {
    const aDue = a.due || "";
    const bDue = b.due || "";
    if (aDue === bDue) return a.title.localeCompare(b.title);
    return aDue.localeCompare(bDue);
  });
};

const normalizeGoogleTaskEvent = (raw: unknown): CalendarEvent | null => {
  if (!raw || typeof raw !== "object") return null;
  const item = raw as Record<string, unknown>;
  const taskId = typeof item.id === "string" ? item.id.trim() : "";
  if (!taskId) return null;
  const due = typeof item.due === "string" && item.due.trim() ? item.due.trim() : null;
  const start = due || new Date().toISOString();
  return {
    id: `task:${taskId}`,
    title: typeof item.title === "string" ? item.title : "",
    start,
    end: due,
    description: typeof item.notes === "string" ? item.notes : null,
    source: "google_task",
    google_event_id: taskId,
    all_day: due ? !due.includes("T") : true,
    task_id: taskId,
    task_status:
      item.status === "completed" || item.status === "needsAction"
        ? item.status
        : "needsAction",
    task_notes: typeof item.notes === "string" ? item.notes : null,
    task_due: due,
    task_completed: typeof item.completed === "string" ? item.completed : null,
    task_updated: typeof item.updated === "string" ? item.updated : null,
  };
};

const normalizeTaskText = (value: string | null | undefined) => {
  if (value == null) return null;
  const trimmed = value.trim();
  return trimmed.length ? trimmed : null;
};

const normalizeTaskDue = (value: string | null | undefined) => {
  const normalized = normalizeTaskText(value);
  return normalized ?? null;
};

const toTaskCalendarEvent = (task: GoogleTask): CalendarEvent => {
  const taskId = String(task.id || "").trim();
  const due = normalizeTaskDue(task.due);
  const status =
    task.status === "completed" || task.status === "needsAction"
      ? task.status
      : "needsAction";
  const start = due || new Date().toISOString();
  return {
    id: `task:${taskId}`,
    title: task.title || "",
    start,
    end: due,
    description: task.notes ?? null,
    source: "google_task",
    google_event_id: taskId,
    all_day: due ? !due.includes("T") : true,
    task_id: taskId,
    task_status: status,
    task_notes: task.notes ?? null,
    task_due: due,
    task_completed: task.completed ?? null,
    task_updated: task.updated ?? null,
  };
};

const applyPayloadToEvent = (event: CalendarEvent, payload: EventPayload): CalendarEvent => {
  const next = { ...event };
  if (payload.title !== undefined) next.title = payload.title;
  if (payload.start !== undefined) next.start = payload.start;
  if (payload.end !== undefined) next.end = payload.end ?? null;
  if (payload.location !== undefined) next.location = payload.location ?? null;
  if (payload.description !== undefined) next.description = payload.description ?? null;
  if (payload.attendees !== undefined) next.attendees = payload.attendees ?? null;
  if (payload.reminders !== undefined) next.reminders = payload.reminders ?? null;
  if (payload.visibility !== undefined) next.visibility = payload.visibility ?? null;
  if (payload.transparency !== undefined) next.transparency = payload.transparency ?? null;
  if (payload.timezone !== undefined) next.timezone = payload.timezone ?? null;
  if (payload.meeting_url !== undefined) next.meeting_url = payload.meeting_url ?? null;
  if (payload.color_id !== undefined) next.color_id = payload.color_id ?? null;
  if (payload.recurrence !== undefined) next.recurrence = payload.recurrence ?? null;
  if (payload.all_day !== undefined) next.all_day = payload.all_day;
  return next;
};

type CalendarIndexes = {
  byDate: Record<string, CalendarEvent[]>;
  byHour: Record<string, Record<number, CalendarEvent[]>>;
};

type CachedYear = {
  events: CalendarEvent[];
  indexes: CalendarIndexes;
};

const emptyIndexes = (): CalendarIndexes => ({ byDate: {}, byHour: {} });

const resolveInclusiveEndDate = (event: CalendarEvent, startDate: Date) => {
  const parsedEnd = parseISODateTime(event.end || "");
  const dateOnlyEnd = toDateOnly(event.end);
  const endSource = parsedEnd || dateOnlyEnd;
  if (!endSource) return startDate;
  let endDate = new Date(endSource.getFullYear(), endSource.getMonth(), endSource.getDate());
  if (event.all_day && endDate > startDate) {
    const isExclusiveMidnight = parsedEnd
      ? parsedEnd.getHours() === 0 && parsedEnd.getMinutes() === 0
      : true;
    if (isExclusiveMidnight) {
      endDate = addDays(endDate, -1);
    }
  }
  if (endDate < startDate) return startDate;
  return endDate;
};

const getEventDateRange = (event: CalendarEvent) => {
  const start = parseISODateTime(event.start);
  if (!start) return null;
  const startDate = new Date(start.getFullYear(), start.getMonth(), start.getDate());
  const endDate = resolveInclusiveEndDate(event, startDate);
  if (endDate < startDate) return { startDate, endDate: startDate };
  return { startDate, endDate };
};

const addEventToDateIndex = (indexes: CalendarIndexes, event: CalendarEvent) => {
  const range = getEventDateRange(event);
  if (!range) return;
  let cursor = range.startDate;
  while (cursor <= range.endDate) {
    const key = toISODate(cursor);
    const list = indexes.byDate[key] ? [...indexes.byDate[key]] : [];
    list.push(event);
    list.sort((a, b) => (a.start || "").localeCompare(b.start || ""));
    indexes.byDate[key] = list;
    cursor = addDays(cursor, 1);
  }
};

const removeEventFromDateIndex = (indexes: CalendarIndexes, event: CalendarEvent) => {
  const range = getEventDateRange(event);
  if (!range) return;
  let cursor = range.startDate;
  while (cursor <= range.endDate) {
    const key = toISODate(cursor);
    const list = indexes.byDate[key];
    if (list) {
      const next = list.filter((item) => normalizeId(item.id) !== normalizeId(event.id));
      if (next.length) {
        indexes.byDate[key] = next;
      } else {
        delete indexes.byDate[key];
      }
    }
    cursor = addDays(cursor, 1);
  }
};

const addEventToHourIndex = (indexes: CalendarIndexes, event: CalendarEvent) => {
  const start = parseISODateTime(event.start);
  if (!start) return;
  const dayKey = toISODate(start);
  const hour = start.getHours();
  const day = indexes.byHour[dayKey] ? { ...indexes.byHour[dayKey] } : {};
  const list = day[hour] ? [...day[hour]] : [];
  list.push(event);
  list.sort((a, b) => (a.start || "").localeCompare(b.start || ""));
  day[hour] = list;
  indexes.byHour[dayKey] = day;
};

const removeEventFromHourIndex = (indexes: CalendarIndexes, event: CalendarEvent) => {
  const start = parseISODateTime(event.start);
  if (!start) return;
  const dayKey = toISODate(start);
  const hour = start.getHours();
  const day = indexes.byHour[dayKey];
  if (!day || !day[hour]) return;
  const next = day[hour].filter((item) => normalizeId(item.id) !== normalizeId(event.id));
  if (next.length) {
    indexes.byHour[dayKey] = { ...day, [hour]: next };
  } else {
    const rest = { ...day };
    delete rest[hour];
    if (Object.keys(rest).length) {
      indexes.byHour[dayKey] = rest;
    } else {
      delete indexes.byHour[dayKey];
    }
  }
};

const buildIndexes = (events: CalendarEvent[]): CachedYear => {
  const sorted = sortByStart(events);
  const indexes = emptyIndexes();
  sorted.forEach((event) => {
    addEventToDateIndex(indexes, event);
    addEventToHourIndex(indexes, event);
  });
  return { events: sorted, indexes };
};

const buildTempId = () => `tmp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const eventOverlapsRange = (event: CalendarEvent, rangeStart: string, rangeEnd: string) => {
  const startDate = toDateOnly(event.start);
  if (!startDate) return false;
  const endDate = resolveInclusiveEndDate(event, startDate);
  const rangeStartDate = toDateOnly(rangeStart);
  const rangeEndDate = toDateOnly(rangeEnd);
  if (!rangeStartDate || !rangeEndDate) return true;
  return !(endDate < rangeStartDate || startDate > rangeEndDate);
};

const getYearsInRange = (startIso: string, endIso: string) => {
  const startDate = toDateOnly(startIso);
  const endDate = toDateOnly(endIso);
  if (!startDate || !endDate) return [];
  const startYear = startDate.getFullYear();
  const endYear = endDate.getFullYear();
  const from = Math.min(startYear, endYear);
  const to = Math.max(startYear, endYear);
  const years: number[] = [];
  for (let year = from; year <= to; year += 1) {
    years.push(year);
  }
  return years;
};

export type CalendarDataState = {
  events: CalendarEvent[];
  allEvents: CalendarEvent[];
  tasks: GoogleTask[];
  indexes: CalendarIndexes;
  loading: boolean;
  error: string | null;
  authStatus: AuthStatus | null;
  refreshKey: number;
  revision: number;
  eventsRevision: number;
  tasksRevision: number;
};

export type CalendarActions = {
  refresh: (force?: boolean) => Promise<void>;
  refreshIfOutdated: (newRevision?: number | null) => Promise<boolean>;
  create: (payload: EventPayload) => Promise<CalendarEvent | null>;
  createRecurring: (payload: RecurringEventPayload) => Promise<CalendarEvent[] | null>;
  update: (event: CalendarEvent, payload: EventPayload) => Promise<void>;
  updateRecurring: (event: CalendarEvent, payload: RecurringEventPayload) => Promise<void>;
  deleteRecurringOccurrence: (event: CalendarEvent) => Promise<void>;
  remove: (event: CalendarEvent) => Promise<void>;
  ingest: (events: CalendarEvent[]) => void;
  removeByIds: (ids: Array<string | number>) => void;
  ensureRangeLoaded: (rangeStart: string, rangeEnd: string) => Promise<CalendarEvent[]>;
  createTask: (payload: TaskPayload) => Promise<GoogleTask | null>;
  updateTask: (taskId: string, updates: TaskUpdate) => Promise<GoogleTask | null>;
  toggleTaskStatus: (task: GoogleTask) => Promise<GoogleTask | null>;
  deleteTask: (taskId: string) => Promise<boolean>;
};

export const useCalendarData = (rangeStart: string, rangeEnd: string, viewAnchor?: Date) => {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [allEvents, setAllEvents] = useState<CalendarEvent[]>([]);
  const [tasks, setTasks] = useState<GoogleTask[]>([]);
  const [indexes, setIndexes] = useState<CalendarIndexes>(emptyIndexes());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const [revision, setRevision] = useState(0);
  const [eventsRevision, setEventsRevision] = useState(0);
  const [tasksRevision, setTasksRevision] = useState(0);
  const cacheRef = useRef<Record<string, CachedYear>>({});
  const sseRef = useRef<EventSource | null>(null);
  const refreshTimerRef = useRef<number | null>(null);
  const refreshRangeRef = useRef<() => void>(() => {});
  const eventUpdateSeqRef = useRef<Record<string, number>>({});
  const localDeltaGuardRef = useRef<Record<string, { at: number; fingerprint: string }>>({});
  const revisionRef = useRef(0);
  const seenOpIdsRef = useRef<Set<string>>(new Set());
  const seenOpOrderRef = useRef<string[]>([]);

  const useGoogle = useMemo(
    () => {
      const result = Boolean(authStatus?.enabled && authStatus?.has_token);
      console.log("[useCalendarData] useGoogle:", result, "authStatus:", authStatus);
      return result;
    },
    [authStatus]
  );

  const updateRevisionState = useCallback(
    (next?: { revision?: number; events_revision?: number; tasks_revision?: number }) => {
      if (!next || typeof next !== "object") return;
      const revisionValue = typeof next.revision === "number" ? next.revision : undefined;
      const eventsValue =
        typeof next.events_revision === "number" ? next.events_revision : undefined;
      const tasksValue =
        typeof next.tasks_revision === "number" ? next.tasks_revision : undefined;
      if (typeof revisionValue === "number" && revisionValue > revisionRef.current) {
        revisionRef.current = revisionValue;
        setRevision(revisionValue);
      } else if (typeof revisionValue === "number") {
        setRevision((prev) => Math.max(prev, revisionValue));
      }
      if (typeof eventsValue === "number") {
        setEventsRevision((prev) => Math.max(prev, eventsValue));
      }
      if (typeof tasksValue === "number") {
        setTasksRevision((prev) => Math.max(prev, tasksValue));
      }
    },
    []
  );

  const isKnownOpId = useCallback((opId: string | null | undefined) => {
    const clean = typeof opId === "string" ? opId.trim() : "";
    if (!clean) return false;
    if (seenOpIdsRef.current.has(clean)) return true;
    seenOpIdsRef.current.add(clean);
    seenOpOrderRef.current.push(clean);
    if (seenOpOrderRef.current.length > 1000) {
      const stale = seenOpOrderRef.current.shift();
      if (stale) seenOpIdsRef.current.delete(stale);
    }
    return false;
  }, []);

  const fetchEventsWithTasks = useCallback(
    async (start: string, end: string, year: number) => {
      const eventsPromise = listEvents(start, end, useGoogle);
      const tasksPromise = useGoogle
        ? listGoogleTasks().catch(
            (): RevisionedItems<CalendarEvent> => ({ items: [], revision: 0 }),
          )
        : Promise.resolve<RevisionedItems<CalendarEvent>>({ items: [], revision: 0 });
      const [eventsResponse, tasksResponse] = await Promise.all([eventsPromise, tasksPromise]);
      const events = eventsResponse.items || [];
      const tasks = tasksResponse.items || [];
      const filteredTasks = tasks.filter((t) => toYearFromIso(t.start) === year);
      return {
        items: [...events, ...filteredTasks],
        revision: Math.max(
          eventsResponse.revision ?? 0,
          tasksResponse.revision ?? 0,
        ),
        events_revision: eventsResponse.events_revision ?? 0,
        tasks_revision: tasksResponse.tasks_revision ?? 0,
      };
    },
    [useGoogle]
  );

  const activeYear = useMemo(() => {
    if (viewAnchor) return viewAnchor.getFullYear();
    const startDate = toDateOnly(rangeStart);
    const endDate = toDateOnly(rangeEnd);
    if (startDate && endDate) {
      const mid = new Date((startDate.getTime() + endDate.getTime()) / 2);
      return mid.getFullYear();
    }
    if (startDate) return startDate.getFullYear();
    if (endDate) return endDate.getFullYear();
    return new Date().getFullYear();
  }, [rangeStart, rangeEnd, viewAnchor]);
  const activeMonth = useMemo(() => {
    if (viewAnchor) return viewAnchor.getMonth() + 1;
    const startDate = toDateOnly(rangeStart);
    const endDate = toDateOnly(rangeEnd);
    if (startDate && endDate) {
      const mid = new Date((startDate.getTime() + endDate.getTime()) / 2);
      return mid.getMonth() + 1;
    }
    if (startDate) return startDate.getMonth() + 1;
    if (endDate) return endDate.getMonth() + 1;
    return new Date().getMonth() + 1;
  }, [rangeStart, rangeEnd, viewAnchor]);

  const activeKey = useMemo(() => buildGoogleYearKey(activeYear), [activeYear]);
  const activePrefix = GOOGLE_CACHE_KEY_PREFIX;

  const buildAllEvents = useCallback(() => {
    const seen = new Set<string>();
    const merged: CalendarEvent[] = [];
    Object.entries(cacheRef.current).forEach(([key, cache]) => {
      if (!key.startsWith(activePrefix)) return;
      cache.events.forEach((event) => {
        const idKey = `${key}:${normalizeId(event.id)}`;
        if (seen.has(idKey)) return;
        seen.add(idKey);
        merged.push(event);
      });
    });
    return sortByStart(merged);
  }, [activePrefix]);

  const updateCache = useCallback(
    (key: string, nextCache: CachedYear) => {
      cacheRef.current[key] = nextCache;
      if (key === activeKey) {
        setEvents(nextCache.events);
        setIndexes(nextCache.indexes);
      }
      const merged = buildAllEvents();
      setAllEvents(merged);
      setTasks(extractTasksFromEvents(merged));
    },
    [activeKey, buildAllEvents]
  );

  const resetCalendarState = useCallback(() => {
    setEvents([]);
    setIndexes(emptyIndexes());
    setAllEvents([]);
    setTasks([]);
  }, []);

  const syncActiveCacheState = useCallback(() => {
    const activeCache = cacheRef.current[activeKey];
    setEvents(activeCache?.events || []);
    setIndexes(activeCache?.indexes || emptyIndexes());
    const merged = buildAllEvents();
    setAllEvents(merged);
    setTasks(extractTasksFromEvents(merged));
  }, [activeKey, buildAllEvents]);

  const prefetchAdjacentYear = useCallback(
    (year: number, month: number) => {
      const adjacentYear = getPrefetchYear(year, month);
      if (!adjacentYear) return;
      const adjacentKey = buildGoogleYearKey(adjacentYear);
      if (cacheRef.current[adjacentKey]) return;
      const { start: adjStart, end: adjEnd } = getYearRange(adjacentYear);
      fetchEventsWithTasks(adjStart, adjEnd, adjacentYear)
        .then((prefetch) => {
          updateCache(adjacentKey, buildIndexes(prefetch.items));
          updateRevisionState(prefetch);
        })
        .catch(() => {});
    },
    [fetchEventsWithTasks, updateCache, updateRevisionState]
  );

  const upsertEvent = useCallback(
    (key: string, event: CalendarEvent, previousEvent?: CalendarEvent) => {
      const current = cacheRef.current[key] || buildIndexes([]);
      const nextIndexes: CalendarIndexes = {
        byDate: { ...current.indexes.byDate },
        byHour: { ...current.indexes.byHour },
      };
      if (previousEvent) {
        removeEventFromDateIndex(nextIndexes, previousEvent);
        removeEventFromHourIndex(nextIndexes, previousEvent);
      }
      addEventToDateIndex(nextIndexes, event);
      addEventToHourIndex(nextIndexes, event);

      const nextEvents = current.events.filter(
        (item) => normalizeId(item.id) !== normalizeId(event.id)
      );
      nextEvents.push(event);

      updateCache(key, { events: sortByStart(nextEvents), indexes: nextIndexes });
    },
    [updateCache]
  );

  const removeEvent = useCallback(
    (key: string, event: CalendarEvent) => {
      const current = cacheRef.current[key];
      if (!current) return;
      const nextEvents = current.events.filter(
        (item) => normalizeId(item.id) !== normalizeId(event.id)
      );
      const nextIndexes: CalendarIndexes = {
        byDate: { ...current.indexes.byDate },
        byHour: { ...current.indexes.byHour },
      };
      removeEventFromDateIndex(nextIndexes, event);
      removeEventFromHourIndex(nextIndexes, event);
      updateCache(key, { events: sortByStart(nextEvents), indexes: nextIndexes });
    },
    [updateCache]
  );

  const removeEventsMatching = useCallback(
    (matcher: (event: CalendarEvent) => boolean) => {
      Object.entries(cacheRef.current).forEach(([key, current]) => {
        if (!key.startsWith(activePrefix)) return;
        const targets = current.events.filter(matcher);
        if (!targets.length) return;
        const nextIndexes: CalendarIndexes = {
          byDate: { ...current.indexes.byDate },
          byHour: { ...current.indexes.byHour },
        };
        targets.forEach((event) => {
          removeEventFromDateIndex(nextIndexes, event);
          removeEventFromHourIndex(nextIndexes, event);
        });
        const nextEvents = current.events.filter((event) => !matcher(event));
        updateCache(key, { events: sortByStart(nextEvents), indexes: nextIndexes });
      });
    },
    [activePrefix, updateCache]
  );

  const applyGoogleDelta = useCallback(
    (payload: unknown) => {
      if (!payload || typeof payload !== "object") return false;
      const data = payload as Record<string, unknown>;
      const revisionFromPayload =
        typeof data.revision === "number" ? data.revision : null;
      const opId = typeof data.op_id === "string" ? data.op_id : null;
      if (opId && isKnownOpId(opId)) {
        return true;
      }
      if (
        typeof revisionFromPayload === "number" &&
        revisionFromPayload < revisionRef.current
      ) {
        return true;
      }
      const action = typeof data.action === "string" ? data.action : "";

      if (action === "upsert") {
        const normalized = normalizeGoogleEvent(data.event);
        if (!normalized) return false;
        const rawId = normalized.google_event_id || String(normalized.id || "");
        if (!rawId) return false;
        const mutationKey = getMutationKey(normalized);
        if (mutationKey) {
          const guard = localDeltaGuardRef.current[mutationKey];
          if (guard) {
            const age = Date.now() - guard.at;
            if (age <= LOCAL_DELTA_GUARD_MS) {
              const incomingFingerprint = buildEventSyncFingerprint(normalized);
              if (incomingFingerprint !== guard.fingerprint) {
                // Ignore stale or out-of-order server deltas briefly after local optimistic change.
                return true;
              }
              delete localDeltaGuardRef.current[mutationKey];
            } else {
              delete localDeltaGuardRef.current[mutationKey];
            }
          }
        }
        removeEventsMatching((item) => {
          const itemRawId = String(item.google_event_id || item.id || "");
          return (
            itemRawId === rawId ||
            normalizeId(item.id) === normalizeId(normalized.id) ||
            normalizeId(item.id).endsWith(`::${rawId}`)
          );
        });
        const year = toYearFromIso(normalized.start) ?? activeYear;
        upsertEvent(buildGoogleYearKey(year), normalized);
        if (typeof revisionFromPayload === "number") {
          updateRevisionState({
            revision: revisionFromPayload,
            events_revision: revisionFromPayload,
          });
        }
        return true;
      }

      if (action === "delete") {
        const eventId = typeof data.event_id === "string" ? data.event_id : "";
        const calendarId = typeof data.calendar_id === "string" ? data.calendar_id : "";
        if (!eventId) return false;
        const composite = calendarId ? `${calendarId}::${eventId}` : "";
        removeEventsMatching((item) => {
          const itemId = normalizeId(item.id);
          const itemRawId = String(item.google_event_id || item.id || "");
          if (composite && itemId === composite) return true;
          if (itemId === eventId) return true;
          if (itemRawId === eventId) return true;
          if (!calendarId && itemId.endsWith(`::${eventId}`)) return true;
          if (
            calendarId &&
            item.calendar_id === calendarId &&
            itemRawId === eventId
          ) {
            return true;
          }
          return false;
        });
        if (typeof revisionFromPayload === "number") {
          updateRevisionState({
            revision: revisionFromPayload,
            events_revision: revisionFromPayload,
          });
        }
        return true;
      }

      return false;
    },
    [activeYear, isKnownOpId, removeEventsMatching, updateRevisionState, upsertEvent]
  );

  const applyGoogleTaskDelta = useCallback(
    (payload: unknown) => {
      if (!payload || typeof payload !== "object") return false;
      const data = payload as Record<string, unknown>;
      const revisionFromPayload =
        typeof data.revision === "number" ? data.revision : null;
      const opId = typeof data.op_id === "string" ? data.op_id : null;
      if (opId && isKnownOpId(opId)) {
        return true;
      }
      if (
        typeof revisionFromPayload === "number" &&
        revisionFromPayload < revisionRef.current
      ) {
        return true;
      }
      const action = typeof data.action === "string" ? data.action : "";
      if (action === "upsert") {
        const normalized = normalizeGoogleTaskEvent(data.task);
        if (!normalized) return false;
        const rawTaskId = String(
          normalized.task_id || normalized.google_event_id || "",
        ).trim();
        if (!rawTaskId) return false;
        removeEventsMatching((item) => {
          if (item.source !== "google_task") return false;
          const itemTaskId = String(
            item.task_id || item.google_event_id || item.id || "",
          ).trim();
          return (
            itemTaskId === rawTaskId ||
            normalizeId(item.id) === normalizeId(normalized.id)
          );
        });
        const year = toYearFromIso(normalized.start) ?? activeYear;
        upsertEvent(buildGoogleYearKey(year), normalized);
        if (typeof revisionFromPayload === "number") {
          updateRevisionState({
            revision: revisionFromPayload,
            tasks_revision: revisionFromPayload,
          });
        }
        return true;
      }
      if (action === "delete") {
        const taskIdRaw =
          (typeof data.task_id === "string" && data.task_id.trim()) ||
          (typeof data.task === "object" &&
          data.task &&
          typeof (data.task as Record<string, unknown>).id === "string"
            ? String((data.task as Record<string, unknown>).id).trim()
            : "");
        if (!taskIdRaw) return false;
        removeEventsMatching((item) => {
          if (item.source !== "google_task") return false;
          const itemTaskId = String(
            item.task_id || item.google_event_id || item.id || "",
          ).trim();
          return itemTaskId === taskIdRaw || normalizeId(item.id) === `task:${taskIdRaw}`;
        });
        if (typeof revisionFromPayload === "number") {
          updateRevisionState({
            revision: revisionFromPayload,
            tasks_revision: revisionFromPayload,
          });
        }
        return true;
      }
      return false;
    },
    [activeYear, isKnownOpId, removeEventsMatching, updateRevisionState, upsertEvent]
  );

  const applyGoogleDeltaBatch = useCallback(
    (payload: unknown) => {
      if (!payload || typeof payload !== "object") return false;
      const data = payload as Record<string, unknown>;
      const events = data.events;
      if (!Array.isArray(events) || events.length === 0) return false;
      let applied = false;
      events.forEach((item) => {
        if (applyGoogleDelta(item)) {
          applied = true;
        }
      });
      return applied;
    },
    [applyGoogleDelta]
  );

  const refresh = useCallback(
    async (force = false) => {
      if (!rangeStart || !rangeEnd || authLoading) return;
      if (!useGoogle) {
        resetCalendarState();
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const cached = cacheRef.current[activeKey];
        if (cached && !force) {
          syncActiveCacheState();
          prefetchAdjacentYear(activeYear, activeMonth);
          return;
        }
        const { start, end } = getYearRange(activeYear);
        const fetched = await fetchEventsWithTasks(start, end, activeYear);
        updateCache(activeKey, buildIndexes(fetched.items));
        updateRevisionState(fetched);
        prefetchAdjacentYear(activeYear, activeMonth);
      } catch (err) {
        setError(toErrorMessage(err, ERROR_MESSAGES.LOAD_EVENTS));
      } finally {
        setLoading(false);
        setRefreshKey((value) => value + 1);
      }
    },
    [
      rangeStart,
      rangeEnd,
      authLoading,
      useGoogle,
      activeKey,
      activeYear,
      activeMonth,
      fetchEventsWithTasks,
      prefetchAdjacentYear,
      resetCalendarState,
      syncActiveCacheState,
      updateRevisionState,
      updateCache,
    ]
  );

  const refreshIfOutdated = useCallback(
    async (newRevision?: number | null) => {
      if (typeof newRevision !== "number" || !Number.isFinite(newRevision)) {
        return false;
      }
      if (newRevision <= revisionRef.current) {
        updateRevisionState({ revision: newRevision });
        return false;
      }
      await refresh(true);
      return true;
    },
    [refresh, updateRevisionState]
  );

  const refreshRange = useCallback(
    async () => {
      if (!rangeStart || !rangeEnd) return;
      if (!useGoogle) {
        resetCalendarState();
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const tasksPromise = useGoogle
          ? listGoogleTasks().catch(
              (): RevisionedItems<CalendarEvent> => ({ items: [], revision: 0 }),
            )
          : Promise.resolve<RevisionedItems<CalendarEvent>>({ items: [], revision: 0 });
        const [eventsResponse, tasksResponse] = await Promise.all([
          listEvents(rangeStart, rangeEnd, useGoogle),
          tasksPromise,
        ]);
        const combined = [...(eventsResponse.items || []), ...(tasksResponse.items || [])];
        updateRevisionState({
          revision: Math.max(eventsResponse.revision ?? 0, tasksResponse.revision ?? 0),
          events_revision: eventsResponse.events_revision ?? 0,
          tasks_revision: tasksResponse.tasks_revision ?? 0,
        });

        const yearsInRange = new Set<number>();
        getYearsInRange(rangeStart, rangeEnd).forEach((year) => yearsInRange.add(year));
        combined.forEach((event) => {
          const year = toYearFromIso(event.start);
          if (year) yearsInRange.add(year);
        });

        const missingYears: number[] = [];
        yearsInRange.forEach((year) => {
          const key = buildGoogleYearKey(year);
          const current = cacheRef.current[key];
          if (!current) {
            missingYears.push(year);
            return;
          }
          const remaining = current.events.filter(
            (event) => !eventOverlapsRange(event, rangeStart, rangeEnd)
          );
          const additions = combined.filter((event) => toYearFromIso(event.start) === year);
          updateCache(key, buildIndexes([...remaining, ...additions]));
        });

        if (missingYears.length) {
          await Promise.all(
            missingYears.map(async (year) => {
              const { start, end } = getYearRange(year);
              const fullData = await fetchEventsWithTasks(start, end, year);
              updateCache(buildGoogleYearKey(year), buildIndexes(fullData.items));
              updateRevisionState(fullData);
            })
          );
        }

        syncActiveCacheState();
      } catch (err) {
        setError(toErrorMessage(err, ERROR_MESSAGES.LOAD_EVENTS));
      } finally {
        setLoading(false);
        setRefreshKey((value) => value + 1);
      }
    },
    [
      rangeStart,
      rangeEnd,
      useGoogle,
      fetchEventsWithTasks,
      resetCalendarState,
      syncActiveCacheState,
      updateCache,
      updateRevisionState,
    ]
  );

  useEffect(() => {
    refreshRangeRef.current = refreshRange;
  }, [refreshRange]);

  useEffect(() => {
    let mounted = true;
    setAuthLoading(true);
    fetchAuthStatus()
      .then((status) => {
        if (mounted) {
          setAuthStatus(status);
          setAuthLoading(false);
        }
      })
      .catch(() => {
        if (mounted) {
          setAuthStatus({ enabled: false, configured: false, has_token: false });
          setAuthLoading(false);
        }
      });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const scheduleSseRefresh = useCallback(() => {
    if (refreshTimerRef.current !== null) return;
    refreshTimerRef.current = window.setTimeout(() => {
      refreshTimerRef.current = null;
      refreshRangeRef.current();
    }, SSE_REFRESH_DELAY_MS);
  }, []);

  useEffect(() => {
    if (!useGoogle) {
      if (sseRef.current) {
        sseRef.current.close();
        sseRef.current = null;
      }
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
      return;
    }

    const source = new EventSource(getGoogleStreamUrl(), { withCredentials: true });
    sseRef.current = source;
    const maybeScheduleRefresh = (payload?: Record<string, unknown> | null) => {
      if (!payload || typeof payload !== "object") {
        scheduleSseRefresh();
        return;
      }
      const revisionFromPayload =
        typeof payload.revision === "number" ? payload.revision : null;
      const opId = typeof payload.op_id === "string" ? payload.op_id : null;
      if (opId && isKnownOpId(opId)) {
        return;
      }
      if (
        typeof revisionFromPayload === "number" &&
        revisionFromPayload <= revisionRef.current
      ) {
        return;
      }
      scheduleSseRefresh();
    };
    const onSync = (event: Event) => {
      const payload = readPayload(event);
      maybeScheduleRefresh(payload);
    };
    const readPayload = (event: Event) => {
      const data = (event as MessageEvent).data;
      if (typeof data !== "string" || !data) return null;
      try {
        return JSON.parse(data);
      } catch {
        return null;
      }
    };
    const onDelta = (event: Event) => {
      const payload = readPayload(event);
      if (!payload) {
        scheduleSseRefresh();
        return;
      }
      try {
        const applied = applyGoogleDelta(payload);
        if (!applied) {
          maybeScheduleRefresh(payload);
        }
      } catch {
        maybeScheduleRefresh(payload);
      }
    };
    const onTaskDelta = (event: Event) => {
      const payload = readPayload(event);
      if (!payload) {
        scheduleSseRefresh();
        return;
      }
      try {
        const applied = applyGoogleTaskDelta(payload);
        if (!applied) {
          maybeScheduleRefresh(payload);
        }
      } catch {
        maybeScheduleRefresh(payload);
      }
    };
    const onDeltaBatch = (event: Event) => {
      const payload = readPayload(event);
      if (!payload) {
        scheduleSseRefresh();
        return;
      }
      try {
        const applied = applyGoogleDeltaBatch(payload);
        if (!applied) {
          maybeScheduleRefresh(payload);
        }
      } catch {
        maybeScheduleRefresh(payload);
      }
    };
    source.addEventListener("google_sync", onSync);
    source.addEventListener("google_delta", onDelta);
    source.addEventListener("google_task_delta", onTaskDelta);
    source.addEventListener("google_delta_batch", onDeltaBatch);
    source.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        const type = typeof payload?.type === "string" ? payload.type : "";
        if (type === "google_delta") {
          if (!applyGoogleDelta(payload)) {
            scheduleSseRefresh();
          }
          return;
        }
        if (type === "google_delta_batch") {
          if (!applyGoogleDeltaBatch(payload)) {
            maybeScheduleRefresh(payload);
          }
          return;
        }
        if (type === "google_task_delta") {
          if (!applyGoogleTaskDelta(payload)) {
            maybeScheduleRefresh(payload);
          }
          return;
        }
        if (type === "google_sync" || type === "ping" || type === "ready") {
          if (type === "google_sync") maybeScheduleRefresh(payload);
          return;
        }
      } catch {
        // fall through
      }
      scheduleSseRefresh();
    };

    return () => {
      source.removeEventListener("google_sync", onSync);
      source.removeEventListener("google_delta", onDelta);
      source.removeEventListener("google_task_delta", onTaskDelta);
      source.removeEventListener("google_delta_batch", onDeltaBatch);
      source.onmessage = null;
      source.close();
      if (sseRef.current === source) sseRef.current = null;
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    };
  }, [
    applyGoogleDelta,
    applyGoogleDeltaBatch,
    applyGoogleTaskDelta,
    isKnownOpId,
    scheduleSseRefresh,
    useGoogle,
  ]);

  const handleCreate = useCallback(
    async (payload: EventPayload) => {
      setLoading(true);
      setError(null);
      const tempId = buildTempId();
      const optimistic: CalendarEvent = {
        id: tempId,
        title: payload.title,
        start: payload.start,
        end: payload.end ?? null,
        location: payload.location ?? null,
        description: payload.description ?? null,
        attendees: payload.attendees ?? null,
        reminders: payload.reminders ?? null,
        visibility: payload.visibility ?? null,
        transparency: payload.transparency ?? null,
        timezone: payload.timezone ?? null,
        meeting_url: payload.meeting_url ?? null,
        color_id: payload.color_id ?? null,
        recurrence: payload.recurrence ?? null,
        all_day: payload.all_day,
        source: "google",
        google_event_id: null,
        calendar_id: null,
      };
      const optimisticYear = toYearFromIso(optimistic.start) ?? activeYear;
      const optimisticKey = buildGoogleYearKey(optimisticYear);
      upsertEvent(optimisticKey, optimistic);
      try {
        const created = await createEvent(payload);
        if (typeof created.new_revision === "number") {
          updateRevisionState({
            revision: created.new_revision,
            events_revision: created.new_revision,
          });
        }
        removeEvent(optimisticKey, optimistic);
        const localYear = toYearFromIso(created.start) ?? activeYear;
        const googleKey = buildGoogleYearKey(localYear);
        const googleId = created.google_event_id ?? String(created.id || "");
        if (googleId) {
          // create response and SSE delta can arrive in either order and produce
          // id variants (raw id vs calendar::id). Remove all matches first.
          removeEventsMatching((item) => {
            if (item.source !== "google") return false;
            const itemId = normalizeId(item.id);
            const itemRawId = String(item.google_event_id || item.id || "");
            return (
              itemRawId === googleId ||
              itemId === googleId ||
              itemId.endsWith(`::${googleId}`)
            );
          });
        }
        upsertEvent(
          googleKey,
          {
            ...created,
            id: created.calendar_id ? `${created.calendar_id}::${googleId}` : googleId,
            google_event_id: googleId,
            source: "google",
          }
        );
        return created;
      } catch (err) {
        const message = toErrorMessage(err, ERROR_MESSAGES.CREATE_EVENT);
        removeEvent(optimisticKey, optimistic);
        setError(message);
        return null;
      } finally {
        setLoading(false);
      }
    },
    [activeYear, removeEvent, removeEventsMatching, updateRevisionState, upsertEvent]
  );

  const handleUpdate = useCallback(
    async (event: CalendarEvent, payload: EventPayload) => {
      setLoading(true);
      setError(null);
      const mutationKey = getMutationKey(event);
      const mutationSeq = (eventUpdateSeqRef.current[mutationKey] ?? 0) + 1;
      eventUpdateSeqRef.current[mutationKey] = mutationSeq;
      const isLatestMutation = () => eventUpdateSeqRef.current[mutationKey] === mutationSeq;

      const optimistic = applyPayloadToEvent(event, payload);
      localDeltaGuardRef.current[mutationKey] = {
        at: Date.now(),
        fingerprint: buildEventSyncFingerprint(optimistic),
      };
      const previousYear = toYearFromIso(event.start) ?? activeYear;
      const updatedYear = toYearFromIso(optimistic.start) ?? activeYear;
      if (updatedYear !== previousYear) {
        const previousKey = buildCacheKey(event.source, previousYear);
        removeEvent(previousKey, event);
      }
      const optimisticKey = buildCacheKey(event.source, updatedYear);
      upsertEvent(optimisticKey, optimistic, updatedYear === previousYear ? event : undefined);
      try {
        const updateResult = await updateEvent(event, payload);
        if (typeof updateResult?.new_revision === "number") {
          updateRevisionState({
            revision: updateResult.new_revision,
            events_revision: updateResult.new_revision,
          });
        }
        if (!isLatestMutation()) return;
        const updated = applyPayloadToEvent(event, payload);
        removeEvent(optimisticKey, optimistic);
        if (updatedYear !== previousYear) {
          const previousKey = buildCacheKey(event.source, previousYear);
          removeEvent(previousKey, event);
        }
        const targetKey = buildCacheKey(event.source, updatedYear);
        upsertEvent(targetKey, updated, updatedYear === previousYear ? optimistic : undefined);
      } catch (err) {
        if (!isLatestMutation()) return;
        delete localDeltaGuardRef.current[mutationKey];
        const message = toErrorMessage(err, ERROR_MESSAGES.UPDATE_EVENT);
        if (updatedYear !== previousYear) {
          const rollbackKey = buildCacheKey(event.source, updatedYear);
          removeEvent(rollbackKey, optimistic);
          const previousKey = buildCacheKey(event.source, previousYear);
          upsertEvent(previousKey, event);
        } else {
          const targetKey = buildCacheKey(event.source, previousYear);
          upsertEvent(targetKey, event, optimistic);
        }
        setError(message);
      } finally {
        if (isLatestMutation()) {
          delete eventUpdateSeqRef.current[mutationKey];
        }
        setLoading(false);
      }
    },
    [activeYear, removeEvent, updateRevisionState, upsertEvent]
  );

  const handleUpdateRecurring = useCallback(
    async (event: CalendarEvent, payload: RecurringEventPayload) => {
      setLoading(true);
      setError(null);
      try {
        const result = await updateRecurringEvent(event, payload);
        cacheRef.current = {};
        await refreshIfOutdated(result?.new_revision ?? null);
      } catch (err) {
        const message = toErrorMessage(err, ERROR_MESSAGES.UPDATE_RECURRING);
        setError(message);
      } finally {
        setLoading(false);
      }
    },
    [refreshIfOutdated]
  );

  const handleDeleteRecurringOccurrence = useCallback(
    async (event: CalendarEvent) => {
      setLoading(true);
      setError(null);
      const year = toYearFromIso(event.start) ?? activeYear;
      const key = buildCacheKey(event.source, year);
      removeEvent(key, event);
      try {
        // google_event_id is the instance ID (e.g. baseId_20260211T010000Z)
        // so deleting it via the API only cancels this single occurrence.
        const result = await deleteEvent(event);
        cacheRef.current = {};
        await refreshIfOutdated(result?.new_revision ?? null);
        return;
      } catch (err) {
        const message = toErrorMessage(err, ERROR_MESSAGES.DELETE_RECURRING);
        upsertEvent(key, event);
        setError(message);
      } finally {
        setLoading(false);
      }
    },
    [activeYear, refreshIfOutdated, removeEvent, upsertEvent]
  );

  const handleCreateRecurring = useCallback(
    async (payload: RecurringEventPayload) => {
      setLoading(true);
      setError(null);
      try {
        const createdMeta = await createRecurringEvent(payload);
        await refreshIfOutdated(createdMeta?.new_revision ?? null);
        const draft: CalendarEvent = {
          id: createdMeta.google_event_id || buildTempId(),
          title: payload.title,
          start: `${payload.start_date}T${payload.time || "00:00"}`,
          end: payload.time
            ? `${payload.start_date}T${payload.time}`
            : `${toISODate(addDays(new Date(payload.start_date), 1))}T00:00`,
          location: payload.location ?? null,
          description: payload.description ?? null,
          attendees: payload.attendees ?? null,
          reminders: payload.reminders ?? null,
          visibility: payload.visibility ?? null,
          transparency: payload.transparency ?? null,
          timezone: payload.timezone ?? null,
          meeting_url: payload.meeting_url ?? null,
          color_id: payload.color_id ?? null,
          recurrence: payload.recurrence,
          all_day: !payload.time,
          source: "google",
          google_event_id: createdMeta.google_event_id ?? null,
          calendar_id: null,
        };
        return [draft];
      } catch (err) {
        const message = toErrorMessage(err, ERROR_MESSAGES.CREATE_RECURRING);
        setError(message);
        return null;
      } finally {
        setLoading(false);
      }
    },
    [refreshIfOutdated]
  );

  const handleRemove = useCallback(
    async (event: CalendarEvent) => {
      setLoading(true);
      setError(null);
      const year = toYearFromIso(event.start) ?? activeYear;
      const key = buildCacheKey(event.source, year);

      // For recurring event series deletion: delete the base recurring event
      // and remove all instances from the local cache.
      const baseRecurringId = event.recurring_event_id;
      if (baseRecurringId) {
        removeEventsMatching((item) => {
          return item.recurring_event_id === baseRecurringId
            || String(item.google_event_id || item.id || "") === baseRecurringId;
        });
      } else {
        removeEvent(key, event);
      }

      try {
        if (baseRecurringId) {
          const result = await deleteGoogleEventById(baseRecurringId, event.calendar_id);
          cacheRef.current = {};
          await refreshIfOutdated(result?.new_revision ?? null);
        } else {
          const result = await deleteEvent(event);
          cacheRef.current = {};
          await refreshIfOutdated(result?.new_revision ?? null);
        }
        return;
      } catch (err) {
        const message = toErrorMessage(err, ERROR_MESSAGES.DELETE_EVENT);
        if (!baseRecurringId) {
          upsertEvent(key, event);
        }
        setError(message);
        await refresh(true);
      } finally {
        setLoading(false);
      }
    },
    [activeYear, refresh, refreshIfOutdated, removeEvent, removeEventsMatching, upsertEvent]
  );

  const ingestEvents = useCallback(
    (_items: CalendarEvent[]) => {
      void _items;
      refresh(true);
    },
    [refresh]
  );

  const removeByIds = useCallback(
    (ids: Array<string | number>) => {
      const normalized = ids
        .map((item) => String(item || "").trim())
        .filter((item) => item.length > 0);
      if (!normalized.length) return;
      const lookup = new Set(normalized);
      removeEventsMatching((event) => {
        const itemId = normalizeId(event.id);
        const rawId = String(event.google_event_id || event.id || "");
        if (lookup.has(itemId) || lookup.has(rawId)) return true;
        for (const target of lookup) {
          if (itemId.endsWith(`::${target}`)) return true;
        }
        return false;
      });
    },
    [removeEventsMatching]
  );

  const handleCreateTask = useCallback(
    async (payload: TaskPayload) => {
      setLoading(true);
      setError(null);
      const tempTaskId = `tmp-task-${buildTempId()}`;
      const optimisticTask: GoogleTask = {
        id: tempTaskId,
        title: payload.title,
        notes: payload.notes ?? null,
        due: normalizeTaskDue(payload.due),
        status: "needsAction",
      };
      const optimisticEvent = toTaskCalendarEvent(optimisticTask);
      const optimisticYear = toYearFromIso(optimisticEvent.start) ?? activeYear;
      const optimisticKey = buildGoogleYearKey(optimisticYear);
      upsertEvent(optimisticKey, optimisticEvent);
      try {
        const created = await createGoogleTask({
          title: payload.title,
          notes: payload.notes ?? null,
          due: normalizeTaskDue(payload.due),
        });
        if (typeof created.new_revision === "number") {
          updateRevisionState({
            revision: created.new_revision,
            tasks_revision: created.new_revision,
          });
        }
        removeEventsMatching((item) => {
          if (item.source !== "google_task") return false;
          const itemTaskId = String(item.task_id || item.google_event_id || "").trim();
          return itemTaskId === tempTaskId;
        });
        const createdEvent = toTaskCalendarEvent(created);
        const createdYear = toYearFromIso(createdEvent.start) ?? activeYear;
        removeEventsMatching((item) => {
          if (item.source !== "google_task") return false;
          const itemTaskId = String(item.task_id || item.google_event_id || "").trim();
          return itemTaskId === String(created.id).trim();
        });
        upsertEvent(buildGoogleYearKey(createdYear), createdEvent);
        return created;
      } catch (err) {
        removeEventsMatching((item) => {
          if (item.source !== "google_task") return false;
          const itemTaskId = String(item.task_id || item.google_event_id || "").trim();
          return itemTaskId === tempTaskId;
        });
        setError(toErrorMessage(err, "Failed to create task."));
        return null;
      } finally {
        setLoading(false);
      }
    },
    [activeYear, removeEventsMatching, updateRevisionState, upsertEvent]
  );

  const handleUpdateTask = useCallback(
    async (taskId: string, updates: TaskUpdate) => {
      setLoading(true);
      setError(null);
      const cleanTaskId = String(taskId || "").trim();
      if (!cleanTaskId) {
        setLoading(false);
        return null;
      }
      const previousTask = tasks.find((task) => task.id === cleanTaskId) ?? null;
      const previousEvent = previousTask ? toTaskCalendarEvent(previousTask) : null;
      const optimisticTask: GoogleTask = {
        id: cleanTaskId,
        title:
          updates.title !== undefined
            ? updates.title ?? ""
            : previousTask?.title ?? "",
        notes:
          updates.notes !== undefined
            ? updates.notes ?? null
            : previousTask?.notes ?? null,
        due:
          updates.due !== undefined
            ? normalizeTaskDue(updates.due)
            : normalizeTaskDue(previousTask?.due),
        status:
          updates.status === "completed" || updates.status === "needsAction"
            ? updates.status
            : previousTask?.status ?? "needsAction",
        completed: previousTask?.completed ?? null,
        updated: previousTask?.updated ?? null,
      };
      const optimisticEvent = toTaskCalendarEvent(optimisticTask);
      removeEventsMatching((item) => {
        if (item.source !== "google_task") return false;
        const itemTaskId = String(item.task_id || item.google_event_id || "").trim();
        return itemTaskId === cleanTaskId;
      });
      const optimisticYear = toYearFromIso(optimisticEvent.start) ?? activeYear;
      upsertEvent(buildGoogleYearKey(optimisticYear), optimisticEvent);
      try {
        const updated = await updateGoogleTask(cleanTaskId, {
          title: updates.title,
          notes: updates.notes,
          due: updates.due === undefined ? undefined : normalizeTaskDue(updates.due),
          status: updates.status,
        });
        if (typeof updated.new_revision === "number") {
          updateRevisionState({
            revision: updated.new_revision,
            tasks_revision: updated.new_revision,
          });
        }
        removeEventsMatching((item) => {
          if (item.source !== "google_task") return false;
          const itemTaskId = String(item.task_id || item.google_event_id || "").trim();
          return itemTaskId === cleanTaskId;
        });
        const updatedEvent = toTaskCalendarEvent(updated);
        const updatedYear = toYearFromIso(updatedEvent.start) ?? activeYear;
        upsertEvent(buildGoogleYearKey(updatedYear), updatedEvent);
        return updated;
      } catch (err) {
        removeEventsMatching((item) => {
          if (item.source !== "google_task") return false;
          const itemTaskId = String(item.task_id || item.google_event_id || "").trim();
          return itemTaskId === cleanTaskId;
        });
        if (previousEvent) {
          const previousYear = toYearFromIso(previousEvent.start) ?? activeYear;
          upsertEvent(buildGoogleYearKey(previousYear), previousEvent);
        }
        setError(toErrorMessage(err, "Failed to update task."));
        return null;
      } finally {
        setLoading(false);
      }
    },
    [activeYear, removeEventsMatching, tasks, updateRevisionState, upsertEvent]
  );

  const handleToggleTaskStatus = useCallback(
    async (task: GoogleTask) => {
      const nextStatus = task.status === "completed" ? "needsAction" : "completed";
      return handleUpdateTask(task.id, { status: nextStatus });
    },
    [handleUpdateTask]
  );

  const handleDeleteTask = useCallback(
    async (taskId: string) => {
      setLoading(true);
      setError(null);
      const cleanTaskId = String(taskId || "").trim();
      if (!cleanTaskId) {
        setLoading(false);
        return false;
      }
      const previousTask = tasks.find((task) => task.id === cleanTaskId) ?? null;
      const previousEvent = previousTask ? toTaskCalendarEvent(previousTask) : null;
      removeEventsMatching((item) => {
        if (item.source !== "google_task") return false;
        const itemTaskId = String(item.task_id || item.google_event_id || "").trim();
        return itemTaskId === cleanTaskId;
      });
      try {
        const result = await deleteGoogleTask(cleanTaskId);
        if (typeof result.new_revision === "number") {
          updateRevisionState({
            revision: result.new_revision,
            tasks_revision: result.new_revision,
          });
        }
        return Boolean(result.ok);
      } catch (err) {
        if (previousEvent) {
          const previousYear = toYearFromIso(previousEvent.start) ?? activeYear;
          upsertEvent(buildGoogleYearKey(previousYear), previousEvent);
        }
        setError(toErrorMessage(err, "Failed to delete task."));
        return false;
      } finally {
        setLoading(false);
      }
    },
    [activeYear, removeEventsMatching, tasks, updateRevisionState, upsertEvent]
  );

  const ensureRangeLoaded = useCallback(
    async (searchStart: string, searchEnd: string) => {
      const years = getYearsInRange(searchStart, searchEnd);
      if (!years.length) return buildAllEvents();
      const missing = years.filter((year) => !cacheRef.current[buildGoogleYearKey(year)]);
      if (!missing.length) return buildAllEvents();
      await Promise.all(
        missing.map(async (year) => {
          const { start, end } = getYearRange(year);
          const data = await fetchEventsWithTasks(start, end, year);
          updateCache(buildGoogleYearKey(year), buildIndexes(data.items));
          updateRevisionState(data);
        })
      );
      return buildAllEvents();
    },
    [buildAllEvents, fetchEventsWithTasks, updateCache, updateRevisionState]
  );

  const state: CalendarDataState = {
    events,
    allEvents,
    tasks,
    indexes,
    loading,
    error,
    authStatus,
    refreshKey,
    revision,
    eventsRevision,
    tasksRevision,
  };
  const actions: CalendarActions = {
    refresh,
    refreshIfOutdated,
    create: handleCreate,
    createRecurring: handleCreateRecurring,
    update: handleUpdate,
    updateRecurring: handleUpdateRecurring,
    deleteRecurringOccurrence: handleDeleteRecurringOccurrence,
    remove: handleRemove,
    ingest: ingestEvents,
    removeByIds,
    ensureRangeLoaded,
    createTask: handleCreateTask,
    updateTask: handleUpdateTask,
    toggleTaskStatus: handleToggleTaskStatus,
    deleteTask: handleDeleteTask,
  };

  return { state, actions, useGoogle };
};
