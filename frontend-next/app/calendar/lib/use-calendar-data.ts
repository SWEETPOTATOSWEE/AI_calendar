"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AuthStatus, CalendarEvent, EventPayload, RecurringEventPayload } from "./types";
import { applyNlpAdd, createEvent, deleteEvent, fetchAuthStatus, listEvents, updateEvent } from "./api";
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
  remove: (event: CalendarEvent) => Promise<void>;
  ingest: (events: CalendarEvent[]) => void;
  removeByIds: (ids: Array<string | number>) => void;
  ensureRangeLoaded: (rangeStart: string, rangeEnd: string) => Promise<CalendarEvent[]>;
};

export const useCalendarData = (rangeStart: string, rangeEnd: string) => {
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [allEvents, setAllEvents] = useState<CalendarEvent[]>([]);
  const [indexes, setIndexes] = useState<CalendarIndexes>(emptyIndexes());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const cacheRef = useRef<Record<string, CachedYear>>({});

  const useGoogle = useMemo(
    () => Boolean(authStatus?.enabled && authStatus?.has_token),
    [authStatus]
  );

  const activeYear = useMemo(() => {
    const startDate = toDateOnly(rangeStart);
    const endDate = toDateOnly(rangeEnd);
    if (startDate && endDate) {
      const mid = new Date((startDate.getTime() + endDate.getTime()) / 2);
      return mid.getFullYear();
    }
    if (startDate) return startDate.getFullYear();
    if (endDate) return endDate.getFullYear();
    return new Date().getFullYear();
  }, [rangeStart, rangeEnd]);
  const activeMonth = useMemo(() => {
    const startDate = toDateOnly(rangeStart);
    const endDate = toDateOnly(rangeEnd);
    if (startDate && endDate) {
      const mid = new Date((startDate.getTime() + endDate.getTime()) / 2);
      return mid.getMonth() + 1;
    }
    if (startDate) return startDate.getMonth() + 1;
    if (endDate) return endDate.getMonth() + 1;
    return new Date().getMonth() + 1;
  }, [rangeStart, rangeEnd]);

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
    if (!rangeStart || !rangeEnd) return;
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
                cacheRef.current[adjacentKey] = buildIndexes(prefetch);
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
              cacheRef.current[adjacentKey] = buildIndexes(prefetch);
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
  }, [rangeStart, rangeEnd, activeKey, activeYear, activeMonth, useGoogle]);

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

  const handleCreate = useCallback(
    async (payload: EventPayload) => {
      setLoading(true);
      setError(null);
      try {
        const created = await createEvent(payload);
        const localYear = toYearFromIso(created.start) ?? activeYear;
        const localKey = `local:${localYear}`;
        upsertEvent(localKey, { ...created, source: "local" });

        if (useGoogle && created.google_event_id) {
          const googleKey = `google:${localYear}`;
          upsertEvent(googleKey, {
            ...created,
            id: created.google_event_id,
            google_event_id: created.google_event_id,
            source: "google",
          });
        }
        return created;
      } catch (err) {
        const message = err instanceof Error ? err.message : "일정 생성에 실패했습니다.";
        setError(message);
        return null;
      } finally {
        setLoading(false);
      }
    },
    [activeYear, upsertEvent, useGoogle]
  );

  const handleUpdate = useCallback(
    async (event: CalendarEvent, payload: EventPayload) => {
      setLoading(true);
      setError(null);
      try {
        await updateEvent(event, payload);
        const updated = applyPayloadToEvent(event, payload);
        const updatedYear = toYearFromIso(updated.start) ?? activeYear;
        const previousYear = toYearFromIso(event.start) ?? activeYear;
        if (updatedYear !== previousYear) {
          const previousKey = `${event.source}:${previousYear}`;
          removeEvent(previousKey, event);
        }
        const targetKey = `${event.source}:${updatedYear}`;
        upsertEvent(targetKey, updated, updatedYear === previousYear ? event : undefined);
      } catch (err) {
        const message = err instanceof Error ? err.message : "일정 수정에 실패했습니다.";
        setError(message);
      } finally {
        setLoading(false);
      }
    },
    [activeYear, removeEvent, upsertEvent]
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
      try {
        await deleteEvent(event);
        const year = toYearFromIso(event.start) ?? activeYear;
        const key = `${event.source}:${year}`;
        removeEvent(key, event);
      } catch (err) {
        const message = err instanceof Error ? err.message : "일정 삭제에 실패했습니다.";
        setError(message);
      } finally {
        setLoading(false);
      }
    },
    [activeYear, removeEvent]
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
            id: event.google_event_id,
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
    remove: handleRemove,
    ingest: ingestEvents,
    removeByIds,
    ensureRangeLoaded,
  };

  return { state, actions, useGoogle };
};
