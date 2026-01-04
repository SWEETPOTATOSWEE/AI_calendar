"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";
import "./fullcalendar.css";
import EventModal from "./components/EventModal";
import AiAssistantModal from "./components/AiAssistantModal";
import CalendarHeaderActions from "./components/CalendarHeaderActions";
import {
  addDays,
  formatLongDate,
  formatMonthYear,
  formatTime,
  formatTimeRange,
  endOfMonth,
  isSameDay,
  parseISODateTime,
  startOfMonth,
  startOfWeek,
  toISODate,
  toISODateTime,
} from "./lib/date";
import type { CalendarEvent, EventRecurrence, RecurringEventPayload } from "./lib/types";
import { useCalendarData } from "./lib/use-calendar-data";
import { useAiAssistant, type AddPreviewItem } from "./lib/use-ai-assistant";
import { useUndoStack } from "./lib/use-undo";
import {
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  Calendar,
  Clock,
  FileText,
  MapPin,
  MoreVertical,
  Plus,
  Search,
  SlidersHorizontal,
  Sparkles,
  Undo2,
  Users,
  X,
} from "lucide-react";

type ViewMode = "month" | "week" | "day";

const VIEW_LABELS: Record<ViewMode, string> = {
  month: "월",
  week: "주",
  day: "일",
};



const MONTH_EVENT_COLOR_MAP: Record<string, { bg: string; border: string; text: string }> = {
  "1": { bg: "#dbeafe", border: "#2563eb", text: "#1e3a8a" },
  "2": { bg: "#d1fae5", border: "#059669", text: "#065f46" },
  "3": { bg: "#ede9fe", border: "#7c3aed", text: "#4c1d95" },
  "4": { bg: "#ffe4e6", border: "#e11d48", text: "#9f1239" },
  "5": { bg: "#ffedd5", border: "#ea580c", text: "#9a3412" },
  "6": { bg: "#ccfbf1", border: "#0d9488", text: "#115e59" },
  "7": { bg: "#e0e7ff", border: "#4f46e5", text: "#3730a3" },
  "8": { bg: "#fef3c7", border: "#d97706", text: "#92400e" },
  "9": { bg: "#fce7f3", border: "#db2777", text: "#9d174d" },
  "10": { bg: "#f1f5f9", border: "#64748b", text: "#334155" },
  "11": { bg: "#cffafe", border: "#0891b2", text: "#0e7490" },
};

const DEFAULT_MONTH_EVENT_COLOR = { bg: "#dbeafe", border: "#64748b", text: "#2f5bd6" };
const getMonthEventColor = (event: CalendarEvent) => {
  const colorKey = event.color_id ? String(event.color_id) : "";
  if (colorKey && colorKey !== "default") {
    return MONTH_EVENT_COLOR_MAP[colorKey] || DEFAULT_MONTH_EVENT_COLOR;
  }
  return DEFAULT_MONTH_EVENT_COLOR;
};

const parseHexColor = (color: string) => {
  if (!color.startsWith("#")) return null;
  let hex = color.slice(1);
  if (hex.length === 3) {
    hex = hex
      .split("")
      .map((part) => part + part)
      .join("");
  }
  if (hex.length !== 6) return null;
  const r = parseInt(hex.slice(0, 2), 16);
  const g = parseInt(hex.slice(2, 4), 16);
  const b = parseInt(hex.slice(4, 6), 16);
  return { r, g, b };
};

const mixHexColors = (primary: string, secondary: string, weight = 0.6) => {
  const p = parseHexColor(primary);
  const s = parseHexColor(secondary);
  if (!p || !s) return primary;
  const ratio = Math.min(1, Math.max(0, weight));
  const r = Math.round(p.r * ratio + s.r * (1 - ratio));
  const g = Math.round(p.g * ratio + s.g * (1 - ratio));
  const b = Math.round(p.b * ratio + s.b * (1 - ratio));
  return `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b
    .toString(16)
    .padStart(2, "0")}`;
};

const toRgba = (color: string, alpha: number) => {
  if (!color) return `rgba(0,0,0,${alpha})`;
  const rgb = parseHexColor(color);
  if (rgb) return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${alpha})`;
  return color;
};

const toDateOnly = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate());
const getWeekKey = (date: Date) => toISODate(startOfWeek(date));

const formatTimeGridSlotLabel = (date: Date) => {
  const parts = new Intl.DateTimeFormat("ko-KR", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  }).formatToParts(date);
  const period = parts.find((part) => part.type === "dayPeriod")?.value ?? "";
  const hour = parts.find((part) => part.type === "hour")?.value ?? "";
  const minute = parts.find((part) => part.type === "minute")?.value;
  const time = minute && minute !== "00" ? `${hour}:${minute}` : `${hour}시`;
  return { period, time };
};


const getWeekNumber = (date: Date) => {
  const temp = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const dayNum = temp.getUTCDay() || 7;
  temp.setUTCDate(temp.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(temp.getUTCFullYear(), 0, 1));
  return Math.ceil((((temp.getTime() - yearStart.getTime()) / 86400000) + 1) / 7);
};

const getWeekOfMonth = (monthDate: Date, weekStart: Date) => {
  const monthStart = startOfMonth(monthDate);
  const monthWeekStart = startOfWeek(monthStart);
  const diffDays = Math.floor((weekStart.getTime() - monthWeekStart.getTime()) / 86400000);
  return Math.max(1, Math.floor(diffDays / 7) + 1);
};

const addMinutesToIso = (value: string, minutes: number) => {
  const base = parseISODateTime(value);
  if (!base) return value;
  const next = new Date(base);
  next.setMinutes(next.getMinutes() + minutes);
  return toISODateTime(next);
};

const buildAiRecurrence = (item: AddPreviewItem): EventRecurrence | null => {
  if (item.recurrence) {
    if (item.recurrence.end || (!item.end_date && !item.count)) return item.recurrence;
    return {
      ...item.recurrence,
      end: {
        until: item.end_date ?? null,
        count: item.count ?? null,
      },
    };
  }
  if (Array.isArray(item.weekdays) && item.weekdays.length > 0) {
    const end = item.end_date || item.count ? { until: item.end_date ?? null, count: item.count ?? null } : null;
    return {
      freq: "WEEKLY",
      interval: 1,
      byweekday: item.weekdays,
      bymonthday: null,
      bysetpos: null,
      bymonth: null,
      end,
    };
  }
  return null;
};

const resolveAiStartEnd = (item: AddPreviewItem, fallbackDate: Date) => {
  if (item.start) {
    const start = item.start;
    let end = item.end ?? null;
    if (!end) {
      if (item.all_day) {
        const datePart = start.split("T")[0] || toISODate(fallbackDate);
        end = `${datePart}T23:59`;
      } else {
        const duration = typeof item.duration_minutes === "number" && item.duration_minutes > 0
          ? item.duration_minutes
          : 60;
        end = addMinutesToIso(start, duration);
      }
    }
    return { start, end };
  }

  const occurrence = item.occurrences?.find((value) => typeof value.start === "string");
  if (occurrence?.start) {
    const end = occurrence.end ?? null;
    return { start: occurrence.start, end };
  }

  const datePart = item.start_date || toISODate(fallbackDate);
  const isAllDay = Boolean(item.all_day);
  const timePart = isAllDay ? "00:00" : item.time || "09:00";
  const start = `${datePart}T${timePart}`;
  let end: string | null = null;
  if (isAllDay) {
    end = `${datePart}T23:59`;
  } else {
    const duration = typeof item.duration_minutes === "number" && item.duration_minutes > 0
      ? item.duration_minutes
      : 60;
    end = addMinutesToIso(start, duration);
  }
  return { start, end };
};

const buildAiDraftEvent = (item: AddPreviewItem, fallbackDate: Date): CalendarEvent => {
  const { start, end } = resolveAiStartEnd(item, fallbackDate);
  const recurrence = item.type === "recurring" ? buildAiRecurrence(item) : null;
  return {
    id: `ai-draft-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    title: item.title?.trim() || "제목 없음",
    start,
    end,
    location: item.location ?? null,
    description: item.description ?? null,
    attendees: item.attendees ?? null,
    reminders: item.reminders ?? null,
    visibility: item.visibility ?? null,
    transparency: item.transparency ?? null,
    meeting_url: item.meeting_url ?? null,
    timezone: item.timezone ?? null,
    color_id: item.color_id ?? null,
    recurrence,
    all_day: Boolean(item.all_day),
    source: "local",
  };
};

const buildDefaultSearchRange = (baseDate: Date) => {
  const start = new Date(baseDate.getFullYear() - 1, baseDate.getMonth(), baseDate.getDate());
  const end = new Date(baseDate.getFullYear() + 1, baseDate.getMonth(), baseDate.getDate());
  return { start: toISODate(start), end: toISODate(end) };
};

let cachedSearchRange: { start: string; end: string } | null = null;
let cachedAppliedSearchRange: { start: string; end: string } | null = null;

const getInitialSearchRange = (baseDate: Date) =>
  cachedSearchRange ?? buildDefaultSearchRange(baseDate);
const getInitialAppliedSearchRange = (baseDate: Date) =>
  cachedAppliedSearchRange ?? buildDefaultSearchRange(baseDate);

function CalendarPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const today = new Date();
  const [currentMonth, setCurrentMonth] = useState(startOfMonth(today));
  const [selectedDate, setSelectedDate] = useState(today);
  const [activeEvent, setActiveEvent] = useState<CalendarEvent | null>(null);
  const [aiDraftEvent, setAiDraftEvent] = useState<CalendarEvent | null>(null);
  const [aiDraftIndex, setAiDraftIndex] = useState<number | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [nowSnapshot] = useState(() => new Date());
  const [searchIndex, setSearchIndex] = useState<number | null>(null);
  const [searchAdvancedOpen, setSearchAdvancedOpen] = useState(false);
  const [searchLocationQuery, setSearchLocationQuery] = useState("");
  const [searchDescriptionQuery, setSearchDescriptionQuery] = useState("");
  const [searchAttendeesQuery, setSearchAttendeesQuery] = useState("");
  const [searchRange, setSearchRange] = useState(() => getInitialSearchRange(nowSnapshot));
  const [searchMatchMode, setSearchMatchMode] = useState<"and" | "or">("and");
  const [searchAppliedQuery, setSearchAppliedQuery] = useState("");
  const [searchAppliedLocation, setSearchAppliedLocation] = useState("");
  const [searchAppliedDescription, setSearchAppliedDescription] = useState("");
  const [searchAppliedAttendees, setSearchAppliedAttendees] = useState("");
  const [searchAppliedRange, setSearchAppliedRange] = useState(() => getInitialAppliedSearchRange(nowSnapshot));
  const [searchAppliedMatchMode, setSearchAppliedMatchMode] = useState<"and" | "or">("and");
  const [searchAllEnabled, setSearchAllEnabled] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [sidebarScrollable, setSidebarScrollable] = useState(false);
  const [mobileScrollable, setMobileScrollable] = useState(false);
  const [sidebarAtTop, setSidebarAtTop] = useState(true);
  const [sidebarAtBottom, setSidebarAtBottom] = useState(true);
  const [mobileAtTop, setMobileAtTop] = useState(true);
  const [mobileAtBottom, setMobileAtBottom] = useState(true);
  const sidebarScrollRef = useRef<HTMLDivElement | null>(null);
  const mobileScrollRef = useRef<HTMLDivElement | null>(null);
  const monthCalendarRef = useRef<FullCalendar | null>(null);
  const miniCalendarRef = useRef<FullCalendar | null>(null);
  const mobileMenuRef = useRef<HTMLDivElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);

  const viewParam = searchParams.get("view");
  const view: ViewMode = viewParam === "week" || viewParam === "day" ? viewParam : "month";
  const viewKeys = useMemo(() => Object.keys(VIEW_LABELS) as ViewMode[], []);
  const activeViewIndex = useMemo(() => viewKeys.indexOf(view), [viewKeys, view]);
  const viewToggleStyle = useMemo(
    () =>
      ({
        "--seg-count": String(viewKeys.length),
        "--seg-index": String(activeViewIndex),
      }) as CSSProperties,
    [activeViewIndex, viewKeys.length]
  );
  const isTimeGridView = view === "week" || view === "day";
  const selectedWeekStartDay = useMemo(() => toDateOnly(startOfWeek(selectedDate)), [selectedDate]);
  const selectedWeekEndDay = useMemo(() => addDays(selectedWeekStartDay, 6), [selectedWeekStartDay]);
  const toggleMiniWeekHover = (weekKey: string, active: boolean) => {
    const api = miniCalendarRef.current?.getApi();
    const root = (api as unknown as { el?: HTMLElement } | undefined)?.el;
    if (!root) return;
    root.querySelectorAll(`[data-week-key="${weekKey}"]`).forEach((node) => {
      node.classList.toggle("mini-week-hover", active);
    });
  };

  const weekStart = startOfWeek(selectedDate);
  const monthStart = startOfMonth(currentMonth);
  const gridStart = startOfWeek(monthStart);
  const gridEnd = addDays(gridStart, 41);

  const [rangeStart, rangeEnd] = useMemo(() => {
    if (view === "day") {
      const dayKey = toISODate(selectedDate);
      return [dayKey, dayKey];
    }
    if (view === "week") {
      return [toISODate(weekStart), toISODate(addDays(weekStart, 6))];
    }
    return [toISODate(gridStart), toISODate(gridEnd)];
  }, [view, weekStart, selectedDate, gridStart, gridEnd]);

  const { state, actions, useGoogle } = useCalendarData(rangeStart, rangeEnd);
  const undo = useUndoStack(() => actions.refresh(true));
  const undoCount = undo.stack.length;
  const hasDraftFilters = useMemo(() => {
    return (
      searchQuery.trim().length > 0 ||
      searchLocationQuery.trim().length > 0 ||
      searchDescriptionQuery.trim().length > 0 ||
      searchAttendeesQuery.trim().length > 0 ||
      searchRange.start !== searchAppliedRange.start ||
      searchRange.end !== searchAppliedRange.end ||
      searchMatchMode !== searchAppliedMatchMode
    );
  }, [
    searchQuery,
    searchLocationQuery,
    searchDescriptionQuery,
    searchAttendeesQuery,
    searchRange.start,
    searchRange.end,
    searchAppliedRange.start,
    searchAppliedRange.end,
    searchMatchMode,
    searchAppliedMatchMode,
  ]);
  const hasSearchInput = useMemo(() => {
    return (
      searchQuery.trim().length > 0 ||
      searchLocationQuery.trim().length > 0 ||
      searchDescriptionQuery.trim().length > 0 ||
      searchAttendeesQuery.trim().length > 0
    );
  }, [searchQuery, searchLocationQuery, searchDescriptionQuery, searchAttendeesQuery]);
  const buildSearchMatches = useCallback(
    ({
      titleQuery,
      locationQuery,
      descriptionQuery,
      attendeesQuery,
      range,
      includeAll,
      matchMode,
      events,
    }: {
      titleQuery: string;
      locationQuery: string;
      descriptionQuery: string;
      attendeesQuery: string;
      range: { start: string; end: string };
      includeAll: boolean;
      matchMode: "and" | "or";
      events?: CalendarEvent[];
    }) => {
      const title = titleQuery.trim().toLowerCase();
      const location = locationQuery.trim().toLowerCase();
      const description = descriptionQuery.trim().toLowerCase();
      const attendees = attendeesQuery.trim().toLowerCase();
      const hasFilters =
        title.length > 0 ||
        location.length > 0 ||
        description.length > 0 ||
        attendees.length > 0;
      if (!hasFilters && !includeAll) return [];
      const parseDateOnly = (value: string) => {
        const [year, month, day] = value.split("-").map(Number);
        if (!year || !month || !day) return null;
        return new Date(year, month - 1, day);
      };
      const rangeStartDate = parseDateOnly(range.start);
      const rangeEndDate = parseDateOnly(range.end);
      if (!rangeStartDate || !rangeEndDate) return [];
      const sourceEvents = events ?? state.allEvents;
      return sourceEvents
        .map((event) => {
          const date = parseISODateTime(event.start);
          if (!date) return null;
          const dateOnly = new Date(date.getFullYear(), date.getMonth(), date.getDate());
          if (dateOnly < rangeStartDate || dateOnly > rangeEndDate) return null;
          const titleMatch = title ? event.title.toLowerCase().includes(title) : false;
          const locationMatch = location
            ? (event.location || "").toLowerCase().includes(location)
            : false;
          const descriptionMatch = description
            ? (event.description || "").toLowerCase().includes(description)
            : false;
          const attendeesMatch = attendees
            ? (event.attendees || []).join(" ").toLowerCase().includes(attendees)
            : false;
          const matches =
            !hasFilters
              ? true
              : matchMode === "or"
              ? titleMatch || locationMatch || descriptionMatch || attendeesMatch
              : (!title || titleMatch) &&
                (!location || locationMatch) &&
                (!description || descriptionMatch) &&
                (!attendees || attendeesMatch);
          if (!matches) return null;
          return { event, date };
        })
        .filter(
          (value): value is { event: CalendarEvent; date: Date } =>
            value !== null
        )
        .sort((a, b) => a.date.getTime() - b.date.getTime());
    },
    [state.allEvents]
  );
  const searchMatches = useMemo(() => {
    return buildSearchMatches({
      titleQuery: searchAppliedQuery,
      locationQuery: searchAppliedLocation,
      descriptionQuery: searchAppliedDescription,
      attendeesQuery: searchAppliedAttendees,
      range: searchAppliedRange,
      includeAll: searchAllEnabled,
      matchMode: searchAppliedMatchMode,
    });
  }, [
    buildSearchMatches,
    searchAppliedQuery,
    searchAppliedLocation,
    searchAppliedDescription,
    searchAppliedAttendees,
    searchAppliedRange,
    searchAllEnabled,
    searchAppliedMatchMode,
  ]);
  const findSearchIndexInMatches = (
    matches: { event: CalendarEvent; date: Date }[],
    direction: "forward" | "backward",
    baseDate: Date,
    strict: boolean
  ) => {
    const baseKey = toISODate(baseDate);
    if (direction === "forward") {
      for (let i = 0; i < matches.length; i += 1) {
        const matchKey = toISODate(matches[i].date);
        if (strict ? matchKey > baseKey : matchKey >= baseKey) return i;
      }
      return null;
    }
    for (let i = matches.length - 1; i >= 0; i -= 1) {
      const matchKey = toISODate(matches[i].date);
      if (strict ? matchKey < baseKey : matchKey <= baseKey) return i;
    }
    return null;
  };
  const findSearchIndex = (
    direction: "forward" | "backward",
    baseDate: Date,
    strict: boolean
  ) => findSearchIndexInMatches(searchMatches, direction, baseDate, strict);
  const goToSearchDate = (date: Date) => {
    const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    monthCalendarRef.current?.getApi().gotoDate(target);
    setSelectedDate(target);
    setCurrentMonth(startOfMonth(target));
  };
  const resetSearchRange = () => {
    setSearchRange(buildDefaultSearchRange(nowSnapshot));
  };
  const applySearchFilters = async (options?: { includeAll?: boolean }) => {
    const includeAll = Boolean(options?.includeAll);
    const appliedRange = searchRange;
    let ensuredEvents: CalendarEvent[] | null = null;
    setIsSearching(true);
    try {
      ensuredEvents = await actions.ensureRangeLoaded(appliedRange.start, appliedRange.end);
    } catch {
      // ignore fetch errors during search range expansion
    } finally {
      setIsSearching(false);
    }
    const matches = buildSearchMatches({
      titleQuery: searchQuery,
      locationQuery: searchLocationQuery,
      descriptionQuery: searchDescriptionQuery,
      attendeesQuery: searchAttendeesQuery,
      range: appliedRange,
      includeAll,
      matchMode: searchMatchMode,
      events: ensuredEvents ?? state.allEvents,
    });
    setSearchAppliedQuery(searchQuery);
    setSearchAppliedLocation(searchLocationQuery);
    setSearchAppliedDescription(searchDescriptionQuery);
    setSearchAppliedAttendees(searchAttendeesQuery);
    setSearchAppliedRange(appliedRange);
    setSearchAppliedMatchMode(searchMatchMode);
    setSearchAllEnabled(includeAll);
    if (matches.length === 0) {
      setSearchIndex(null);
      return;
    }
    let targetIndex = findSearchIndexInMatches(matches, "forward", nowSnapshot, false);
    if (targetIndex === null) {
      targetIndex = findSearchIndexInMatches(matches, "backward", nowSnapshot, false);
    }
    if (targetIndex === null) {
      setSearchIndex(null);
      return;
    }
    setSearchIndex(targetIndex);
    goToSearchDate(matches[targetIndex].date);
  };
  const handleSearchAll = async () => {
    if (!hasSearchInput) {
      setSearchAllEnabled(false);
      setSearchIndex(null);
      return;
    }
    setSearchAdvancedOpen(false);
    const appliedRange = searchRange;
    let ensuredEvents: CalendarEvent[] | null = null;
    setIsSearching(true);
    try {
      ensuredEvents = await actions.ensureRangeLoaded(appliedRange.start, appliedRange.end);
    } catch {
      // ignore fetch errors during search range expansion
    } finally {
      setIsSearching(false);
    }
    const matches = buildSearchMatches({
      titleQuery: searchQuery,
      locationQuery: searchLocationQuery,
      descriptionQuery: searchDescriptionQuery,
      attendeesQuery: searchAttendeesQuery,
      range: appliedRange,
      includeAll: true,
      matchMode: searchMatchMode,
      events: ensuredEvents ?? state.allEvents,
    });
    setSearchAppliedQuery(searchQuery);
    setSearchAppliedLocation(searchLocationQuery);
    setSearchAppliedDescription(searchDescriptionQuery);
    setSearchAppliedAttendees(searchAttendeesQuery);
    setSearchAppliedRange(appliedRange);
    setSearchAppliedMatchMode(searchMatchMode);
    setSearchAllEnabled(true);
    if (matches.length === 0) {
      setSearchIndex(null);
      return;
    }
    let targetIndex = findSearchIndexInMatches(matches, "forward", nowSnapshot, false);
    if (targetIndex === null) {
      targetIndex = findSearchIndexInMatches(matches, "backward", nowSnapshot, false);
    }
    if (targetIndex === null) {
      setSearchIndex(null);
      return;
    }
    setSearchIndex(targetIndex);
    goToSearchDate(matches[targetIndex].date);
  };
  const formatSearchResultMeta = (event: CalendarEvent) => {
    const startDate = parseISODateTime(event.start);
    if (!startDate) return "";
    return formatLongDate(startDate);
  };
  const formatSearchResultTime = (event: CalendarEvent) => {
    if (event.all_day) return "종일";
    return formatTimeRange(event.start, event.end);
  };
  const handleSearchStep = (direction: "forward" | "backward") => {
    if (searchMatches.length === 0) return;
    const baseDate =
      searchIndex === null ? nowSnapshot : searchMatches[searchIndex]?.date;
    if (!baseDate) return;
    const strict = searchIndex !== null;
    const targetIndex = findSearchIndex(direction, baseDate, strict);
    if (targetIndex === null) return;
    setSearchIndex(targetIndex);
    goToSearchDate(searchMatches[targetIndex].date);
  };
  const canSearchForward =
    searchMatches.length > 0 &&
    findSearchIndex(
      "forward",
      searchIndex === null ? nowSnapshot : searchMatches[searchIndex]?.date || nowSnapshot,
      searchIndex !== null
    ) !== null;
  const canSearchBackward =
    searchMatches.length > 0 &&
    findSearchIndex(
      "backward",
      searchIndex === null ? nowSnapshot : searchMatches[searchIndex]?.date || nowSnapshot,
      searchIndex !== null
    ) !== null;
  const ai = useAiAssistant({
    onApplied: actions.refresh,
    onAddApplied: (events) => {
      actions.ingest(events);
      undo.record(events);
    },
    onDeleteApplied: (ids) => {
      if (useGoogle) {
        actions.refresh(true);
        return;
      }
      actions.removeByIds(ids);
    },
  });

  useEffect(() => {
    if (searchOpen) {
      searchInputRef.current?.focus();
      setMobileMenuOpen(false);
    } else {
      setSearchIndex(null);
      setSearchAllEnabled(false);
      setSearchAdvancedOpen(false);
    }
  }, [searchOpen]);

  useEffect(() => {
    if (searchAllEnabled) {
      setSearchAdvancedOpen(false);
    }
  }, [searchAllEnabled]);

  useEffect(() => {
    cachedSearchRange = searchRange;
  }, [searchRange]);

  useEffect(() => {
    cachedAppliedSearchRange = searchAppliedRange;
  }, [searchAppliedRange]);


  const handleCreate = async (payload: Parameters<typeof actions.create>[0]) => {
    const created = await actions.create(payload);
    if (created) undo.record([created]);
    return created;
  };

  const handleCreateRecurring = async (payload: RecurringEventPayload) => {
    const created = await actions.createRecurring(payload);
    if (created && created.length > 0) undo.record(created);
    return created;
  };

  const selectedEvents = useMemo(() => {
    const key = toISODate(selectedDate);
    return state.indexes.byDate[key] || [];
  }, [state.indexes.byDate, selectedDate]);

  const monthCalendarEvents = useMemo(() => {
    return state.allEvents.map((event) => {
      const colors = getMonthEventColor(event);
      return {
        id: String(event.id),
        title: event.title,
        start: event.start,
        end: event.end || undefined,
        allDay: !!event.all_day,
        backgroundColor: colors.bg,
        borderColor: colors.border,
        textColor: colors.text,
        extendedProps: { raw: event },
      };
    });
  }, [state.allEvents]);
  const hasMonthEvents = useMemo(() => {
    const monthStart = startOfMonth(currentMonth);
    const monthEnd = endOfMonth(currentMonth);
    monthEnd.setHours(23, 59, 59, 999);
    return state.allEvents.some((event) => {
      const start = parseISODateTime(event.start);
      if (!start) return false;
      const end = parseISODateTime(event.end || "") || start;
      return start <= monthEnd && end >= monthStart;
    });
  }, [currentMonth, state.allEvents]);

  useEffect(() => {
    const updateScrollFlags = () => {
      const sidebarEl = sidebarScrollRef.current;
      if (sidebarEl) {
        const canScroll = sidebarEl.scrollHeight > sidebarEl.clientHeight + 1;
        setSidebarScrollable(canScroll);
        setSidebarAtTop(sidebarEl.scrollTop <= 1);
        setSidebarAtBottom(sidebarEl.scrollTop + sidebarEl.clientHeight >= sidebarEl.scrollHeight - 1);
      }
      const mobileEl = mobileScrollRef.current;
      if (mobileEl) {
        const canScroll = mobileEl.scrollHeight > mobileEl.clientHeight + 1;
        setMobileScrollable(canScroll);
        setMobileAtTop(mobileEl.scrollTop <= 1);
        setMobileAtBottom(mobileEl.scrollTop + mobileEl.clientHeight >= mobileEl.scrollHeight - 1);
      }
    };

    updateScrollFlags();
    window.addEventListener("resize", updateScrollFlags);
    return () => {
      window.removeEventListener("resize", updateScrollFlags);
    };
  }, [selectedEvents.length, view]);

  useEffect(() => {
    const monthOfSelected = startOfMonth(selectedDate);
    if (monthOfSelected.getTime() !== currentMonth.getTime()) {
      setCurrentMonth(monthOfSelected);
    }
  }, [selectedDate, currentMonth]);

  useEffect(() => {
    if (!isTimeGridView) return;
    const api = miniCalendarRef.current?.getApi();
    if (!api) return;
    const rafId = window.requestAnimationFrame(() => {
      api.gotoDate(selectedDate);
    });
    return () => {
      window.cancelAnimationFrame(rafId);
    };
  }, [selectedDate, isTimeGridView]);

  useEffect(() => {
    if (!mobileMenuOpen) return;
    const handlePointer = (event: MouseEvent | TouchEvent) => {
      const target = event.target as Node;
      if (mobileMenuRef.current && !mobileMenuRef.current.contains(target)) {
        setMobileMenuOpen(false);
      }
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileMenuOpen(false);
    };
    document.addEventListener("mousedown", handlePointer);
    document.addEventListener("touchstart", handlePointer);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handlePointer);
      document.removeEventListener("touchstart", handlePointer);
      document.removeEventListener("keydown", handleKey);
    };
  }, [mobileMenuOpen]);

  const upcomingEvent = useMemo(() => {
    return state.allEvents
      .map((event) => ({
        event,
        start: parseISODateTime(event.start),
      }))
      .filter((item) => item.start && item.start >= nowSnapshot)
      .sort((a, b) => (a.start?.getTime() || 0) - (b.start?.getTime() || 0))
      .map((item) => item.event)[0];
  }, [state.allEvents, nowSnapshot]);

  const upNextLabel = (() => {
    if (!upcomingEvent) return "";
    const start = parseISODateTime(upcomingEvent.start);
    if (!start) return "";
    const diffMinutes = Math.max(0, Math.floor((start.getTime() - nowSnapshot.getTime()) / 60000));
    if (diffMinutes < 60) return `${diffMinutes}분 후`;
    if (diffMinutes < 60 * 24) {
      const diffHours = Math.floor(diffMinutes / 60);
      return `${diffHours}시간 후`;
    }

    const startDate = new Date(start.getFullYear(), start.getMonth(), start.getDate());
    const nowDate = new Date(nowSnapshot.getFullYear(), nowSnapshot.getMonth(), nowSnapshot.getDate());
    const diffDays = Math.max(
      0,
      Math.floor((startDate.getTime() - nowDate.getTime()) / (1000 * 60 * 60 * 24))
    );
    if (diffDays < 7) {
      return `${Math.max(1, diffDays)}일 후`;
    }
    if (diffDays < 31) {
      const weeks = Math.floor(diffDays / 7);
      return `${Math.max(1, weeks)}주 후`;
    }

    let months =
      (start.getFullYear() - nowSnapshot.getFullYear()) * 12 +
      (start.getMonth() - nowSnapshot.getMonth());
    const anchor = new Date(nowSnapshot.getFullYear(), nowSnapshot.getMonth() + months, nowSnapshot.getDate());
    if (anchor > start) {
      months -= 1;
    }

    if (months < 12) {
      return `${Math.max(1, months)}달 후`;
    }
    const years = Math.floor(months / 12);
    return `${Math.max(1, years)}년 후`;
  })();

  const handleViewChange = (next: ViewMode) => {
    const api = monthCalendarRef.current?.getApi();
    const anchorDate = selectedDate;
    if (api) {
      const targetView =
        next === "month" ? "dayGridMonth" : next === "week" ? "timeGridWeek" : "timeGridDay";
      api.changeView(targetView, anchorDate);
    }
    const target = next === "month" ? "/calendar" : `/calendar?view=${next}`;
    setCurrentMonth(startOfMonth(anchorDate));
    router.replace(target, { scroll: false });
  };

  const handlePrev = () => {
    const api = monthCalendarRef.current?.getApi();
    if (api) {
      if (view === "week" || view === "day") {
        const shiftDays = view === "week" ? -7 : -1;
        let nextDate = addDays(selectedDate, shiftDays);
        if (view === "week") {
          const monthStart = startOfMonth(currentMonth);
          const monthEnd = endOfMonth(currentMonth);
          const weekStart = startOfWeek(nextDate);
          const weekEnd = addDays(weekStart, 6);
          const isOutsideMonth = nextDate < monthStart || nextDate > monthEnd;
          const overlapsMonth = weekStart <= monthEnd && weekEnd >= monthStart;
          if (isOutsideMonth && overlapsMonth) {
            nextDate = weekStart > monthStart ? weekStart : monthStart;
          }
        }
        api.gotoDate(nextDate);
        setSelectedDate(nextDate);
        setCurrentMonth(startOfMonth(nextDate));
        return;
      }
      api.prev();
      const d = api.getDate();
      setCurrentMonth(startOfMonth(d));
      setSelectedDate(d);
      return;
    }
    const shiftDays = view === "week" ? -7 : -1;
    const nextDate = addDays(selectedDate, shiftDays);
    setSelectedDate(nextDate);
    setCurrentMonth(startOfMonth(nextDate));
  };

  const handleNext = () => {
    const api = monthCalendarRef.current?.getApi();
    if (api) {
      if (view === "week" || view === "day") {
        const shiftDays = view === "week" ? 7 : 1;
        let nextDate = addDays(selectedDate, shiftDays);
        if (view === "week") {
          const monthStart = startOfMonth(currentMonth);
          const monthEnd = endOfMonth(currentMonth);
          const weekStart = startOfWeek(nextDate);
          const weekEnd = addDays(weekStart, 6);
          const isOutsideMonth = nextDate < monthStart || nextDate > monthEnd;
          const overlapsMonth = weekStart <= monthEnd && weekEnd >= monthStart;
          if (isOutsideMonth && overlapsMonth) {
            nextDate = weekEnd < monthEnd ? weekEnd : monthEnd;
          }
        }
        api.gotoDate(nextDate);
        setSelectedDate(nextDate);
        setCurrentMonth(startOfMonth(nextDate));
        return;
      }
      api.next();
      const d = api.getDate();
      setCurrentMonth(startOfMonth(d));
      setSelectedDate(d);
      return;
    }
    const shiftDays = view === "week" ? 7 : 1;
    const nextDate = addDays(selectedDate, shiftDays);
    setSelectedDate(nextDate);
    setCurrentMonth(startOfMonth(nextDate));
  };

  const handleToday = () => {
    const api = monthCalendarRef.current?.getApi();
    if (api) {
      api.today();
      const d = api.getDate();
      const anchorDate = view === "week" ? today : d;
      setSelectedDate(anchorDate);
      setCurrentMonth(startOfMonth(anchorDate));
      return;
    }
    setSelectedDate(today);
    setCurrentMonth(startOfMonth(today));
  };

  const monthNumberLabel = `${currentMonth.getMonth() + 1}월`;
  const viewTitle =
    view === "month"
      ? monthNumberLabel
      : view === "week"
        ? `${getWeekOfMonth(currentMonth, weekStart)}주차`
        : formatLongDate(selectedDate);
  const viewBadge = null;
  const weekdayLabels = useMemo(() => {
    const labels = ["일", "월", "화", "수", "목", "금", "토"];
    const startIndex = 1;
    return labels.slice(startIndex).concat(labels.slice(0, startIndex));
  }, []);

  return (
    <div
      className={`month-shell bg-background-light dark:bg-background-dark text-slate-900 dark:text-white ${
        view === "month" ? "overflow-visible" : "overflow-hidden"
      } h-screen flex flex-col transition-colors duration-200`}
    >
      <AiAssistantModal
        assistant={ai}
        onEditAddItem={(item, index) => {
          const draft = buildAiDraftEvent(item, selectedDate);
          setAiDraftEvent(draft);
          setAiDraftIndex(index);
          setActiveEvent(null);
          setModalOpen(true);
        }}
      />
      <EventModal
        open={modalOpen}
        event={aiDraftEvent ?? activeEvent}
        forceCreate={Boolean(aiDraftEvent)}
        defaultDate={selectedDate}
        onClose={() => {
          setModalOpen(false);
          setActiveEvent(null);
          setAiDraftEvent(null);
          setAiDraftIndex(null);
        }}
        onCreate={async (payload) => {
          if (aiDraftIndex !== null) {
            ai.updateAddPreviewItem(aiDraftIndex, {
              type: "single",
              title: payload.title,
              start: payload.start,
              end: payload.end ?? null,
              location: payload.location ?? null,
              description: payload.description ?? null,
              attendees: payload.attendees ?? null,
              reminders: payload.reminders ?? null,
              visibility: payload.visibility ?? null,
              transparency: payload.transparency ?? null,
              meeting_url: payload.meeting_url ?? null,
              timezone: payload.timezone ?? null,
              color_id: payload.color_id ?? null,
              all_day: Boolean(payload.all_day),
            });
            return null;
          }
          return handleCreate(payload);
        }}
        onCreateRecurring={async (payload) => {
          if (aiDraftIndex !== null) {
            const end = payload.recurrence.end || null;
            ai.updateAddPreviewItem(aiDraftIndex, {
              type: "recurring",
              title: payload.title,
              start_date: payload.start_date,
              time: payload.time ?? null,
              duration_minutes: payload.duration_minutes ?? null,
              location: payload.location ?? null,
              description: payload.description ?? null,
              attendees: payload.attendees ?? null,
              reminders: payload.reminders ?? null,
              visibility: payload.visibility ?? null,
              transparency: payload.transparency ?? null,
              meeting_url: payload.meeting_url ?? null,
              timezone: payload.timezone ?? null,
              color_id: payload.color_id ?? null,
              recurrence: payload.recurrence,
              weekdays: payload.recurrence.byweekday ?? undefined,
              end_date: end?.until ?? null,
              count: end?.count ?? null,
            });
            return null;
          }
          return handleCreateRecurring(payload);
        }}
        onUpdate={actions.update}
        onDelete={actions.remove}
      />

      <header className="relative flex flex-col whitespace-nowrap px-6 py-3 bg-white/80 dark:bg-[#111418]/80 backdrop-blur-md sticky top-0 z-50 border-b border-gray-100 dark:border-gray-800">
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-4 text-slate-900 dark:text-white">
            <div className="h-8 px-3 rounded-lg border border-dashed border-slate-300 dark:border-slate-600 text-xs font-medium flex items-center justify-center text-gray-500">
              로고 예정
            </div>
            <div className="flex items-center gap-2"></div>
          </div>
          <div className="relative flex flex-1 items-center justify-end min-h-[44px]">
            <div
              className={`header-actions-layer flex flex-wrap items-center gap-3 justify-end ${
                searchOpen ? "is-hidden" : "is-visible"
              }`}
              aria-hidden={searchOpen}
            >
              <div
                className="relative hidden md:flex items-center bg-gray-100 dark:bg-gray-800 p-1 rounded-full mr-2 segmented-toggle"
                style={viewToggleStyle}
              >
                <span
                  className="segmented-indicator"
                >
                  <span
                    key={view}
                    className="view-indicator-pulse block h-full w-full rounded-full bg-white dark:bg-gray-700 shadow-sm"
                  />
                </span>
                {viewKeys.map((key) => (
                  <button
                    key={key}
                    className={`relative z-10 flex-1 px-3 py-1 text-sm font-semibold transition-colors ${
                      view === key
                        ? "text-gray-900 dark:text-white"
                        : "text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white"
                    }`}
                    type="button"
                    onClick={() => handleViewChange(key)}
                  >
                    {VIEW_LABELS[key]}
                  </button>
                ))}
              </div>
              <div className="flex gap-1">
                <button
                  className="relative flex items-center justify-center rounded-full size-9 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors text-slate-600 dark:text-slate-300 disabled:opacity-40 disabled:cursor-not-allowed"
                  type="button"
                  onClick={undo.undo}
                  disabled={undoCount === 0}
                  aria-label="마지막 작업 되돌리기"
                >
                  <Undo2 className="size-5" />
                  {undoCount > 0 && (
                    <span className="absolute -top-0.5 -right-0.5 min-w-[16px] h-4 px-1 rounded-full bg-slate-900 text-white text-[10px] font-semibold leading-4">
                      {undoCount}
                    </span>
                  )}
                </button>
                <button
                  className="flex items-center justify-center rounded-full size-9 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors text-slate-600 dark:text-slate-300"
                  type="button"
                  onClick={() => setSearchOpen(true)}
                  aria-label="일정 검색"
                >
                  <Search className="size-5" />
                </button>
                <button
                  className="flex items-center justify-center rounded-full size-9 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors text-slate-600 dark:text-slate-300"
                  type="button"
                  onClick={() => {
                    setActiveEvent(null);
                    setAiDraftEvent(null);
                    setAiDraftIndex(null);
                    setModalOpen(true);
                  }}
                  aria-label="일정 추가"
                >
                  <Plus className="size-5" />
                </button>
              </div>
              <div className="hidden md:flex">
                <CalendarHeaderActions status={state.authStatus} />
              </div>
              <div ref={mobileMenuRef} className="relative md:hidden">
                <button
                  className="flex items-center justify-center rounded-full size-9 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors text-slate-600 dark:text-slate-300"
                  type="button"
                  onClick={() => setMobileMenuOpen((prev) => !prev)}
                  aria-label="사용자 메뉴"
                  aria-haspopup="menu"
                  aria-expanded={mobileMenuOpen}
                >
                  <MoreVertical className="size-5" />
                </button>
                {mobileMenuOpen && (
                  <div className="absolute right-0 mt-2 w-52 rounded-xl border border-gray-100 dark:border-gray-700 bg-white dark:bg-[#111418] shadow-lg p-3 flex flex-col gap-2 z-50">
                    <CalendarHeaderActions
                      status={state.authStatus}
                      className="flex flex-col items-stretch gap-2"
                      buttonClassName="w-full text-left"
                    />
                    <div className="h-px bg-gray-100 dark:bg-gray-800"></div>
                    <div className="flex items-center gap-2 rounded-lg border border-gray-200 dark:border-gray-700 px-2 py-2">
                      <div
                        className="bg-center bg-no-repeat bg-cover rounded-full size-7 border border-gray-200 dark:border-gray-700 shadow-sm"
                        data-alt="사용자 프로필 이미지"
                        style={{
                          backgroundImage:
                            "url('https://lh3.googleusercontent.com/aida-public/AB6AXuDtp4EN6aKO3e3qE7dZReqE5nVIXN_43sBCdsgWGm4dzvClBNxW2Pt1ibIGwyQGQMdAIBX_9RVDwfqDlnwBKi8NUIR8rfqDGSj3ORylu9O-CXp3AbsLY8YZ3mR-GbbYWBsxTQB71hnJnS4lk0cKSAhR2Mze8_hVjC0o-hEK8J-0fJFYlA65gMBrartXdJiV-A1yCzwWF3mFEhJe5idk641dS6JWo1bXrr9PhY-ZLclsGGcfXhRrdchRQLXlbMpMc3vMNQXkbvxka-4')",
                        }}
                      ></div>
                      <span className="text-xs font-medium text-slate-600 dark:text-slate-300">프로필 사진</span>
                    </div>
                  </div>
                )}
              </div>
              <div
                className="hidden md:block bg-center bg-no-repeat bg-cover rounded-full size-8 border border-gray-200 dark:border-gray-700 shadow-sm"
                data-alt="사용자 프로필 이미지"
                style={{
                  backgroundImage:
                    "url('https://lh3.googleusercontent.com/aida-public/AB6AXuDtp4EN6aKO3e3qE7dZReqE5nVIXN_43sBCdsgWGm4dzvClBNxW2Pt1ibIGwyQGQMdAIBX_9RVDwfqDlnwBKi8NUIR8rfqDGSj3ORylu9O-CXp3AbsLY8YZ3mR-GbbYWBsxTQB71hnJnS4lk0cKSAhR2Mze8_hVjC0o-hEK8J-0fJFYlA65gMBrartXdJiV-A1yCzwWF3mFEhJe5idk641dS6JWo1bXrr9PhY-ZLclsGGcfXhRrdchRQLXlbMpMc3vMNQXkbvxka-4')",
                }}
              ></div>
            </div>
            <div
              className={`header-search-layer flex w-full items-center gap-2 ${
                searchOpen ? "is-visible" : "is-hidden"
              }`}
              aria-hidden={!searchOpen}
            >
              <div className="flex w-full items-center gap-2">
                <div className="relative w-full max-w-[420px] min-w-0 ml-auto">
                  <div
                    className={`flex items-center gap-2 border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] px-3 py-2 ${
                      searchAdvancedOpen && !searchAllEnabled
                        ? "rounded-t-[22px] rounded-b-none border-b-0"
                        : "rounded-full"
                    }`}
                  >
                    <Search className="size-4 text-slate-400" />
                    <input
                      ref={searchInputRef}
                      className="flex-1 bg-transparent text-sm text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none"
                      placeholder="제목"
                      value={searchQuery}
                      onChange={(event) => setSearchQuery(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") void applySearchFilters();
                        if (event.key === "Escape") setSearchOpen(false);
                      }}
                    />
                    <button
                      className="flex items-center justify-center rounded-full p-1 text-slate-700 dark:text-slate-200 hover:bg-gray-100 dark:hover:bg-gray-800"
                      type="button"
                      onClick={() => {
                        if (searchAllEnabled) {
                          setSearchAllEnabled(false);
                          setSearchAdvancedOpen((prev) => !prev);
                          return;
                        }
                        setSearchAllEnabled(false);
                        setSearchAdvancedOpen((prev) => !prev);
                      }}
                      aria-expanded={searchAdvancedOpen}
                      aria-label="고급 일정"
                    >
                      {searchAdvancedOpen ? (
                        <ChevronUp className="size-4" />
                      ) : (
                        <ChevronDown className="size-4" />
                      )}
                      <span className="sr-only">고급 일정</span>
                    </button>
                  </div>
                  {searchAdvancedOpen && !searchAllEnabled && (
                    <div className="absolute left-0 right-0 top-full mt-0 w-full rounded-b-[22px] border border-gray-200 dark:border-gray-700 border-t-0 bg-white dark:bg-[#111418] px-4 pb-4 pt-4 shadow-lg whitespace-normal z-40">
                      <div className="grid grid-cols-1 gap-3">
                        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                          <label className="flex flex-col gap-0.5">
                            <span className="text-[11px] text-slate-500">시작일</span>
                            <div className="relative">
                              <Calendar className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" />
                              <input
                                type="date"
                                className="w-full rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] pl-9 pr-3 py-2 text-sm text-slate-700 dark:text-slate-200"
                                value={searchRange.start}
                                onChange={(event) => setSearchRange((prev) => ({ ...prev, start: event.target.value }))}
                              />
                            </div>
                          </label>
                          <label className="flex flex-col gap-0.5">
                            <span className="text-[11px] text-slate-500">종료일</span>
                            <div className="relative">
                              <Calendar className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" />
                              <input
                                type="date"
                                className="w-full rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] pl-9 pr-3 py-2 text-sm text-slate-700 dark:text-slate-200"
                                value={searchRange.end}
                                onChange={(event) => setSearchRange((prev) => ({ ...prev, end: event.target.value }))}
                              />
                            </div>
                          </label>
                        </div>
                        <div className="relative">
                          <MapPin className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" />
                          <input
                            className="w-full rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] pl-9 pr-3 py-2 text-sm text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none"
                            placeholder="장소"
                            value={searchLocationQuery}
                            onChange={(event) => setSearchLocationQuery(event.target.value)}
                          />
                        </div>
                        <div className="relative">
                          <Users className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" />
                          <input
                            className="w-full rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] pl-9 pr-3 py-2 text-sm text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none"
                            placeholder="참석자"
                            value={searchAttendeesQuery}
                            onChange={(event) => setSearchAttendeesQuery(event.target.value)}
                          />
                        </div>
                        <div className="relative">
                          <FileText className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-slate-400" />
                          <input
                            className="w-full rounded-full border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] pl-9 pr-3 py-2 text-sm text-slate-900 dark:text-white placeholder:text-slate-400 focus:outline-none"
                            placeholder="메모"
                            value={searchDescriptionQuery}
                            onChange={(event) => setSearchDescriptionQuery(event.target.value)}
                          />
                        </div>
                      </div>
                      <div className="mt-4 flex items-center gap-2 text-[11px] text-slate-500">
                        <span>조건</span>
                        <div className="inline-flex rounded-full border border-gray-200 dark:border-gray-700 overflow-hidden">
                          <button
                            className={`px-2.5 py-1 text-[11px] font-semibold ${
                              searchMatchMode === "and"
                                ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900"
                                : "text-slate-600 dark:text-slate-300 hover:bg-gray-100 dark:hover:bg-gray-800"
                            }`}
                            type="button"
                            onClick={() => setSearchMatchMode("and")}
                            aria-pressed={searchMatchMode === "and"}
                          >
                            AND
                          </button>
                          <button
                            className={`px-2.5 py-1 text-[11px] font-semibold ${
                              searchMatchMode === "or"
                                ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900"
                                : "text-slate-600 dark:text-slate-300 hover:bg-gray-100 dark:hover:bg-gray-800"
                            }`}
                            type="button"
                            onClick={() => setSearchMatchMode("or")}
                            aria-pressed={searchMatchMode === "or"}
                          >
                            OR
                          </button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
                <button
                  className="flex items-center gap-1 rounded-full px-3 py-2 text-xs font-semibold text-slate-700 dark:text-slate-200 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed"
                  type="button"
                  onClick={() => void applySearchFilters()}
                  disabled={isSearching || (!hasDraftFilters && !searchAllEnabled)}
                >
                  {isSearching ? "찾는 중" : "찾기"}
                </button>
                <button
                  className="flex items-center gap-1 rounded-full px-3 py-2 text-xs font-semibold text-slate-700 dark:text-slate-200 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed"
                  type="button"
                  onClick={() => void handleSearchAll()}
                  disabled={!hasSearchInput}
                >
                  모두 찾기
                </button>
                <button
                  className="flex items-center gap-1 rounded-full px-3 py-2 text-xs font-semibold text-slate-700 dark:text-slate-200 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed"
                  type="button"
                  onClick={() => handleSearchStep("backward")}
                  disabled={!canSearchBackward}
                  aria-label="뒤로 찾기"
                >
                  <ChevronLeft className="size-4" />
                </button>
                <button
                  className="flex items-center gap-1 rounded-full px-3 py-2 text-xs font-semibold text-slate-700 dark:text-slate-200 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed"
                  type="button"
                  onClick={() => handleSearchStep("forward")}
                  disabled={!canSearchForward}
                  aria-label="앞으로 찾기"
                >
                  <ChevronRight className="size-4" />
                </button>
                <button
                  className="flex items-center justify-center rounded-full size-9 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors text-slate-600 dark:text-slate-300"
                  type="button"
                  onClick={() => setSearchOpen(false)}
                  aria-label="검색 닫기"
                >
                  <X className="size-5" />
                </button>
              </div>
              </div>
            </div>
          </div>
        {searchAllEnabled && (
          <div className="absolute left-0 right-0 top-full mt-2 whitespace-normal z-50">
            <div className="px-6">
              <div className="w-full max-w-[500px] ml-auto rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] px-4 py-3 shadow-lg">
                <div className="flex items-center text-xs text-slate-600 dark:text-slate-300">
                  <span className="font-semibold">모두 찾기 결과</span>
                  <span className="ml-auto text-slate-500">결과 {searchMatches.length}개</span>
                  <button
                    className="ml-2 flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold text-slate-600 dark:text-slate-200 hover:bg-gray-100 dark:hover:bg-gray-800"
                    type="button"
                    onClick={() => setSearchAllEnabled(false)}
                    aria-label="모두 찾기 닫기"
                  >
                    <X className="size-3.5" />
                    닫기
                  </button>
                </div>
                <div className="mt-3 max-h-[40vh] overflow-y-auto space-y-2 pr-1">
                  {searchMatches.length === 0 ? (
                    <p className="text-xs text-slate-400">모두 찾기 결과가 없습니다.</p>
                  ) : (
                    searchMatches.map(({ event }) => (
                      <div
                        key={`${event.id}-${event.start}`}
                        className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] px-3 py-2"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-sm font-semibold text-slate-900 dark:text-white">
                            {event.title}
                          </p>
                        </div>
                        <p className="text-xs text-slate-500 flex items-center gap-1">
                          <Calendar className="size-3.5 text-slate-400" />
                          {formatSearchResultMeta(event)}
                        </p>
                        <p className="text-xs text-slate-500 flex items-center gap-1">
                          <Clock className="size-3.5 text-slate-400" />
                          {formatSearchResultTime(event)}
                        </p>
                        {event.location && (
                          <p className="text-xs text-slate-500">{event.location}</p>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>
        )}
      </header>

      <main
        className={`flex-1 flex flex-col max-w-[1600px] mx-auto w-full p-4 md:p-6 lg:p-6 gap-6 min-h-0 ${
          view === "month" ? "overflow-visible" : "overflow-hidden"
        }`}
      >
        <div className="flex flex-col lg:flex-row gap-6 flex-1 min-h-0">
          <aside className="hidden lg:flex order-2 lg:order-1 w-full lg:w-[320px] xl:w-[360px] flex-col gap-4 overflow-hidden min-h-0 lg:flex-none">
            <div className="hidden lg:block rounded-2xl p-5 text-white shadow-lg shadow-blue-500/20 relative overflow-hidden bg-[#1E6BFF]">
              <div className="relative z-10 flex flex-col gap-4">
                <div className="flex items-start">
                  <span className="bg-white/10 px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider text-[#D6E4FF]">
                    다음 일정
                  </span>
                </div>
                <div>
                  {upNextLabel ? (
                    <div className="text-[#EAF1FF] text-xs font-medium leading-[1.4] mb-1">{upNextLabel}</div>
                  ) : null}
                  <h3 className="text-[22px] font-bold leading-[1.2] mb-2 text-white">
                    {upcomingEvent ? upcomingEvent.title : "예정된 일정 없음"}
                  </h3>
                  <div className="flex items-center gap-2 text-[#EAF1FF] text-sm font-semibold leading-[1.4]">
                    <Clock className="size-4 text-[#C3D6FF]" />
                    <span>{upcomingEvent ? formatTimeRange(upcomingEvent.start, upcomingEvent.end) : ""}</span>
                  </div>
                </div>
              </div>
            </div>
            {isTimeGridView && (
              <div className="mini-calendar bg-white dark:bg-[#111418] rounded-2xl p-4 shadow-sm border border-gray-100 dark:border-gray-800">
                <FullCalendar
                  ref={miniCalendarRef}
                  plugins={[dayGridPlugin, interactionPlugin]}
                  initialView="dayGridMonth"
                  initialDate={selectedDate}
                  height="auto"
                  fixedWeekCount={false}
                  firstDay={1}
                  locale="ko"
                  headerToolbar={{ left: "title", center: "", right: "prev,next" }}
                  titleFormat={{ year: "numeric", month: "long" }}
                  events={[]}
                  dayCellContent={(arg) => {
                    const numberText = arg.dayNumberText.replace(/[^\d]/g, "");
                    return <span>{numberText}</span>;
                  }}
                  dayCellDidMount={(info) => {
                    const weekKey = getWeekKey(info.date);
                    info.el.dataset.weekKey = weekKey;
                    const handleEnter = () => toggleMiniWeekHover(weekKey, true);
                    const handleLeave = () => toggleMiniWeekHover(weekKey, false);
                    info.el.addEventListener("mouseenter", handleEnter);
                    info.el.addEventListener("mouseleave", handleLeave);
                    (info.el as HTMLElement & { __weekHoverHandlers?: { enter: () => void; leave: () => void } })
                      .__weekHoverHandlers = { enter: handleEnter, leave: handleLeave };
                  }}
                  dayCellWillUnmount={(info) => {
                    const el = info.el as HTMLElement & { __weekHoverHandlers?: { enter: () => void; leave: () => void } };
                    if (el.__weekHoverHandlers) {
                      el.removeEventListener("mouseenter", el.__weekHoverHandlers.enter);
                      el.removeEventListener("mouseleave", el.__weekHoverHandlers.leave);
                      delete el.__weekHoverHandlers;
                    }
                  }}
                  dayCellClassNames={(arg) =>
                    {
                      const day = toDateOnly(arg.date);
                      const weekStartDay = toDateOnly(startOfWeek(day));
                      const weekEndDay = addDays(weekStartDay, 6);
                      const classes: string[] = [];
                      if (day >= selectedWeekStartDay && day <= selectedWeekEndDay) {
                        classes.push("mini-week-selected");
                      }
                      if (isSameDay(day, weekStartDay)) classes.push("mini-week-start");
                      if (isSameDay(day, weekEndDay)) classes.push("mini-week-end");
                      if (view === "day" && isSameDay(day, selectedDate)) {
                        classes.push("fc-day-selected");
                      }
                      return classes;
                    }
                  }
                  dateClick={(info) => {
                    const isWeekView = view === "week";
                    const selectedAnchor = info.date;
                    setSelectedDate(selectedAnchor);
                    setCurrentMonth(startOfMonth(info.date));
                    const api = monthCalendarRef.current?.getApi();
                    if (api) {
                      const targetView = isWeekView ? "timeGridWeek" : "timeGridDay";
                      api.changeView(targetView, info.date);
                    }
                  }}
                />
              </div>
            )}
            {view === "month" && (
              <div className="bg-white dark:bg-[#111418] rounded-2xl p-5 shadow-sm border border-gray-200 dark:border-gray-800 flex flex-col">
                <div className="relative">
                  {sidebarScrollable && !sidebarAtTop && (
                    <div className="pointer-events-none absolute inset-x-0 top-0 h-6 bg-gradient-to-b from-white via-white/80 to-transparent dark:from-[#111418] dark:via-[#111418]/80 z-10"></div>
                  )}
                  {sidebarScrollable && !sidebarAtBottom && (
                    <div className="pointer-events-none absolute inset-x-0 bottom-0 h-6 bg-gradient-to-t from-white via-white/80 to-transparent dark:from-[#111418] dark:via-[#111418]/80 z-10"></div>
                  )}
                  <div
                    ref={sidebarScrollRef}
                    className="relative max-h-[calc(100vh-340px)] overflow-y-auto no-scrollbar pr-2"
                    onScroll={() => {
                      const el = sidebarScrollRef.current;
                      if (!el) return;
                      setSidebarAtTop(el.scrollTop <= 1);
                      setSidebarAtBottom(el.scrollTop + el.clientHeight >= el.scrollHeight - 1);
                    }}
                  >
                    <div className="pointer-events-none absolute left-[7px] top-0 bottom-0 w-px bg-gray-100 dark:bg-gray-800"></div>
                    <div className="space-y-6">
                      {selectedEvents.length === 0 && (
                        <p className="text-xs text-slate-400 pl-6">해당 날짜에 일정이 없습니다.</p>
                      )}
                    {selectedEvents.map((event) => (
                      <div key={event.id} className="relative group pl-6">
                        <div className="absolute left-0 top-1 bg-white dark:bg-[#111418] border-2 border-gray-200 dark:border-gray-700 size-3.5 rounded-full group-hover:border-primary transition-colors"></div>
                        <div className="flex flex-col gap-1">
                          <span className="text-xs font-semibold text-gray-700 leading-[1.5]">
                            {formatTime(event.start)}
                          </span>
                          <button
                            className={`rounded-lg px-3 py-2.5 text-left transition-colors cursor-pointer group-hover:shadow-sm ${
                              activeEvent?.id === event.id
                                ? "bg-[#EFF6FF]"
                                : "bg-background-light dark:bg-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800"
                            }`}
                            type="button"
                            onClick={() => {
                              setActiveEvent(event);
                              setAiDraftEvent(null);
                              setAiDraftIndex(null);
                              setModalOpen(true);
                            }}
                          >
                            <p className="text-gray-900 dark:text-white font-semibold text-[15px] leading-[1.4]">
                              {event.title}
                            </p>
                            <p className="text-gray-500 text-xs font-normal leading-[1.4]">
                              {event.location || ""}
                            </p>
                          </button>
                        </div>
                      </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </aside>

          <div className="order-1 lg:order-2 flex-1 flex flex-col gap-4 min-h-0 min-w-0">
            <div className="hidden lg:hidden rounded-2xl p-5 text-white shadow-lg shadow-blue-500/20 relative overflow-hidden bg-[#1E6BFF]">
              <div className="relative z-10 flex flex-col gap-4">
                <div className="flex items-start">
                  <span className="bg-white/10 px-2.5 py-0.5 rounded-full text-xs font-semibold uppercase tracking-wider text-[#D6E4FF]">
                    다음 일정
                  </span>
                </div>
                <div>
                  {upNextLabel ? (
                    <div className="text-[#EAF1FF] text-xs font-medium leading-[1.4] mb-1">{upNextLabel}</div>
                  ) : null}
                  <h3 className="text-[22px] font-bold leading-[1.2] mb-2 text-white">
                    {upcomingEvent ? upcomingEvent.title : "예정된 일정 없음"}
                  </h3>
                  <div className="flex items-center gap-2 text-[#EAF1FF] text-sm font-semibold leading-[1.4]">
                    <Clock className="size-4 text-[#C3D6FF]" />
                    <span>{upcomingEvent ? formatTimeRange(upcomingEvent.start, upcomingEvent.end) : ""}</span>
                  </div>
                </div>
              </div>
            </div>
            <section
              className={`flex-1 bg-white dark:bg-[#111418] rounded-2xl shadow-sm border border-gray-100 dark:border-gray-800 flex flex-col ${
                view === "month" ? "overflow-visible" : "overflow-hidden"
              } min-h-[300px]`}
            >
            <div className="flex items-center gap-3 px-6 py-4 shrink-0">
              <div className="flex items-baseline gap-2">
                {view === "month" && (
                  <span className="text-[24px] font-bold leading-[1.2] text-gray-900 dark:text-white">
                    {currentMonth.getFullYear()}년
                  </span>
                )}
                <h2 className="text-[24px] font-bold leading-[1.2] text-gray-900 dark:text-white">
                  {viewTitle}
                </h2>
              </div>
              {viewBadge && (
                <span className="text-xs font-semibold text-slate-500">{viewBadge}</span>
              )}
              <div className="ml-auto flex items-center gap-1">
                <button
                  className="size-8 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 text-slate-600 dark:text-slate-300"
                  type="button"
                  onClick={handlePrev}
                  aria-label="이전"
                >
                  <ChevronLeft className="size-5" />
                </button>
                <button
                  className="px-3 py-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-[13px] font-medium text-gray-700 dark:text-slate-300"
                  type="button"
                  onClick={handleToday}
                >
                  오늘
                </button>
                <button
                  className="size-8 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 text-slate-600 dark:text-slate-300"
                  type="button"
                  onClick={handleNext}
                  aria-label="다음"
                >
                  <ChevronRight className="size-5" />
                </button>
              </div>
            </div>
            <div className="flex-1 px-4 py-3 flex flex-col min-h-0 overflow-hidden">
              <div className="flex-1 min-h-0 overflow-hidden flex flex-col">
              <div className="flex-1 min-h-0 flex flex-col">
                <div className="flex-1 min-h-0 overflow-visible border border-gray-100 dark:border-gray-800 border-t-0 border-l-0 border-r-0 bg-white dark:bg-[#111418] flex flex-col relative z-10">
                  {view === "month" && (
                    <div className="grid grid-cols-7">
                      {weekdayLabels.map((label) => (
                        <div
                          key={label}
                          className="py-2 text-center text-[12px] font-semibold uppercase tracking-[0.08em] text-gray-600"
                        >
                          {label}
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex-1 min-h-0 overflow-visible">
                    <FullCalendar
                      ref={monthCalendarRef}
                      plugins={[dayGridPlugin, timeGridPlugin, interactionPlugin]}
                      initialView={
                        view === "month" ? "dayGridMonth" : view === "week" ? "timeGridWeek" : "timeGridDay"
                      }
                      initialDate={selectedDate}
                      height={view === "month" ? (hasMonthEvents ? "100%" : "auto") : "100%"}
                      fixedWeekCount={false}
                      firstDay={1}
                      locale="ko"
                      headerToolbar={false}
                      allDayText="종일 일정"
                      displayEventTime={view !== "month"}
                      eventTimeFormat={{ hour: "numeric", minute: "2-digit", hour12: true }}
                      eventDisplay="block"
                      eventMinWidth={0}
                      slotEventOverlap={false}
                      dayMaxEventRows={6}
                      slotLabelContent={(arg) => {
                        if (arg.view.type === "timeGridWeek" || arg.view.type === "timeGridDay") {
                          const { period, time } = formatTimeGridSlotLabel(arg.date);
                          return (
                            <div className="fc-timegrid-slot-label-custom">
                              <span className="fc-timegrid-weekday">{period}</span>
                              <span className="fc-timegrid-date">{time}</span>
                            </div>
                          );
                        }
                        return arg.text;
                      }}
                      dayHeaderContent={(arg) => {
                        if (arg.view.type === "timeGridWeek" || arg.view.type === "timeGridDay") {
                          const weekday = arg.date.toLocaleDateString("ko-KR", { weekday: "short" });
                          const dayNumber = arg.date.getDate();
                          return (
                            <div className="fc-timegrid-day-header">
                              <span className="fc-timegrid-weekday">{weekday}</span>
                              <span className="fc-timegrid-date">{dayNumber}</span>
                            </div>
                          );
                        }
                        return arg.text;
                      }}
                      events={monthCalendarEvents}
                      dayCellContent={(arg) => {
                        const numberText = arg.dayNumberText.replace(/[^\d]/g, "");
                        return <span>{numberText}</span>;
                      }}
                      dayCellClassNames={(arg) =>
                        view === "month" && isSameDay(arg.date, selectedDate) ? ["fc-day-selected"] : []
                      }
                      dayCellDidMount={(info) => {
                        const el = info.el as HTMLElement & { __dblClickHandler?: (event: MouseEvent) => void };
                        const handler = () => {
                          setSelectedDate(info.date);
                          setActiveEvent(null);
                          setAiDraftEvent(null);
                          setAiDraftIndex(null);
                          setModalOpen(true);
                        };
                        el.__dblClickHandler = handler;
                        el.addEventListener("dblclick", handler);
                      }}
                      dayCellWillUnmount={(info) => {
                        const el = info.el as HTMLElement & { __dblClickHandler?: (event: MouseEvent) => void };
                        if (el.__dblClickHandler) {
                          el.removeEventListener("dblclick", el.__dblClickHandler);
                          delete el.__dblClickHandler;
                        }
                      }}
                      eventContent={(arg) => {
                        const textColor = arg.event.textColor || "#2f5bd6";
                        const continued = !arg.isStart;
                        const timeText = arg.timeText?.replace(/(오전|오후)\s*/g, "").trim();
                        const rawColorId = (arg.event.extendedProps as { raw?: CalendarEvent })?.raw?.color_id;
                        const isDefaultColor = !rawColorId || rawColorId === "default";
                        const backgroundColor = String(arg.event.backgroundColor || "#ffffff");
                        const borderColor = String(arg.event.borderColor || textColor);
                        const accentBase = isDefaultColor
                          ? textColor
                          : mixHexColors(borderColor, backgroundColor, 0.6);
                        const accentStrong = toRgba(accentBase, 1);
                        const accentFade = toRgba(accentBase, 0.3);
                        if (arg.view.type === "timeGridWeek" || arg.view.type === "timeGridDay") {
                          if (arg.event.allDay) {
                            return (
                              <div
                                className={`month-event-content${continued ? " month-event-continued" : ""}`}
                                style={{
                                  color: textColor,
                                  ["--event-accent" as never]: accentStrong,
                                  ["--event-accent-fade" as never]: accentFade,
                                }}
                              >
                                <span className="fc-event-title">{arg.event.title}</span>
                              </div>
                            );
                          }
                          return (
                            <div className="timegrid-event-content" style={{ color: textColor }}>
                              <span className="fc-event-title">{arg.event.title}</span>
                              {timeText && <span className="fc-event-time">{timeText}</span>}
                            </div>
                          );
                        }
                        return (
                          <div
                            className={`month-event-content${continued ? " month-event-continued" : ""}`}
                            style={{
                              color: textColor,
                              ["--event-accent" as never]: accentStrong,
                              ["--event-accent-fade" as never]: accentFade,
                            }}
                          >
                            <span className="fc-event-title">{arg.event.title}</span>
                          </div>
                        );
                      }}
                      moreLinkText={(num) => `+${num}`}
                      datesSet={(info) => {
                        if (info.view.type === "timeGridWeek") return;
                        const baseDate = info.view?.currentStart ?? info.start ?? new Date();
                        setCurrentMonth(startOfMonth(baseDate));
                        if (info.view.type === "dayGridMonth") {
                          const rangeStart = toDateOnly(info.start ?? baseDate);
                          const rangeEnd = toDateOnly(info.end ?? addDays(baseDate, 1));
                          const selected = toDateOnly(selectedDate);
                          if (selected < rangeStart || selected >= rangeEnd) {
                            setSelectedDate(baseDate);
                          }
                          return;
                        }
                        setSelectedDate(baseDate);
                      }}
                      dateClick={(info) => {
                        setSelectedDate(info.date);
                      }}
                      eventClick={(info) => {
                        const target = state.allEvents.find((ev) => String(ev.id) === String(info.event.id));
                        if (info.event.start) setSelectedDate(info.event.start);
                        if (target) {
                          setActiveEvent(target);
                          setAiDraftEvent(null);
                          setAiDraftIndex(null);
                          setModalOpen(true);
                        }
                      }}
                    />
                  </div>
                </div>
              </div>

              {view === "month" && (
                <div className="lg:hidden mt-2 pt-2">
                  <div className="text-xs font-semibold text-slate-500 mb-3">선택한 날짜 일정</div>
                  <div className="relative max-h-[20vh] overflow-hidden min-h-0">
                    {mobileScrollable && !mobileAtTop && (
                    <div className="pointer-events-none absolute inset-x-0 top-0 h-6 bg-gradient-to-b from-white via-white/80 to-transparent dark:from-[#111418] dark:via-[#111418]/80 z-10"></div>
                  )}
                  {mobileScrollable && !mobileAtBottom && (
                    <div className="pointer-events-none absolute inset-x-0 bottom-0 h-6 bg-gradient-to-t from-white via-white/80 to-transparent dark:from-[#111418] dark:via-[#111418]/80 z-10"></div>
                  )}
                  <div
                    ref={mobileScrollRef}
                    className="relative max-h-[20vh] overflow-y-auto no-scrollbar pr-2"
                    onScroll={() => {
                      const el = mobileScrollRef.current;
                      if (!el) return;
                      setMobileAtTop(el.scrollTop <= 1);
                      setMobileAtBottom(el.scrollTop + el.clientHeight >= el.scrollHeight - 1);
                    }}
                  >
                    <div className="pointer-events-none absolute left-[7px] top-0 bottom-0 w-px bg-gray-100 dark:bg-gray-800"></div>
                    <div className="space-y-4">
                      {selectedEvents.length === 0 && (
                        <p className="text-xs text-slate-400 pl-6">해당 날짜에 일정이 없습니다.</p>
                      )}
                      {selectedEvents.map((event) => (
                        <div key={`mobile-${event.id}`} className="relative group pl-6">
                          <div className="absolute left-0 top-1 bg-white dark:bg-[#111418] border-2 border-gray-200 dark:border-gray-700 size-3.5 rounded-full group-hover:border-primary transition-colors"></div>
                          <div className="flex flex-col gap-1">
                            <span className="text-xs font-semibold text-gray-700 leading-[1.5]">
                              {formatTime(event.start)}
                            </span>
                            <button
                              className={`rounded-lg px-3 py-2.5 text-left transition-colors cursor-pointer group-hover:shadow-sm ${
                                activeEvent?.id === event.id
                                  ? "bg-[#EFF6FF]"
                                  : "bg-background-light dark:bg-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800"
                              }`}
                              type="button"
                              onClick={() => {
                                setActiveEvent(event);
                                setAiDraftEvent(null);
                                setAiDraftIndex(null);
                                setModalOpen(true);
                              }}
                            >
                              <p className="text-gray-900 dark:text-white font-semibold text-[15px] leading-[1.4]">
                                {event.title}
                              </p>
                              <p className="text-gray-500 text-xs font-normal leading-[1.4]">
                                {event.location || ""}
                              </p>
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
                </div>
              )}

              {state.loading && <p className="text-xs text-slate-400 mt-3">일정 불러오는 중...</p>}
              {state.error && <p className="text-xs text-red-500 mt-3">{state.error}</p>}
            </div>
            </div>
            </section>
          </div>
        </div>
      </main>
      <button
        className="fixed bottom-6 right-6 z-50 size-14 rounded-full bg-primary text-white shadow-lg shadow-blue-500/30 hover:bg-blue-600 transition-colors flex items-center justify-center"
        type="button"
        aria-label="AI 어시스턴트 열기"
        onClick={() => ai.openWithText("")}
      >
        <Sparkles className="size-6" />
      </button>
    </div>
  );
}

export default function CalendarPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-[#f7f7f5]" />}>
      <CalendarPageInner />
    </Suspense>
  );
}
