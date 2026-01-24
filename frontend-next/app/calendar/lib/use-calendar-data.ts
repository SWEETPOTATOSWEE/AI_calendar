"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AuthStatus, CalendarEvent, EventPayload, RecurringEventPayload } from "./types";
import {
  applyNlpAdd,
  createEvent,
  deleteEvent,
  fetchAuthStatus,
  getGoogleStreamUrl,
  listEvents,
  updateEvent,
  updateRecurringEvent,
  addRecurringException,
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

const normalizeId = (value: string | number) => String(value);

const sortByStart = (events: CalendarEvent[]) =>
  [...events].sort((a, b) => (a.start || "").localeCompare(b.start || ""));

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

const getEventDateRange = (event: CalendarEvent) => {
  const start = parseISODateTime(event.start);
  if (!start) return null;
  const end = parseISODateTime(event.end || "") || start;
  const startDate = new Date(start.getFullYear(), start.getMonth(), start.getDate());
  const endDate = new Date(end.getFullYear(), end.getMonth(), end.getDate());
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
    const { [hour]: _, ...rest } = day;
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

const buildGoogleEventId = (event: CalendarEvent) => {
  const rawId = event.google_event_id ?? event.id;
  if (!rawId) return "";
  if (!event.calendar_id) return String(rawId);
  return `${event.calendar_id}::${rawId}`;
};

const buildTempId = () => `tmp-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

const eventOverlapsRange = (event: CalendarEvent, rangeStart: string, rangeEnd: string) => {
  const startDate = toDateOnly(event.start);
  if (!startDate) return false;
  const endDate = toDateOnly(event.end ?? event.start) || startDate;
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
  indexes: CalendarIndexes;
  loading: boolean;
  error: string | null;
  authStatus: AuthStatus | null;
};

export type CalendarActions = {
  refresh: (force?: boolean) => Promise<void>;
  create: (payload: EventPayload) => Promise<CalendarEvent | null>;
  createRecurring: (payload: RecurringEventPayload) => Promise<CalendarEvent[] | null>;
  update: (event: CalendarEvent, payload: EventPayload) => Promise<void>;
  updateRecurring: (event: CalendarEvent, payload: RecurringEventPayload) => Promise<void>;
  deleteRecurringOccurrence: (event: CalendarEvent) => Promise<void>;
  remove: (event: CalendarEvent) => Promise<void>;
  ingest: (events: CalendarEvent[]) => void;
  removeByIds: (ids: Array<string | number>) => void;
  ensureRangeLoaded: (rangeStart: string, rangeEnd: string) => Promise<CalendarEvent[]>;
};

export const useCalendarData = (rangeStart: string, rangeEnd: string, viewAnchor?: Date) => {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [allEvents, setAllEvents] = useState<CalendarEvent[]>([]);
  const [indexes, setIndexes] = useState<CalendarIndexes>(emptyIndexes());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const cacheRef = useRef<Record<string, CachedYear>>({});
  const sseRef = useRef<EventSource | null>(null);
  const refreshTimerRef = useRef<number | null>(null);
  const refreshRangeRef = useRef<() => void>(() => {});

  const useGoogle = useMemo(
    () => Boolean(authStatus?.enabled && authStatus?.has_token && !authStatus?.admin),
    [authStatus]
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

  const activeKey = useMemo(
    () => `${useGoogle ? "google" : "local"}:${activeYear}`,
    [useGoogle, activeYear]
  );
  const activePrefix = useMemo(() => `${useGoogle ? "google" : "local"}:`, [useGoogle]);

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
      setAllEvents(buildAllEvents());
    },
    [activeKey, buildAllEvents]
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

  const refresh = useCallback(async (force = false) => {
    if (!rangeStart || !rangeEnd || authStatus === null) return;
    setLoading(true);
    setError(null);
    try {
      const cached = cacheRef.current[activeKey];
      if (cached && !force) {
        setEvents(cached.events);
        setIndexes(cached.indexes);
        setAllEvents(buildAllEvents());
        const adjacentYear = getPrefetchYear(activeYear, activeMonth);
        if (adjacentYear) {
          const adjacentKey = `${useGoogle ? "google" : "local"}:${adjacentYear}`;
          if (!cacheRef.current[adjacentKey]) {
            const { start: adjStart, end: adjEnd } = getYearRange(adjacentYear);
            listEvents(adjStart, adjEnd, useGoogle)
              .then((prefetch) => {
                updateCache(adjacentKey, buildIndexes(prefetch));
              })
              .catch(() => {});
          }
        }
        return;
      }
      const { start, end } = getYearRange(activeYear);
      const data = await listEvents(start, end, useGoogle);
      cacheRef.current[activeKey] = buildIndexes(data);
      setEvents(cacheRef.current[activeKey].events);
      setIndexes(cacheRef.current[activeKey].indexes);
      setAllEvents(buildAllEvents());
      const adjacentYear = getPrefetchYear(activeYear, activeMonth);
      if (adjacentYear) {
        const adjacentKey = `${useGoogle ? "google" : "local"}:${adjacentYear}`;
        if (!cacheRef.current[adjacentKey]) {
          const { start: adjStart, end: adjEnd } = getYearRange(adjacentYear);
          listEvents(adjStart, adjEnd, useGoogle)
            .then((prefetch) => {
              updateCache(adjacentKey, buildIndexes(prefetch));
            })
            .catch(() => {});
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "이벤트를 불러오지 못했습니다.";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [rangeStart, rangeEnd, activeKey, activeYear, activeMonth, useGoogle, updateCache, authStatus]);

  const refreshRange = useCallback(async () => {
    if (!rangeStart || !rangeEnd) return;
    setLoading(true);
    setError(null);
    try {
      const data = await listEvents(rangeStart, rangeEnd, useGoogle);
      const yearsInRange = new Set<number>();
      getYearsInRange(rangeStart, rangeEnd).forEach((year) => yearsInRange.add(year));
      data.forEach((event) => {
        const year = toYearFromIso(event.start);
        if (year) yearsInRange.add(year);
      });

      const prefix = useGoogle ? "google" : "local";
      const missingYears: number[] = [];
      yearsInRange.forEach((year) => {
        const key = `${prefix}:${year}`;
        const current = cacheRef.current[key];
        if (!current) {
          missingYears.push(year);
          return;
        }
        const remaining = current.events.filter(
          (event) => !eventOverlapsRange(event, rangeStart, rangeEnd)
        );
        const additions = data.filter((event) => toYearFromIso(event.start) === year);
        updateCache(key, buildIndexes([...remaining, ...additions]));
      });

      if (missingYears.length) {
        await Promise.all(
          missingYears.map(async (year) => {
            const { start, end } = getYearRange(year);
            const fullData = await listEvents(start, end, useGoogle);
            updateCache(`${prefix}:${year}`, buildIndexes(fullData));
          })
        );
      }

      const activeCache = cacheRef.current[activeKey];
      setEvents(activeCache?.events || []);
      setIndexes(activeCache?.indexes || emptyIndexes());
      setAllEvents(buildAllEvents());
    } catch (err) {
      const message = err instanceof Error ? err.message : "이벤트를 불러오지 못했습니다.";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [rangeStart, rangeEnd, useGoogle, updateCache, activeKey, buildAllEvents]);

  useEffect(() => {
    refreshRangeRef.current = refreshRange;
  }, [refreshRange]);

  useEffect(() => {
    let mounted = true;
    fetchAuthStatus()
      .then((status) => {
        if (mounted) setAuthStatus(status);
      })
      .catch(() => {
        if (mounted) setAuthStatus({ enabled: false, configured: false, has_token: false });
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
    }, 600);
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
    const onSync = () => scheduleSseRefresh();
    source.addEventListener("google_sync", onSync);
    source.onmessage = onSync;

    return () => {
      source.removeEventListener("google_sync", onSync);
      source.onmessage = null;
      source.close();
      if (sseRef.current === source) sseRef.current = null;
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current);
        refreshTimerRef.current = null;
      }
    };
  }, [scheduleSseRefresh, useGoogle]);

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
        source: useGoogle ? "google" : "local",
        google_event_id: null,
        calendar_id: null,
      };
      const optimisticYear = toYearFromIso(optimistic.start) ?? activeYear;
      const optimisticKey = `${optimistic.source}:${optimisticYear}`;
      upsertEvent(optimisticKey, optimistic);
      try {
        const created = await createEvent(payload);
        removeEvent(optimisticKey, optimistic);
        const localYear = toYearFromIso(created.start) ?? activeYear;
        const localKey = `local:${localYear}`;
        upsertEvent(localKey, { ...created, source: "local" }, optimistic.source === "local" ? optimistic : undefined);

        if (useGoogle && created.google_event_id) {
          const googleKey = `google:${localYear}`;
          const googleId = created.google_event_id;
          upsertEvent(googleKey, {
            ...created,
            id: created.calendar_id ? `${created.calendar_id}::${googleId}` : googleId,
            google_event_id: googleId,
            source: "google",
          }, optimistic.source === "google" ? optimistic : undefined);
        }
        return created;
      } catch (err) {
        const message = err instanceof Error ? err.message : "일정 생성에 실패했습니다.";
        removeEvent(optimisticKey, optimistic);
        setError(message);
        return null;
      } finally {
        setLoading(false);
      }
    },
    [activeYear, removeEvent, upsertEvent, useGoogle]
  );

  const handleUpdate = useCallback(
    async (event: CalendarEvent, payload: EventPayload) => {
      setLoading(true);
      setError(null);
      const optimistic = applyPayloadToEvent(event, payload);
      const previousYear = toYearFromIso(event.start) ?? activeYear;
      const updatedYear = toYearFromIso(optimistic.start) ?? activeYear;
      if (updatedYear !== previousYear) {
        const previousKey = `${event.source}:${previousYear}`;
        removeEvent(previousKey, event);
      }
      const optimisticKey = `${event.source}:${updatedYear}`;
      upsertEvent(optimisticKey, optimistic, updatedYear === previousYear ? event : undefined);
      try {
        await updateEvent(event, payload);
        const updated = applyPayloadToEvent(event, payload);
        removeEvent(optimisticKey, optimistic);
        if (updatedYear !== previousYear) {
          const previousKey = `${event.source}:${previousYear}`;
          removeEvent(previousKey, event);
        }
        const targetKey = `${event.source}:${updatedYear}`;
        upsertEvent(targetKey, updated, updatedYear === previousYear ? optimistic : undefined);
      } catch (err) {
        const message = err instanceof Error ? err.message : "일정 수정에 실패했습니다.";
        if (updatedYear !== previousYear) {
          const optimisticKey = `${event.source}:${updatedYear}`;
          removeEvent(optimisticKey, optimistic);
          const previousKey = `${event.source}:${previousYear}`;
          upsertEvent(previousKey, event);
        } else {
          const targetKey = `${event.source}:${previousYear}`;
          upsertEvent(targetKey, event, optimistic);
        }
        setError(message);
      } finally {
        setLoading(false);
      }
    },
    [activeYear, removeEvent, upsertEvent]
  );

  const handleUpdateRecurring = useCallback(
    async (event: CalendarEvent, payload: RecurringEventPayload) => {
      setLoading(true);
      setError(null);
      try {
        await updateRecurringEvent(event, payload);
        cacheRef.current = {};
        await refresh(true);
      } catch (err) {
        const message = err instanceof Error ? err.message : "반복 일정 수정에 실패했습니다.";
        setError(message);
      } finally {
        setLoading(false);
      }
    },
    [refresh]
  );

  const handleDeleteRecurringOccurrence = useCallback(
    async (event: CalendarEvent) => {
      setLoading(true);
      setError(null);
      try {
        if (useGoogle) {
          throw new Error("Google 모드에서는 반복 일정을 수정할 수 없습니다.");
        }
        const startDate = event.start?.split("T")[0];
        if (!startDate) {
          throw new Error("일정 시작 날짜를 찾을 수 없습니다.");
        }
        await addRecurringException(event, startDate);
        cacheRef.current = {};
        await refresh(true);
      } catch (err) {
        const message = err instanceof Error ? err.message : "반복 일정 삭제에 실패했습니다.";
        setError(message);
      } finally {
        setLoading(false);
      }
    },
    [refresh, useGoogle]
  );

  const handleCreateRecurring = useCallback(
    async (payload: RecurringEventPayload) => {
      setLoading(true);
      setError(null);
      try {
        const created = await applyNlpAdd([payload as unknown as Record<string, unknown>]);
        const years = new Set<number>();
        created.forEach((item) => {
          const year = toYearFromIso(item.start) ?? activeYear;
          years.add(year);
        });
        for (const year of years) {
          const { start, end } = getYearRange(year);
          const data = await listEvents(start, end, useGoogle);
          const key = `${useGoogle ? "google" : "local"}:${year}`;
          updateCache(key, buildIndexes(data));
        }
        return created;
      } catch (err) {
        const message = err instanceof Error ? err.message : "반복 일정 생성에 실패했습니다.";
        setError(message);
        return null;
      } finally {
        setLoading(false);
      }
    },
    [activeYear, updateCache, useGoogle]
  );

  const handleRemove = useCallback(
    async (event: CalendarEvent) => {
      setLoading(true);
      setError(null);
      const year = toYearFromIso(event.start) ?? activeYear;
      const key = `${event.source}:${year}`;
      removeEvent(key, event);
      try {
        await deleteEvent(event);
        const shouldRefresh = useGoogle || event.recur === "recurring" || Boolean(event.recurrence);
        if (shouldRefresh) {
          cacheRef.current = {};
          await refresh(true);
          return;
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "일정 삭제에 실패했습니다.";
        upsertEvent(key, event);
        setError(message);
      } finally {
        setLoading(false);
      }
    },
    [activeYear, refresh, removeEvent, upsertEvent, useGoogle]
  );

  const ingestEvents = useCallback(
    (items: CalendarEvent[]) => {
      if (useGoogle) {
        refresh(true);
        return;
      }
      const hasRecurring = items.some(
        (event) => event.recur === "recurring" || Boolean(event.recurrence)
      );
      if (hasRecurring) {
        refresh(true);
        return;
      }
      items.forEach((event) => {
        const year = toYearFromIso(event.start) ?? activeYear;
        const localKey = `local:${year}`;
        const localEvent = event.source ? event : { ...event, source: "local" as const };
        upsertEvent(localKey, localEvent);
        if (useGoogle && event.google_event_id) {
          const googleKey = `google:${year}`;
          upsertEvent(googleKey, {
            ...event,
            id: buildGoogleEventId(event),
            google_event_id: event.google_event_id,
            source: "google",
          });
        }
      });
    },
    [activeYear, refresh, upsertEvent, useGoogle]
  );

  const removeByIds = useCallback(
    (ids: Array<string | number>) => {
      if (ids.length === 0) return;
      const idSet = new Set(ids.map((val) => normalizeId(val)));
      Object.entries(cacheRef.current).forEach(([key, cache]) => {
        if (!key.startsWith("local:")) return;
        cache.events.forEach((event) => {
          if (idSet.has(normalizeId(event.id))) {
            removeEvent(key, event);
          }
        });
      });
    },
    [removeEvent]
  );

  const ensureRangeLoaded = useCallback(
    async (searchStart: string, searchEnd: string) => {
      const years = getYearsInRange(searchStart, searchEnd);
      if (!years.length) return buildAllEvents();
      const prefix = useGoogle ? "google" : "local";
      const missing = years.filter((year) => !cacheRef.current[`${prefix}:${year}`]);
      if (!missing.length) return buildAllEvents();
      await Promise.all(
        missing.map(async (year) => {
          const key = `${prefix}:${year}`;
          const { start, end } = getYearRange(year);
          const data = await listEvents(start, end, useGoogle);
          updateCache(key, buildIndexes(data));
        })
      );
      return buildAllEvents();
    },
    [buildAllEvents, updateCache, useGoogle]
  );

  const state: CalendarDataState = { events, allEvents, indexes, loading, error, authStatus };
  const actions: CalendarActions = {
    refresh,
    create: handleCreate,
    createRecurring: handleCreateRecurring,
    update: handleUpdate,
    updateRecurring: handleUpdateRecurring,
    deleteRecurringOccurrence: handleDeleteRecurringOccurrence,
    remove: handleRemove,
    ingest: ingestEvents,
    removeByIds,
    ensureRangeLoaded,
  };

  return { state, actions, useGoogle };
};
