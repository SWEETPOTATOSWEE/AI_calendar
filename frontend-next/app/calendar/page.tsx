"use client";

import { Suspense, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import timeGridPlugin from "@fullcalendar/timegrid";
import interactionPlugin from "@fullcalendar/interaction";
import { motion } from "motion/react";
import "./fullcalendar.css";
import EventModal from "./components/EventModal";
import AiAssistantModal from "./components/AiAssistantModal";
import CalendarHeaderActions from "./components/CalendarHeaderActions";
import ThemeTokenProvider from "./components/ThemeTokenProvider";
import {
  addDays,
  addMonths,
  formatLongDate,
  formatMonthYear,
  formatSearchDate,
  formatShortDate,
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
import { DatePopover } from "./components/DatePopover";
import {
  Bell,
  Calendar as CalendarIcon,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  Clock,
  MapPin,
  MoreVertical,
  Pencil,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  RotateCcw,
  Search,
  Sparkles,
  Trash2,
  X,
  Undo2,
} from "lucide-react";

type ViewMode = "month" | "week" | "day";
type MonthPopupAlign = "left" | "right" | "center";
type MonthEventPopup = {
  event: CalendarEvent;
  top: number;
  left: number;
  align: MonthPopupAlign;
  anchorTop: number;
  anchorBottom: number;
};
type DayEventsPopup = {
  date: Date;
  events: CalendarEvent[];
  top: number;
  left: number;
  align: MonthPopupAlign;
  anchorTop: number;
  anchorBottom: number;
};

const VIEW_LABELS: Record<ViewMode, string> = {
  month: "월",
  week: "주",
  day: "일",
};



const MONTH_EVENT_COLOR_MAP: Record<string, { bg: string; border: string; text: string }> = {
  "1": { bg: "oklch(var(--success) / 0.15)", border: "oklch(var(--success))", text: "oklch(var(--success))" },
  "2": { bg: "oklch(var(--error) / 0.15)", border: "oklch(var(--error))", text: "oklch(var(--error))" },
  "3": { bg: "oklch(var(--warning) / 0.15)", border: "oklch(var(--warning))", text: "oklch(var(--warning))" },
  "4": { bg: "oklch(var(--info) / 0.15)", border: "oklch(var(--info))", text: "oklch(var(--info))" },
};

const DEFAULT_MONTH_EVENT_COLOR = { 
  bg: "oklch(var(--primary-low))", 
  border: "oklch(var(--primary))", 
  text: "oklch(var(--primary))" 
};
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
const parseISODate = (value?: string | null) => {
  if (!value) return null;
  const [yearStr, monthStr, dayStr] = value.split("-");
  const year = Number(yearStr);
  const month = Number(monthStr);
  const day = Number(dayStr);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
};
const getEventStartDate = (value?: string | null) => {
  if (!value) return null;
  const parsed = parseISODateTime(value) ?? parseISODate(value);
  if (parsed) return parsed;
  const fallback = new Date(value);
  return Number.isNaN(fallback.getTime()) ? null : fallback;
};
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

const formatReminderLabel = (minutes: number) => {
  if (!Number.isFinite(minutes) || minutes <= 0) return "알림";
  if (minutes % 1440 === 0) return `${minutes / 1440}일 전`;
  if (minutes % 60 === 0) return `${minutes / 60}시간 전`;
  return `${minutes}분 전`;
};

const getReminderLabel = (reminders?: number[] | null) => {
  if (!reminders || reminders.length === 0) return null;
  const sorted = reminders.filter((value) => Number.isFinite(value)).sort((a, b) => a - b);
  if (sorted.length === 0) return null;
  const primary = formatReminderLabel(sorted[0]);
  return sorted.length > 1 ? `${primary} 외 ${sorted.length - 1}개` : primary;
};

const getEventDateLabel = (event: CalendarEvent) => {
  const startDate = getEventStartDate(event.start);
  return startDate ? formatLongDate(startDate) : "날짜 미정";
};

const getEventTimeLabel = (event: CalendarEvent) => {
  if (event.all_day) return "종일";
  const timeRange = formatTimeRange(event.start, event.end ?? null);
  return timeRange || "시간 미정";
};

const getPopupAccentColor = (event: CalendarEvent) => {
  return getMonthEventColor(event).border;
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

function CalendarPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const today = new Date();

  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  const [currentMonth, setCurrentMonth] = useState(startOfMonth(today));
  const [selectedDate, setSelectedDate] = useState(today);
  const [activeEvent, setActiveEvent] = useState<CalendarEvent | null>(null);
  const [aiDraftEvent, setAiDraftEvent] = useState<CalendarEvent | null>(null);
  const [aiDraftIndex, setAiDraftIndex] = useState<number | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [eventFormResetKey, setEventFormResetKey] = useState(0);
  const [monthEventPopup, setMonthEventPopup] = useState<MonthEventPopup | null>(null);
  const [dayEventsPopup, setDayEventsPopup] = useState<DayEventsPopup | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchAdvancedOpen, setSearchAdvancedOpen] = useState(false);
  const [searchAdvancedHeight, setSearchAdvancedHeight] = useState(0);
  const [searchFilters, setSearchFilters] = useState(() => {
    const baseDate = new Date();
    return {
      title: "",
      attendees: "",
      location: "",
      exclude: "",
      startDate: "",
      endDate: "",
      rangeStart: toISODate(addMonths(baseDate, -12)),
      rangeEnd: toISODate(addMonths(baseDate, 12)),
    };
  });
  const [searchResults, setSearchResults] = useState<CalendarEvent[]>([]);
  const [searchResultsOpen, setSearchResultsOpen] = useState(false);
  const [searchResultsHeight, setSearchResultsHeight] = useState(0);
  const lastSearchKeyRef = useRef<string | null>(null);
  const [nowSnapshot] = useState(() => new Date());
  const [viewMenuOpen, setViewMenuOpen] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [leftPanelOpen, setLeftPanelOpen] = useState(false);
  const [rightPanelOpen, setRightPanelOpen] = useState(false);
  const [sidebarScrollable, setSidebarScrollable] = useState(false);
  const [mobileScrollable, setMobileScrollable] = useState(false);
  const [sidebarAtTop, setSidebarAtTop] = useState(true);
  const [sidebarAtBottom, setSidebarAtBottom] = useState(true);
  const [mobileAtTop, setMobileAtTop] = useState(true);
  const [mobileAtBottom, setMobileAtBottom] = useState(true);
  const [swipeOffset, setSwipeOffset] = useState(0);
  const [isSwiping, setIsSwiping] = useState(false);
  const sidebarScrollRef = useRef<HTMLDivElement | null>(null);
  const mobileScrollRef = useRef<HTMLDivElement | null>(null);
  const monthCalendarRef = useRef<FullCalendar | null>(null);
  const miniCalendarRef = useRef<FullCalendar | null>(null);
  const mobileMenuRef = useRef<HTMLDivElement | null>(null);
  const searchAdvancedRef = useRef<HTMLDivElement | null>(null);
  const searchResultsPanelRef = useRef<HTMLDivElement | null>(null);
  const monthPopupRef = useRef<HTMLDivElement | null>(null);
  const dayEventsPopupRef = useRef<HTMLDivElement | null>(null);
  const viewMenuRef = useRef<HTMLDivElement | null>(null);
  const calendarSwipeRef = useRef<HTMLDivElement | null>(null);
  const swipeStartRef = useRef<{ x: number; y: number; time: number } | null>(null);
  const skipDateClickRef = useRef(false);
  const searchResultsCacheRef = useRef<CalendarEvent[]>([]);

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

  const viewAnchor = view === "month" ? currentMonth : selectedDate;
  const { state, actions, useGoogle } = useCalendarData(rangeStart, rangeEnd, viewAnchor);
  const ai = useAiAssistant({
    onApplied: actions.refresh,
    onAddApplied: (events) => {
      actions.ingest(events);
    },
    onDeleteApplied: (ids) => {
      if (useGoogle) {
        actions.refresh(true);
        return;
      }
      actions.removeByIds(ids);
    },
  });

  // Helper function to safely parse event date
  const parseEventDate = (dateValue: string | Date | null | undefined): Date | null => {
    if (!dateValue) return null;
    if (dateValue instanceof Date) return dateValue;
    if (typeof dateValue === 'string') return parseISODateTime(dateValue);
    return null;
  };

  // Today's events filtering
  const todayEvents = useMemo(() => {
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const todayEnd = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 23, 59, 59);
    
    return state.events.filter(event => {
      const eventStart = parseEventDate(event.start);
      const eventEnd = parseEventDate(event.end) || eventStart;
      if (!eventStart) return false;
      return eventStart <= todayEnd && (!eventEnd || eventEnd >= todayStart);
    }).sort((a, b) => {
      const aStart = parseEventDate(a.start);
      const bStart = parseEventDate(b.start);
      if (!aStart || !bStart) return 0;
      return aStart.getTime() - bStart.getTime();
    });
  }, [state.events]);

  const ongoingEvents = useMemo(() => {
    const now = new Date();
    return todayEvents.filter(event => {
      const start = parseEventDate(event.start);
      const end = parseEventDate(event.end) || start;
      if (!start || !end) return false;
      return start <= now && end >= now;
    });
  }, [todayEvents]);

  const upcomingEvents = useMemo(() => {
    const now = new Date();
    const oneHourLater = new Date(now.getTime() + 60 * 60 * 1000);
    return todayEvents.filter(event => {
      const start = parseEventDate(event.start);
      if (!start) return false;
      return start > now && start <= oneHourLater;
    });
  }, [todayEvents]);

  const handleCreate = async (payload: Parameters<typeof actions.create>[0]) => {
    const created = await actions.create(payload);
    return created;
  };

  const handleCreateRecurring = async (payload: RecurringEventPayload) => {
    const created = await actions.createRecurring(payload);
    return created;
  };

  const resetEventForm = () => {
    setEventFormResetKey((prev) => prev + 1);
  };

  const openAiDrawer = () => {
    setSearchOpen(false);
    setModalOpen(false);
    if (!isMobile) {
      setRightPanelOpen(true);
    }
    ai.openWithText("");
  };

  const openEventDrawer = (options?: { reset?: boolean; showForm?: boolean }) => {
    setSearchOpen(false);
    if (options?.reset) {
      resetEventForm();
    }
    if (options?.showForm) {
      ai.close();
    }
    if (!isMobile) {
      setRightPanelOpen(true);
    }
    if (options?.showForm) {
      setModalOpen(true);
    }
  };

  const hideEventForm = () => {
    setModalOpen(false);
    setAiDraftEvent(null);
    setAiDraftIndex(null);
  };

  const closeEventDrawer = () => {
    hideEventForm();
    setRightPanelOpen(false);
    setSearchOpen(false);
    setSearchResultsOpen(false);
    setSearchAdvancedOpen(false);
  };

  const onSearchKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void handleAdvancedSearch();
    }
  };

  const searchUI = (
    <div className="flex h-full flex-col overflow-hidden min-h-0">
      {isMobile && (
        <div className="flex items-center justify-between px-3 pt-3 pb-2">
          <h3 className="text-[18px] font-semibold text-text-primary">일정 검색</h3>
          <button
            type="button"
            className="flex size-9 items-center justify-center rounded-full hover:bg-bg-subtle transition-colors text-text-secondary"
            onClick={closeEventDrawer}
            aria-label="닫기"
          >
            <X className="size-5" />
          </button>
        </div>
      )}
      <div className="px-3 py-2">
        <div className="flex items-center gap-2 rounded-xl border border-border-subtle bg-bg-canvas px-3 py-1.5 shadow-sm">
          <Search className="size-4 text-text-secondary" />
          <input
            className="flex-1 bg-transparent text-[13px] text-text-primary focus:outline-none placeholder:text-text-disabled"
            placeholder="일정 키워드 입력"
            value={searchFilters.title}
            onChange={(event) =>
              setSearchFilters((prev) => ({ ...prev, title: event.target.value }))
            }
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void handleBasicSearch();
              }
            }}
            aria-label="기본 검색어"
          />
          <button
            type="button"
            className={`flex size-7 items-center justify-center rounded-lg transition-colors hover:bg-bg-subtle ${
              searchAdvancedOpen ? "text-token-primary bg-bg-subtle" : "text-text-secondary"
            }`}
            onClick={() => setSearchAdvancedOpen((prev) => !prev)}
            aria-label="고급 검색 토글"
            aria-expanded={searchAdvancedOpen}
          >
            <ChevronDown
              className={`size-4 transition-transform ${searchAdvancedOpen ? "rotate-180" : ""}`}
            />
          </button>
        </div>
      </div>

      {searchAdvancedOpen && (
        <div className="px-3 pb-4 border-b border-border-subtle animate-in slide-in-from-top-1 duration-200">
          <div className="space-y-3 pt-1">
            <div className="space-y-1">
              <div className="text-[11px] font-medium text-text-secondary px-1">참석자</div>
              <input
                className="w-full rounded-lg border border-border-subtle bg-bg-canvas px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-1 focus:ring-token-primary/20"
                type="text"
                placeholder="참석자, 주최자 입력"
                value={searchFilters.attendees}
                onChange={(event) =>
                  setSearchFilters((prev) => ({ ...prev, attendees: event.target.value }))
                }
                onKeyDown={onSearchKeyDown}
              />
            </div>
            <div className="space-y-1">
              <div className="text-[11px] font-medium text-text-secondary px-1">장소</div>
              <input
                className="w-full rounded-lg border border-border-subtle bg-bg-canvas px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-1 focus:ring-token-primary/20"
                type="text"
                placeholder="위치 또는 회의실"
                value={searchFilters.location}
                onChange={(event) =>
                  setSearchFilters((prev) => ({ ...prev, location: event.target.value }))
                }
                onKeyDown={onSearchKeyDown}
              />
            </div>
            <div className="space-y-1">
              <div className="text-[11px] font-medium text-text-secondary px-1">제외할 검색어</div>
              <input
                className="w-full rounded-lg border border-border-subtle bg-bg-canvas px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-1 focus:ring-token-primary/20"
                type="text"
                placeholder="제외할 키워드"
                value={searchFilters.exclude}
                onChange={(event) =>
                  setSearchFilters((prev) => ({ ...prev, exclude: event.target.value }))
                }
                onKeyDown={onSearchKeyDown}
              />
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="space-y-1">
                <div className="text-[11px] font-medium text-text-secondary px-1">시작일</div>
                <DatePopover
                  className="w-full"
                  value={searchFilters.startDate}
                  onChange={(val) =>
                    setSearchFilters((prev) => ({ ...prev, startDate: val }))
                  }
                  label="시작일"
                  icon={<CalendarIcon className="size-3.5" />}
                  placeholder="시작일"
                />
              </div>
              <div className="space-y-1">
                <div className="text-[11px] font-medium text-text-secondary px-1">종료일</div>
                <DatePopover
                  className="w-full"
                  value={searchFilters.endDate}
                  onChange={(val) =>
                    setSearchFilters((prev) => ({ ...prev, endDate: val }))
                  }
                  label="종료일"
                  icon={<CalendarIcon className="size-3.5" />}
                  placeholder="종료일"
                />
              </div>
            </div>
            <div className="flex justify-end items-center pt-2">
              <button
                type="button"
                className="text-[11px] text-text-tertiary hover:text-text-secondary transition-colors inline-flex items-center gap-1"
                onClick={() => {
                  setSearchFilters((prev) => ({
                    ...prev,
                    attendees: "",
                    location: "",
                    exclude: "",
                    startDate: "",
                    endDate: "",
                  }));
                }}
              >
                <Undo2 className="size-3" /> 필터 초기화
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex-1 overflow-y-auto px-3 py-2 pb-10">
        {searchResultsOpen && (
          searchResults.length === 0 ? (
            <div className="py-10 text-center text-xs text-text-tertiary">
              검색 결과가 없습니다.
            </div>
          ) : (
            (() => {
              const groups: Record<string, typeof searchResults> = {};
              searchResults.forEach((event) => {
                const date = getEventStartDate(event.start);
                const key = date ? formatSearchDate(date) : "날짜 미지정";
                if (!groups[key]) groups[key] = [];
                groups[key].push(event);
              });

              return Object.entries(groups).map(([dateLabel, events], gIndex) => (
                <div key={`group-${dateLabel}-${gIndex}`} className="mb-4">
                  <div className="px-1 mb-1 text-[13px] font-medium text-text-primary border-b border-border-subtle pb-1">
                    {dateLabel}
                  </div>
                  <div className="space-y-0.5">
                    {events.map((event, index) => {
                      const colorId = (event as CalendarEvent).color_id || "default";
                      const colorData =
                        MONTH_EVENT_COLOR_MAP[colorId] || DEFAULT_MONTH_EVENT_COLOR;
                      const accentColor = colorData.border;
                      return (
                        <div
                          key={`search-result-drawer-${event.id}-${index}`}
                          className="group flex items-center gap-2 rounded-md px-2 py-1.5 text-[14px] text-text-primary hover:bg-bg-subtle transition-colors cursor-pointer"
                          onClick={() => focusSearchResult(event)}
                        >
                          <div
                            className="w-[3px] self-stretch my-0.5 rounded-full shrink-0"
                            style={{ backgroundColor: accentColor }}
                          />
                          <div className="flex-1 flex flex-col min-w-0">
                            <div className="font-medium truncate">{event.title}</div>
                            {event.location && (
                              <div className="text-[12px] text-text-secondary truncate">
                                {event.location}
                              </div>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              ));
            })()
          )
        )}
      </div>
    </div>
  );

  const selectedEvents = useMemo(() => {
    const key = toISODate(selectedDate);
    return state.indexes.byDate[key] || [];
  }, [state.indexes.byDate, selectedDate]);

  const monthCalendarEvents = useMemo(() => {
    return state.allEvents.map((event) => {
      const colors = getMonthEventColor(event);
      const endForCalendar = (() => {
        if (!event.all_day) return event.end || undefined;
        const base = parseEventDate(event.end) || parseEventDate(event.start);
        if (!base) return event.end || undefined;
        const baseDate = new Date(base.getFullYear(), base.getMonth(), base.getDate());
        return toISODate(addDays(baseDate, 1));
      })();
      return {
        id: String(event.id),
        title: event.title,
        start: event.start,
        end: endForCalendar,
        allDay: !!event.all_day,
        backgroundColor: colors.bg,
        borderColor: colors.border,
        textColor: colors.text,
        extendedProps: { raw: event },
      };
    });
  }, [state.allEvents]);

  useEffect(() => {
    if (view !== "month") {
      setMonthEventPopup(null);
      setDayEventsPopup(null);
    }
  }, [view]);

  useEffect(() => {
    if (!monthEventPopup && !dayEventsPopup) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMonthEventPopup(null);
        setDayEventsPopup(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [dayEventsPopup, monthEventPopup]);

  useLayoutEffect(() => {
    if (!monthEventPopup || !monthPopupRef.current) return;
    if (window.innerWidth < 640) return;
    if (!monthEventPopup.anchorTop && !monthEventPopup.anchorBottom) return;
    const popupGap = 12;
    const popupHeight = monthPopupRef.current.getBoundingClientRect().height;
    const viewportHeight = window.innerHeight;
    const spaceAbove = monthEventPopup.anchorTop;
    const spaceBelow = viewportHeight - monthEventPopup.anchorBottom;
    const fitsBelow = spaceBelow >= popupHeight + popupGap;
    const fitsAbove = spaceAbove >= popupHeight + popupGap;
    const maxTop = Math.max(popupGap, viewportHeight - popupHeight - popupGap);
    const bottomTop = monthEventPopup.anchorBottom + popupGap;
    const topTop = monthEventPopup.anchorTop - popupHeight - popupGap;
    let top = bottomTop;
    if (!fitsBelow && fitsAbove) {
      top = topTop;
    } else if (!fitsBelow && !fitsAbove && spaceAbove > spaceBelow) {
      top = topTop;
    }
    top = Math.min(Math.max(top, popupGap), maxTop);
    if (Math.abs(top - monthEventPopup.top) > 1) {
      setMonthEventPopup((prev) => (prev ? { ...prev, top } : prev));
    }
  }, [monthEventPopup]);

  useLayoutEffect(() => {
    if (!dayEventsPopup || !dayEventsPopupRef.current) return;
    if (window.innerWidth < 640) return;
    if (!dayEventsPopup.anchorTop && !dayEventsPopup.anchorBottom) return;
    const popupGap = 12;
    const popupHeight = dayEventsPopupRef.current.getBoundingClientRect().height;
    const viewportHeight = window.innerHeight;
    const spaceAbove = dayEventsPopup.anchorTop;
    const spaceBelow = viewportHeight - dayEventsPopup.anchorBottom;
    const fitsBelow = spaceBelow >= popupHeight + popupGap;
    const fitsAbove = spaceAbove >= popupHeight + popupGap;
    const maxTop = Math.max(popupGap, viewportHeight - popupHeight - popupGap);
    const bottomTop = dayEventsPopup.anchorBottom + popupGap;
    const topTop = dayEventsPopup.anchorTop - popupHeight - popupGap;
    let top = bottomTop;
    if (!fitsBelow && fitsAbove) {
      top = topTop;
    } else if (!fitsBelow && !fitsAbove && spaceAbove > spaceBelow) {
      top = topTop;
    }
    top = Math.min(Math.max(top, popupGap), maxTop);
    if (Math.abs(top - dayEventsPopup.top) > 1) {
      setDayEventsPopup((prev) => (prev ? { ...prev, top } : prev));
    }
  }, [dayEventsPopup]);

  useEffect(() => {
    if (modalOpen) {
      setMonthEventPopup(null);
      setDayEventsPopup(null);
    }
  }, [modalOpen]);

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

  useEffect(() => {
    if (!searchAdvancedRef.current) return;
    if (searchAdvancedOpen) {
      setSearchAdvancedHeight(searchAdvancedRef.current.scrollHeight);
    } else {
      setSearchAdvancedHeight(0);
    }
  }, [searchAdvancedOpen]);

  useEffect(() => {
    searchResultsCacheRef.current = searchResults;
  }, [searchResults]);

  useEffect(() => {
    if (!searchResultsPanelRef.current) return;
    if (searchResultsOpen) {
      setSearchResultsHeight(searchResultsPanelRef.current.scrollHeight);
    } else {
      setSearchResultsHeight(0);
    }
  }, [searchResultsOpen, searchResults.length]);

  useEffect(() => {
    if (!viewMenuOpen) return;
    const handleClick = (event: MouseEvent) => {
      if (!viewMenuRef.current || viewMenuRef.current.contains(event.target as Node)) return;
      setViewMenuOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => {
      document.removeEventListener("mousedown", handleClick);
    };
  }, [viewMenuOpen]);

  useEffect(() => {
    const monthApi = monthCalendarRef.current?.getApi();
    const miniApi = miniCalendarRef.current?.getApi();
    if (!monthApi && !miniApi) return;
    const rafId = window.requestAnimationFrame(() => {
      monthApi?.updateSize();
      miniApi?.updateSize();
    });
    const timeoutId = window.setTimeout(() => {
      monthApi?.updateSize();
      miniApi?.updateSize();
    }, 220);
    return () => {
      window.cancelAnimationFrame(rafId);
      window.clearTimeout(timeoutId);
    };
  }, [rightPanelOpen, leftPanelOpen]);

  const upcomingEvent = useMemo(() => {
    return state.allEvents
      .map((event) => ({
        event,
        start: parseEventDate(event.start),
      }))
      .filter((item) => item.start && item.start >= nowSnapshot)
      .sort((a, b) => (a.start?.getTime() || 0) - (b.start?.getTime() || 0))
      .map((item) => item.event)[0];
  }, [state.allEvents, nowSnapshot]);

  const upNextLabel = (() => {
    if (!upcomingEvent) return "";
    const start = parseEventDate(upcomingEvent.start);
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

  const handleSwipeStart = (e: React.TouchEvent) => {
    if (!isMobile) return;
    const touch = e.touches[0];
    swipeStartRef.current = {
      x: touch.clientX,
      y: touch.clientY,
      time: Date.now(),
    };
    setIsSwiping(true);
  };

  const handleSwipeMove = (e: React.TouchEvent) => {
    if (!isMobile || !swipeStartRef.current) return;
    const touch = e.touches[0];
    const deltaX = touch.clientX - swipeStartRef.current.x;
    const deltaY = touch.clientY - swipeStartRef.current.y;
    
    // 수직 스크롤이 더 크면 스와이프 취소
    if (Math.abs(deltaY) > Math.abs(deltaX)) {
      setSwipeOffset(0);
      return;
    }
    
    // 스와이프 오프셋 설정 (최대 100px로 제한)
    const offset = Math.max(-100, Math.min(100, deltaX * 0.5));
    setSwipeOffset(offset);
  };

  const handleSwipeEnd = () => {
    if (!isMobile || !swipeStartRef.current) {
      setIsSwiping(false);
      setSwipeOffset(0);
      return;
    }
    
    const threshold = 50; // 50px 이상 스와이프해야 이동
    
    if (Math.abs(swipeOffset) > threshold) {
      if (swipeOffset > 0) {
        handlePrev();
      } else {
        handleNext();
      }
    }
    
    swipeStartRef.current = null;
    setIsSwiping(false);
    setSwipeOffset(0);
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

  const normalizeSearchValue = (value: string) => value.trim().toLowerCase();

  const resolveSearchRange = () => {
    const fallbackStart = toISODate(addMonths(new Date(), -12));
    const fallbackEnd = toISODate(addMonths(new Date(), 12));
    const rangeStart = searchFilters.rangeStart?.trim() || fallbackStart;
    const rangeEnd = searchFilters.rangeEnd?.trim() || fallbackEnd;
    return { rangeStart, rangeEnd };
  };

  const normalizeRange = (start: Date | null, end: Date | null) => {
    if (start && end && start > end) return [end, start] as const;
    return [start, end] as const;
  };

  const focusSearchResult = (event: CalendarEvent) => {
    const targetDate = getEventStartDate(event.start);
    if (!targetDate) return;
    const api = monthCalendarRef.current?.getApi();
    if (api) api.gotoDate(targetDate);
    setSelectedDate(targetDate);
    setCurrentMonth(startOfMonth(targetDate));
  };

  const buildSearchKey = (mode: "basic" | "advanced", rangeStart: string, rangeEnd: string) => {
    const keyword = normalizeSearchValue(searchFilters.title);
    const base = [mode, keyword, rangeStart, rangeEnd];
    if (mode === "advanced") {
      base.push(
        normalizeSearchValue(searchFilters.attendees),
        normalizeSearchValue(searchFilters.location),
        normalizeSearchValue(searchFilters.exclude),
        searchFilters.startDate?.trim() || "",
        searchFilters.endDate?.trim() || ""
      );
    }
    return base.join("|");
  };

  const getSearchResults = async (
    mode: "basic" | "advanced",
    options: { reuse?: boolean; resetIndex?: boolean; focusFirst?: boolean } = {}
  ) => {
    const keyword = normalizeSearchValue(searchFilters.title);
    const attendees = normalizeSearchValue(searchFilters.attendees);
    const location = normalizeSearchValue(searchFilters.location);
    const exclude = normalizeSearchValue(searchFilters.exclude);
    const filterStartValue = searchFilters.startDate?.trim() || "";
    const filterEndValue = searchFilters.endDate?.trim() || "";
    const hasAdvancedCriteria = Boolean(
      keyword || attendees || location || exclude || filterStartValue || filterEndValue
    );

    if (mode === "basic" && !keyword) {
      lastSearchKeyRef.current = null;
      searchResultsCacheRef.current = [];
      setSearchResults([]);
      return [];
    }

    if (mode === "advanced" && !hasAdvancedCriteria) {
      lastSearchKeyRef.current = null;
      searchResultsCacheRef.current = [];
      setSearchResults([]);
      return [];
    }

    const { rangeStart, rangeEnd } = resolveSearchRange();
    const searchKey = buildSearchKey(mode, rangeStart, rangeEnd);
    const isSameKey = lastSearchKeyRef.current === searchKey;
    if (options.reuse && isSameKey && searchResultsCacheRef.current.length > 0) {
      return searchResultsCacheRef.current;
    }

    const allEvents = await actions.ensureRangeLoaded(rangeStart, rangeEnd);
    const [rangeStartDate, rangeEndDate] = normalizeRange(
      parseISODate(rangeStart),
      parseISODate(rangeEnd)
    );
    const [filterStartDate, filterEndDate] = normalizeRange(
      parseISODate(filterStartValue),
      parseISODate(filterEndValue)
    );

    const matches = allEvents.filter((event) => {
      const eventStart = getEventStartDate(event.start);
      if (!eventStart) return false;
      const eventDate = toDateOnly(eventStart);
      if (rangeStartDate && eventDate < rangeStartDate) return false;
      if (rangeEndDate && eventDate > rangeEndDate) return false;

      const haystack = [
        event.title,
        event.description,
        event.location,
        event.attendees?.join(" "),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      if (mode === "basic") {
        return haystack.includes(keyword);
      }

      if (keyword && !haystack.includes(keyword)) return false;
      if (exclude && haystack.includes(exclude)) return false;
      if (attendees) {
        const attendeeText = (event.attendees ?? []).join(" ").toLowerCase();
        if (!attendeeText.includes(attendees)) return false;
      }
      if (location && !(event.location ?? "").toLowerCase().includes(location)) return false;
      if (filterStartDate && eventDate < filterStartDate) return false;
      if (filterEndDate && eventDate > filterEndDate) return false;
      return true;
    });

    lastSearchKeyRef.current = searchKey;
    searchResultsCacheRef.current = matches;
    setSearchResults(matches);

    if (options.focusFirst && matches[0]) focusSearchResult(matches[0]);

    return matches;
  };

  const handleBasicSearch = async () => {
    setSearchAdvancedOpen(false);
    await getSearchResults("basic", { resetIndex: true, focusFirst: false });
    setSearchResultsOpen(true);
  };

  const handleAdvancedSearch = async () => {
    await getSearchResults("advanced", { resetIndex: true });
    setSearchResultsOpen(true);
  };

  const openMonthEventPopup = (event: CalendarEvent, anchor: HTMLElement | null) => {
    setDayEventsPopup(null);
    const rect = anchor?.getBoundingClientRect?.();
    if (!rect) {
      setMonthEventPopup({
        event,
        top: 96,
        left: window.innerWidth / 2,
        align: "center",
        anchorTop: 0,
        anchorBottom: 0,
      });
      return;
    }
    const isSmallScreen = window.innerWidth < 640;
    const popupHeight = 320;
    const popupGap = 12;
    const viewportHeight = window.innerHeight;
    const spaceAbove = rect.top;
    const spaceBelow = viewportHeight - rect.bottom;
    const align = isSmallScreen
      ? "center"
      : rect.left + rect.width / 2 > window.innerWidth / 2
        ? "right"
        : "left";
    const left = align === "center" ? window.innerWidth / 2 : align === "right" ? rect.right : rect.left;
    const maxTop = Math.max(popupGap, viewportHeight - popupHeight - popupGap);
    const bottomTop = rect.bottom + popupGap;
    const topTop = rect.top - popupHeight - popupGap;
    const fitsBelow = spaceBelow >= popupHeight + popupGap;
    const fitsAbove = spaceAbove >= popupHeight + popupGap;
    let top = bottomTop;
    if (!fitsBelow && fitsAbove) {
      top = topTop;
    } else if (!fitsBelow && !fitsAbove && spaceAbove > spaceBelow) {
      top = topTop;
    }
    top = Math.min(Math.max(top, popupGap), maxTop);
    setMonthEventPopup({
      event,
      top,
      left,
      align,
      anchorTop: rect.top,
      anchorBottom: rect.bottom,
    });
  };

  const openDayEventsPopup = (date: Date, events: CalendarEvent[], anchor: HTMLElement | null) => {
    setMonthEventPopup(null);
    const sortedEvents = [...events].sort((a, b) => {
      const aStart = getEventStartDate(a.start)?.getTime() ?? Number.MAX_SAFE_INTEGER;
      const bStart = getEventStartDate(b.start)?.getTime() ?? Number.MAX_SAFE_INTEGER;
      if (aStart !== bStart) return aStart - bStart;
      return a.title.localeCompare(b.title);
    });
    const rect = anchor?.getBoundingClientRect?.();
    if (!rect) {
      setDayEventsPopup({
        date,
        events: sortedEvents,
        top: 96,
        left: window.innerWidth / 2,
        align: "center",
        anchorTop: 0,
        anchorBottom: 0,
      });
      return;
    }
    const isSmallScreen = window.innerWidth < 640;
    const popupHeight = 360;
    const popupGap = 12;
    const viewportHeight = window.innerHeight;
    const spaceAbove = rect.top;
    const spaceBelow = viewportHeight - rect.bottom;
    const align = isSmallScreen
      ? "center"
      : rect.left + rect.width / 2 > window.innerWidth / 2
        ? "right"
        : "left";
    const left = align === "center" ? window.innerWidth / 2 : align === "right" ? rect.right : rect.left;
    const maxTop = Math.max(popupGap, viewportHeight - popupHeight - popupGap);
    const bottomTop = rect.bottom + popupGap;
    const topTop = rect.top - popupHeight - popupGap;
    const fitsBelow = spaceBelow >= popupHeight + popupGap;
    const fitsAbove = spaceAbove >= popupHeight + popupGap;
    let top = bottomTop;
    if (!fitsBelow && fitsAbove) {
      top = topTop;
    } else if (!fitsBelow && !fitsAbove && spaceAbove > spaceBelow) {
      top = topTop;
    }
    top = Math.min(Math.max(top, popupGap), maxTop);
    setDayEventsPopup({
      date,
      events: sortedEvents,
      top,
      left,
      align,
      anchorTop: rect.top,
      anchorBottom: rect.bottom,
    });
  };

  const handleEditFromPopup = (event: CalendarEvent) => {
    const startDate = getEventStartDate(event.start);
    if (startDate) setSelectedDate(startDate);
    setActiveEvent(event);
    setAiDraftEvent(null);
    setAiDraftIndex(null);
    openEventDrawer({ reset: true, showForm: true });
  };

  const monthNumberLabel = `${currentMonth.getMonth() + 1}월`;
  const viewTitle =
    view === "month"
      ? monthNumberLabel
      : view === "week"
        ? `${getWeekOfMonth(currentMonth, weekStart)}주차`
        : formatLongDate(selectedDate);
  const viewBadge = null;
  const headerDateLabel =
    view === "month"
      ? `${currentMonth.getFullYear()}년 ${viewTitle}`
      : viewTitle;
  const weekdayLabels = useMemo(() => {
    const labels = ["일", "월", "화", "수", "목", "금", "토"];
    const startIndex = 0;
    return labels.slice(startIndex).concat(labels.slice(0, startIndex));
  }, []);
  const monthPopupTransform = useMemo(() => {
    if (!monthEventPopup) return "translate(0, 0)";
    if (monthEventPopup.align === "center") return "translate(-50%, 0)";
    if (monthEventPopup.align === "right") return "translate(-100%, 0)";
    return "translate(0, 0)";
  }, [monthEventPopup]);
  const dayEventsPopupTransform = useMemo(() => {
    if (!dayEventsPopup) return "translate(0, 0)";
    if (dayEventsPopup.align === "center") return "translate(-50%, 0)";
    if (dayEventsPopup.align === "right") return "translate(-100%, 0)";
    return "translate(0, 0)";
  }, [dayEventsPopup]);
  const monthPopupEvent = monthEventPopup?.event ?? null;
  const monthPopupAccent = monthPopupEvent ? getPopupAccentColor(monthPopupEvent) : "oklch(var(--text-primary))";
  const monthPopupCardBg = "oklch(var(--bg-surface))";
  const monthPopupDateLabel = monthPopupEvent ? getEventDateLabel(monthPopupEvent) : "";
  const monthPopupTimeLabel = monthPopupEvent ? getEventTimeLabel(monthPopupEvent) : "";
  const monthPopupReminderLabel = monthPopupEvent ? getReminderLabel(monthPopupEvent.reminders) : null;
  const monthPopupLocation = monthPopupEvent?.location?.trim() || "";
  const monthPopupDescription = monthPopupEvent?.description?.trim() || "";
  const dayPopupEvents = dayEventsPopup?.events ?? [];
  const dayPopupDateLabel = dayEventsPopup ? formatLongDate(dayEventsPopup.date) : "";

  return (
    <div
      className={`month-shell bg-bg-canvas text-text-primary ${
        view === "month" ? "overflow-visible" : "overflow-hidden"
      } h-screen flex flex-col ${rightPanelOpen ? "pr-[320px]" : "pr-0"} ${
        leftPanelOpen ? "pl-[320px]" : "pl-0"
      }`}
    >

      <header className={`relative flex flex-col whitespace-nowrap px-3 py-1.5 md:py-3 bg-bg-canvas sticky top-0 z-50 ${
        (leftPanelOpen || rightPanelOpen) ? "max-md:hidden" : ""
      }`}>
        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3 text-text-primary">
            {!leftPanelOpen && (
              <button
                type="button"
                className="hidden md:flex size-9 items-center justify-center rounded-full hover:bg-bg-subtle transition-colors text-text-secondary"
                onClick={() => setLeftPanelOpen(true)}
                aria-label="왼쪽 탭 열기"
                aria-expanded={leftPanelOpen}
                aria-controls="calendar-left-panel"
              >
                <PanelLeftOpen className="size-5" />
              </button>
            )}
            <div className="flex flex-col gap-0.5">
              <div className="flex items-baseline gap-1.5 text-lg font-semibold leading-tight">
                {view === "month" ? (
                  <>
                    <span className="text-text-secondary">{currentMonth.getFullYear()}년</span>
                    <span className="text-text-primary">{viewTitle}</span>
                  </>
                ) : (
                  <span className="text-text-primary">{viewTitle}</span>
                )}
              </div>
              {viewBadge && <span className="text-xs font-semibold text-text-disabled">{viewBadge}</span>}
            </div>
            <div className="flex items-center gap-1 rounded-full px-2 py-1 hidden md:flex">
              <button
                className="size-7 flex items-center justify-center rounded-full hover:bg-bg-subtle text-text-secondary"
                type="button"
                onClick={handlePrev}
                aria-label="이전"
              >
                <ChevronLeft className="size-4" />
              </button>
              <button
                className="px-3 py-1 rounded-full text-[13px] font-medium text-text-secondary hover:bg-bg-subtle"
                type="button"
                onClick={handleToday}
              >
                오늘
              </button>
              <button
                className="size-7 flex items-center justify-center rounded-full hover:bg-bg-subtle text-text-secondary"
                type="button"
                onClick={handleNext}
                aria-label="다음"
              >
                <ChevronRight className="size-4" />
              </button>
            </div>
          </div>
          <div className="relative flex flex-1 items-center justify-end min-h-[36px] gap-3">
              <motion.div
                className="header-actions-layer flex flex-wrap items-center justify-end"
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.2, ease: [0.2, 0.7, 0.2, 1] }}
              >
              {/* 데스크톱 뷰 토글 */}
              <div
                className="relative hidden md:flex items-center bg-bg-surface p-1 rounded-full mr-2 segmented-toggle"
                style={viewToggleStyle}
              >
                <span className="segmented-indicator">
                  <span
                    key={view}
                    className="view-indicator-pulse block h-full w-full rounded-full bg-bg-canvas shadow-sm"
                  />
                </span>
                {viewKeys.map((key) => (
                  <button
                    key={key}
                    className={`relative z-10 flex-1 px-3 py-1 text-sm transition-all ${
                      view === key
                        ? "text-text-primary !font-bold"
                        : "text-text-secondary font-normal hover:text-text-primary"
                    }`}
                    type="button"
                    onClick={() => handleViewChange(key)}
                  >
                    {VIEW_LABELS[key]}
                  </button>
                ))}
              </div>
              {/* 모바일 뷰 선택 버튼 */}
              <div className="relative md:hidden" ref={viewMenuRef}>
                <button
                  className="flex items-center gap-1 px-3 py-1 text-sm font-medium text-text-primary bg-bg-surface rounded-full hover:bg-bg-subtle transition-colors"
                  type="button"
                  onClick={() => setViewMenuOpen((prev) => !prev)}
                  aria-label="뷰 선택"
                  aria-expanded={viewMenuOpen}
                  aria-haspopup="menu"
                >
                  {VIEW_LABELS[view]}
                  <ChevronDown className={`size-3.5 transition-transform ${viewMenuOpen ? "rotate-180" : ""}`} />
                </button>
                {viewMenuOpen && (
                  <div className="absolute left-0 mt-2 z-50 w-max min-w-[60px]" role="menu">
                    <div
                      className="min-w-full overflow-hidden whitespace-nowrap popover-surface popover-animate border border-border-subtle bg-canvas shadow-lg"
                      data-side="bottom"
                      data-align="start"
                    >
                      {viewKeys.map((key) => (
                        <button
                          key={key}
                          type="button"
                          className="flex w-full justify-start rounded-md px-3 py-2 text-[15px] text-left font-medium text-text-primary transition-colors hover:bg-bg-subtle"
                          onClick={() => {
                            handleViewChange(key);
                            setViewMenuOpen(false);
                          }}
                          role="menuitem"
                        >
                          {VIEW_LABELS[key]}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              <div className="flex gap-1">
                <button
                  className="flex items-center justify-center rounded-full size-9 hover:bg-bg-subtle transition-colors text-text-secondary"
                  type="button"
                  onClick={() => {
                    setActiveEvent(null);
                    setAiDraftEvent(null);
                    setAiDraftIndex(null);
                    openEventDrawer({ reset: true, showForm: true });
                  }}
                  aria-label="직접 추가"
                >
                  <Plus className="size-5" />
                </button>
                <button
                  className="flex items-center justify-center rounded-full size-9 hover:bg-bg-subtle transition-colors text-text-secondary"
                  type="button"
                  onClick={() => openAiDrawer()}
                  aria-label="알아서 추가"
                >
                  <Sparkles className="size-5" />
                </button>
                <button
                  className="flex items-center justify-center rounded-full size-9 hover:bg-bg-subtle transition-colors text-text-secondary"
                  type="button"
                  onClick={() => {
                    setSearchOpen(true);
                    setModalOpen(false);
                    if (!isMobile) {
                      setRightPanelOpen(true);
                    }
                    ai.close();
                  }}
                  aria-label="검색"
                  aria-expanded={searchOpen}
                >
                  <Search className="size-5" />
                </button>
              </div>
              
              <div className="hidden md:flex">
                <CalendarHeaderActions status={state.authStatus} />
              </div>
              <div ref={mobileMenuRef} className="relative md:hidden">
                <button
                  className="flex items-center justify-center rounded-full size-9 hover:bg-bg-subtle transition-colors text-text-secondary"
                  type="button"
                  onClick={() => setMobileMenuOpen((prev) => !prev)}
                  aria-label="사용자 메뉴"
                  aria-haspopup="menu"
                  aria-expanded={mobileMenuOpen}
                >
                  <MoreVertical className="size-5" />
                </button>
                {mobileMenuOpen && (
                  <div className="absolute right-0 mt-2 w-52 rounded-xl border border-border-subtle bg-bg-surface shadow-lg p-3 flex flex-col gap-2 z-50">
                    <CalendarHeaderActions
                      status={state.authStatus}
                      className="flex flex-col items-stretch gap-2"
                      buttonClassName="w-full text-left font-medium text-text-primary"
                    />
                  </div>
                )}
              </div>
              {!rightPanelOpen && (
                <button
                  type="button"
                  className="hidden md:flex size-9 items-center justify-center rounded-full hover:bg-bg-subtle transition-colors text-text-secondary"
                  onClick={() => openEventDrawer()}
                  aria-label="오른쪽 탭 열기"
                  aria-expanded={rightPanelOpen}
                  aria-controls="calendar-right-panel"
                >
                  <PanelRightOpen className="size-5" />
                </button>
              )}
              </motion.div>
          </div>
        </div>
      </header>

      <main
        className={`flex-1 flex flex-col max-w-[1600px] mx-auto w-full gap-6 min-h-0 ${
          view === "month" ? "overflow-visible" : "overflow-hidden"
        }`}
      >
        <div className="flex flex-col lg:flex-row gap-6 flex-1 min-h-0">
          <div className="order-1 lg:order-2 flex-1 flex flex-col gap-4 min-h-0 min-w-0">
            <section
              ref={calendarSwipeRef}
              className={`flex-1 bg-bg-canvas shadow-sm flex flex-col ${
                view === "month" ? "overflow-visible" : "overflow-hidden"
              } min-h-[300px] touch-pan-y`}
              onTouchStart={handleSwipeStart}
              onTouchMove={handleSwipeMove}
              onTouchEnd={handleSwipeEnd}
              onTouchCancel={handleSwipeEnd}
              style={{
                transition: isSwiping ? 'none' : 'transform 0.3s ease-out',
                transform: `translateX(${swipeOffset}px)`,
              }}
            >
            <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
              <div className="flex-1 min-h-0 overflow-visible bg-bg-canvas flex flex-col relative z-10">
                {view === "month" && (
                  <div className="grid grid-cols-7 bg-bg-canvas">
                    {weekdayLabels.map((label) => (
                      <div
                        key={label}
                        className="py-2 text-center text-[12px] font-semibold uppercase tracking-[0.08em] text-text-secondary"
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
                    height="100%"
                    expandRows={view === "month"}
                    fixedWeekCount={false}
                    firstDay={0}
                    locale="ko"
                    headerToolbar={false}
                    allDayText="종일 일정"
                    displayEventTime={view !== "month"}
                    eventTimeFormat={{ hour: "numeric", minute: "2-digit", hour12: true }}
                    eventDisplay="block"
                    eventMinWidth={0}
                    slotEventOverlap={false}
                    dayMaxEventRows={view === "month" ? true : 6}
                    scrollTimeReset={false}
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
                        openEventDrawer({ reset: true, showForm: true });
                        setMonthEventPopup(null);
                        setDayEventsPopup(null);
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
                              className={`month-event-content${continued ? " month-event-continued" : ""} cursor-pointer`}
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
                          <div className="timegrid-event-content cursor-pointer" style={{ color: textColor }}>
                            <span className="fc-event-title">{arg.event.title}</span>
                            {timeText && <span className="fc-event-time">{timeText}</span>}
                          </div>
                        );
                      }
                      return (
                        <div
                          className={`month-event-content${continued ? " month-event-continued" : ""} cursor-pointer`}
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
                      if (skipDateClickRef.current) {
                        skipDateClickRef.current = false;
                        return;
                      }
                      setSelectedDate(info.date);
                      setMonthEventPopup(null);
                      setDayEventsPopup(null);
                    }}
                    eventClick={(info) => {
                      const target = state.allEvents.find((ev) => String(ev.id) === String(info.event.id));
                      if (info.event.start) setSelectedDate(info.event.start);
                      if (target) {
                        setActiveEvent(target);
                        setAiDraftEvent(null);
                        setAiDraftIndex(null);
                        openMonthEventPopup(target, info.el);
                      }
                    }}
                    moreLinkText={(num) => `${num}개 더보기`}
                    moreLinkClick={(arg) => {
                      if (view !== "month") return "popover";
                      const rawTarget = (arg.jsEvent.currentTarget || arg.jsEvent.target) as HTMLElement | null;
                      const anchor = rawTarget?.closest?.(".fc-more-link") ?? null;
                      const hiddenEvents = arg.hiddenSegs
                        .map((segment) => {
                          const raw = (segment.event.extendedProps as { raw?: CalendarEvent } | undefined)?.raw;
                          if (raw) return raw;
                          const fallback = state.allEvents.find(
                            (event) => String(event.id) === String(segment.event.id)
                          );
                          if (fallback) return fallback;
                          const start = segment.event.start ? toISODateTime(segment.event.start) : "";
                          const end = segment.event.end ? toISODateTime(segment.event.end) : null;
                          if (!start) return null;
                          return {
                            id: segment.event.id,
                            title: segment.event.title,
                            start,
                            end,
                            all_day: segment.event.allDay,
                            source: "local",
                          } as CalendarEvent;
                        })
                        .filter((event): event is CalendarEvent => Boolean(event));
                      if (hiddenEvents.length === 0) return "popover";
                      arg.jsEvent.preventDefault();
                      arg.jsEvent.stopPropagation();
                      skipDateClickRef.current = true;
                      setSelectedDate(arg.date);
                      setMonthEventPopup(null);
                      openDayEventsPopup(arg.date, hiddenEvents, anchor as HTMLElement | null);
                      return {} as unknown as "popover";
                    }}
                  />
                </div>
              </div>
              {state.loading && <p className="text-xs text-text-tertiary mt-3">일정 불러오는 중...</p>}
              {state.error && <p className="text-xs text-token-error mt-3">{state.error}</p>}
            </div>
            </section>
          </div>
        </div>
      </main>
      {dayEventsPopup && (
        <div
          className="fixed z-[80] w-[min(360px,calc(100vw-32px))] rounded-3xl border border-border-subtle bg-bg-surface p-5 text-text-primary shadow-[0_20px_45px_rgba(20,16,12,0.2)]"
          style={{
            top: dayEventsPopup.top,
            left: dayEventsPopup.left,
            transform: dayEventsPopupTransform,
          }}
          role="dialog"
          aria-label={`${dayPopupDateLabel} 일정 목록`}
          ref={dayEventsPopupRef}
        >
          <div className="flex items-center justify-end">
            <button
              type="button"
              onClick={() => setDayEventsPopup(null)}
              className="rounded-full border border-border-subtle p-2 text-text-secondary hover:bg-bg-subtle"
              aria-label="팝업 닫기"
            >
              <X className="size-4" />
            </button>
          </div>
          <div className="mt-4 max-h-[50vh] space-y-2 overflow-y-auto pr-1">
            {dayPopupEvents.length === 0 ? (
              <p className="text-sm text-text-secondary">등록된 일정이 없습니다.</p>
            ) : (
              dayPopupEvents.map((event) => {
                const timeLabel = getEventTimeLabel(event);
                const location = event.location?.trim() || "";
                const colors = getMonthEventColor(event);
                return (
                  <button
                    key={`day-popup-${event.id}`}
                    type="button"
                    className="flex w-full flex-col gap-1 rounded-lg border border-border-subtle px-3 py-2 text-left text-text-primary transition"
                    onClick={(clickEvent) => {
                      const startDate = getEventStartDate(event.start);
                      if (startDate) setSelectedDate(startDate);
                      setActiveEvent(event);
                      setAiDraftEvent(null);
                      setAiDraftIndex(null);
                      setDayEventsPopup(null);
                      openMonthEventPopup(event, clickEvent.currentTarget);
                    }}
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className="mt-0.5 size-2.5 rounded-full"
                        style={{ backgroundColor: colors.border }}
                        aria-hidden="true"
                      />
                      <span className="text-sm font-semibold">{event.title}</span>
                    </div>
                    <div className="text-xs text-text-secondary">
                      {timeLabel}
                      {location ? ` · ${location}` : ""}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}
      {monthEventPopup && monthPopupEvent && (
        <>
          <div
            className="fixed z-[80] w-[min(360px,calc(100vw-32px))] rounded-3xl border border-border-subtle bg-bg-surface p-5 text-text-primary shadow-[0_20px_45px_rgba(20,16,12,0.2)]"
            style={{
              top: monthEventPopup.top,
              left: monthEventPopup.left,
              transform: monthPopupTransform,
            }}
            role="dialog"
            aria-label={`${monthPopupEvent.title} 일정 상세`}
            ref={monthPopupRef}
          >
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-lg font-semibold text-text-primary">
                  {monthPopupEvent.title}
                </h3>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleEditFromPopup(monthPopupEvent)}
                  className="rounded-full border border-border-subtle p-2 text-text-secondary hover:bg-bg-surface"
                  aria-label="일정 수정"
                >
                  <Pencil className="size-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setMonthEventPopup(null)}
                  className="rounded-full border border-border-subtle p-2 text-text-secondary hover:bg-bg-surface"
                  aria-label="팝업 닫기"
                >
                  <X className="size-4" />
                </button>
              </div>
            </div>

            <div className="mt-4 space-y-3 text-sm text-text-secondary">
              <div
                className="flex items-start gap-3 rounded-xl px-3 py-2"
                style={{ backgroundColor: monthPopupCardBg }}
              >
                <Clock className="mt-0.5 size-4 text-text-primary" />
                <div>
                  <p className="text-xs uppercase tracking-[0.2em] text-text-tertiary">
                    일정
                  </p>
                  <p className="font-semibold text-text-primary">
                    {monthPopupDateLabel}
                  </p>
                  <p>{monthPopupTimeLabel}</p>
                </div>
              </div>
              {monthPopupLocation && (
                <div
                  className="flex items-start gap-3 rounded-xl px-3 py-2"
                  style={{ backgroundColor: monthPopupCardBg }}
                >
                  <MapPin className="mt-0.5 size-4 text-text-primary dark:text-slate-100" />
                  <div>
                    <p className="text-xs uppercase tracking-[0.2em] text-text-disabled dark:text-slate-400">
                      장소
                    </p>
                    <p className="font-semibold text-text-primary dark:text-white">
                      {monthPopupLocation}
                    </p>
                  </div>
                </div>
              )}
              {monthPopupReminderLabel && (
                <div
                  className="flex items-start gap-3 rounded-xl px-3 py-2"
                  style={{ backgroundColor: monthPopupCardBg }}
                >
                  <Bell className="mt-0.5 size-4 text-text-primary dark:text-slate-100" />
                  <div>
                    <p className="text-xs uppercase tracking-[0.2em] text-text-disabled dark:text-slate-400">
                      알림
                    </p>
                    <p className="font-semibold text-text-primary dark:text-white">
                      {monthPopupReminderLabel}
                    </p>
                  </div>
                </div>
              )}
            </div>

            {monthPopupDescription && (
              <div
                className="mt-4 rounded-xl px-4 py-3 text-sm text-text-secondary dark:text-slate-200"
                style={{ backgroundColor: monthPopupCardBg }}
              >
                <p className="text-xs uppercase tracking-[0.2em] text-text-disabled dark:text-slate-400">
                  메모
                </p>
                <p className="mt-2 text-sm text-text-primary dark:text-white">
                  {monthPopupDescription}
                </p>
              </div>
            )}
          </div>
        </>
      )}
      <div
        className={`fixed left-0 top-0 z-[60] md:z-40 flex h-full items-center ${
          leftPanelOpen ? "pointer-events-auto" : "pointer-events-none"
        }`}
      >
        <div
          id="calendar-left-panel"
          className={`h-full w-full md:w-[320px] border-r border-border-subtle bg-bg-surface ${
            leftPanelOpen ? "translate-x-0 pointer-events-auto" : "-translate-x-full pointer-events-none"
          }`}
        >
          <div className="flex h-full flex-col gap-4 px-3 py-3 text-text-primary">
            {leftPanelOpen && (
              <div className="flex justify-start">
                <button
                  type="button"
                  className="flex size-9 items-center justify-center rounded-full hover:bg-bg-subtle transition-colors text-text-secondary"
                  onClick={() => setLeftPanelOpen(false)}
                  aria-label="왼쪽 탭 닫기"
                >
                  <PanelLeftClose className="size-5" />
                </button>
              </div>
            )}
            <div
              className={`mini-calendar bg-bg-surface ${
                view === "day" ? "mini-calendar-day" : ""
              }`}
            >
              <FullCalendar
                key={view}
                ref={miniCalendarRef}
                plugins={[dayGridPlugin, interactionPlugin]}
                initialView="dayGridMonth"
                initialDate={selectedDate}
                height="auto"
                fixedWeekCount={false}
                firstDay={0}
                locale="ko"
                headerToolbar={{ left: "title", center: "", right: "prev,next" }}
                titleFormat={{ year: "numeric", month: "long" }}
                events={[]}
                dayCellContent={(arg) => {
                  const numberText = arg.dayNumberText.replace(/[^\d]/g, "");
                  return <span>{numberText}</span>;
                }}
                dayCellDidMount={(info) => {
                  if (view !== "week") return;
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
                dayCellClassNames={(arg) => {
                  const day = toDateOnly(arg.date);
                  const weekStartDay = toDateOnly(startOfWeek(day));
                  const weekEndDay = addDays(weekStartDay, 6);
                  const classes: string[] = [];
                  if (view === "week" && day >= selectedWeekStartDay && day <= selectedWeekEndDay) {
                    classes.push("mini-week-selected");
                  }
                  if (view === "week" && isSameDay(day, weekStartDay)) classes.push("mini-week-start");
                  if (view === "week" && isSameDay(day, weekEndDay)) classes.push("mini-week-end");
                  if (view === "day" && isSameDay(day, selectedDate)) {
                    classes.push("fc-day-selected");
                  }
                  return classes;
                }}
                dateClick={(info) => {
                  const isWeekView = view === "week";
                  const selectedAnchor = info.date;
                  setSelectedDate(selectedAnchor);
                  setCurrentMonth(startOfMonth(info.date));
                  const api = monthCalendarRef.current?.getApi();
                  if (api) {
                    if (view === "month") {
                      // 월 보기에서는 날짜만 이동하고 뷰는 유지
                      api.gotoDate(info.date);
                    } else if (view === "week" || view === "day") {
                      api.gotoDate(info.date);
                    }
                  }
                }}
              />
            </div>

            {/* Today's Summary */}
            <div className="flex-1 overflow-y-auto px-3 pb-3 space-y-3">
              {/* Ongoing Events */}
              {ongoingEvents.length > 0 && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-xs font-semibold text-text-primary">
                    <div className="size-2 rounded-full bg-green-500"></div>
                    <span>지금 진행중 ({ongoingEvents.length})</span>
                  </div>
                  <div className="space-y-1">
                    {ongoingEvents.map(event => {
                      const startDate = parseEventDate(event.start);
                      const endDate = parseEventDate(event.end);
                      return (
                        <div key={event.id} className="flex items-start gap-2 rounded-lg bg-green-50 dark:bg-green-950/30 px-3 py-2 text-xs">
                          <Clock className="size-3 mt-0.5 shrink-0 text-green-600 dark:text-green-400" />
                          <div className="flex-1 min-w-0">
                            <div className="font-medium text-text-primary truncate">{event.title}</div>
                            <div className="text-text-secondary">
                              {startDate && formatTimeRange(startDate, endDate)}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* Upcoming Events */}
              {upcomingEvents.length > 0 && (
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-xs font-semibold text-text-primary">
                    <div className="size-2 rounded-full bg-orange-500"></div>
                    <span>임박한 일정 ({upcomingEvents.length})</span>
                  </div>
                  <div className="space-y-1">
                    {upcomingEvents.map(event => {
                      const startDate = parseEventDate(event.start);
                      return (
                        <div key={event.id} className="flex items-start gap-2 rounded-lg bg-orange-50 dark:bg-orange-950/30 px-3 py-2 text-xs">
                          <Bell className="size-3 mt-0.5 shrink-0 text-orange-600 dark:text-orange-400" />
                          <div className="flex-1 min-w-0">
                            <div className="font-medium text-text-primary truncate">{event.title}</div>
                            <div className="text-text-secondary">
                              {startDate && formatTime(startDate)}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              {/* All Today's Events */}
              <div className="space-y-2">
                <div className="flex items-center gap-2 text-xs font-semibold text-text-primary">
                  <CalendarIcon className="size-3" />
                  <span>오늘의 일정 ({todayEvents.length})</span>
                </div>
                {todayEvents.length > 0 ? (
                  <div className="space-y-1">
                    {todayEvents.slice(0, 5).map(event => {
                      const isOngoing = ongoingEvents.some(e => e.id === event.id);
                      const isUpcoming = upcomingEvents.some(e => e.id === event.id);
                      const startDate = parseEventDate(event.start);
                      return (
                        <div 
                          key={event.id} 
                          className={`flex items-start gap-2 rounded-lg px-3 py-2 text-xs ${
                            isOngoing ? 'bg-green-50/50 dark:bg-green-950/20' : 
                            isUpcoming ? 'bg-orange-50/50 dark:bg-orange-950/20' : 
                            'bg-subtle'
                          }`}
                        >
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="text-text-secondary shrink-0">
                                {startDate && formatTime(startDate)}
                              </span>
                              <span className="font-medium text-text-primary truncate">{event.title}</span>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                    {todayEvents.length > 5 && (
                      <div className="text-xs text-text-secondary text-center py-1">
                        +{todayEvents.length - 5}개 더보기
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-xs text-text-disabled text-center py-4">
                    오늘 예정된 일정이 없습니다
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
      <div
        className={`fixed right-0 top-0 z-[60] md:z-40 flex h-full items-center ${
          rightPanelOpen ? "pointer-events-auto" : "pointer-events-none"
        }`}
      >
        <div
          id="calendar-right-panel"
          className={`h-full w-full md:w-[320px] border-l border-border-subtle bg-bg-surface flex flex-col ${
            rightPanelOpen ? "translate-x-0 pointer-events-auto" : "translate-x-full pointer-events-none"
          }`}
        >
          {rightPanelOpen && (
            <div className="flex items-center justify-between px-3 pt-3 bg-bg-surface">
              {ai.open && !searchOpen ? (
                <div className="flex items-center gap-4">
                  <button
                    className="size-9 rounded-full flex items-center justify-center bg-subtle text-text-secondary hover:text-token-primary"
                    type="button"
                    onClick={ai.resetConversation}
                    aria-label="대화 초기화"
                  >
                    <RotateCcw className="size-4" />
                  </button>
                </div>
              ) : (
                <span />
              )}
              <button
                type="button"
                className="flex size-9 items-center justify-center rounded-full hover:bg-subtle transition-colors text-text-secondary"
                onClick={closeEventDrawer}
                aria-label="오른쪽 탭 닫기"
              >
                <PanelRightClose className="size-5" />
              </button>
            </div>
          )}
          <div className="flex-1 overflow-hidden min-h-0">
            {searchOpen ? (
              <div className="flex h-full flex-col overflow-hidden min-h-0">
                <div className="px-3 py-2">
                  <div className="flex items-center gap-2 rounded-xl border border-border-subtle bg-bg-canvas px-3 py-1.5 shadow-sm">
                    <Search className="size-4 text-text-secondary" />
                    <input
                      className="flex-1 bg-transparent text-[13px] text-text-primary focus:outline-none placeholder:text-text-disabled"
                      placeholder="일정 키워드 입력"
                      value={searchFilters.title}
                      onChange={(event) =>
                        setSearchFilters((prev) => ({ ...prev, title: event.target.value }))
                      }
                      onKeyDown={(event) => {
                        if (event.key === "Enter") {
                          event.preventDefault();
                          void handleBasicSearch();
                        }
                      }}
                      aria-label="기본 검색어"
                    />
                    <button
                      type="button"
                      className={`flex size-7 items-center justify-center rounded-lg transition-colors hover:bg-bg-subtle ${
                        searchAdvancedOpen ? "text-token-primary bg-bg-subtle" : "text-text-secondary"
                      }`}
                      onClick={() => setSearchAdvancedOpen((prev) => !prev)}
                      aria-label="고급 검색 토글"
                      aria-expanded={searchAdvancedOpen}
                    >
                      <ChevronDown
                        className={`size-4 transition-transform ${searchAdvancedOpen ? "rotate-180" : ""}`}
                      />
                    </button>
                  </div>
                </div>

                {searchAdvancedOpen && (
                  <div className="px-3 pb-4 border-b border-border-subtle animate-in slide-in-from-top-1 duration-200">
                    <div className="space-y-3 pt-1">
                      <div className="space-y-1">
                        <div className="text-[11px] font-medium text-text-secondary px-1">참석자</div>
                        <input
                          className="w-full rounded-lg border border-border-subtle bg-bg-canvas px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-1 focus:ring-token-primary/20"
                          type="text"
                          placeholder="참석자, 주최자 입력"
                          value={searchFilters.attendees}
                          onChange={(event) =>
                            setSearchFilters((prev) => ({ ...prev, attendees: event.target.value }))
                          }
                          onKeyDown={onSearchKeyDown}
                        />
                      </div>
                      <div className="space-y-1">
                        <div className="text-[11px] font-medium text-text-secondary px-1">장소</div>
                        <input
                          className="w-full rounded-lg border border-border-subtle bg-bg-canvas px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-1 focus:ring-token-primary/20"
                          type="text"
                          placeholder="위치 또는 회의실"
                          value={searchFilters.location}
                          onChange={(event) =>
                            setSearchFilters((prev) => ({ ...prev, location: event.target.value }))
                          }
                          onKeyDown={onSearchKeyDown}
                        />
                      </div>
                      <div className="space-y-1">
                        <div className="text-[11px] font-medium text-text-secondary px-1">제외할 검색어</div>
                        <input
                          className="w-full rounded-lg border border-border-subtle bg-bg-canvas px-3 py-1.5 text-xs text-text-primary focus:outline-none focus:ring-1 focus:ring-token-primary/20"
                          type="text"
                          placeholder="제외할 키워드"
                          value={searchFilters.exclude}
                          onChange={(event) =>
                            setSearchFilters((prev) => ({ ...prev, exclude: event.target.value }))
                          }
                          onKeyDown={onSearchKeyDown}
                        />
                      </div>
                      <div className="grid grid-cols-2 gap-2">
                        <div className="space-y-1">
                          <div className="text-[11px] font-medium text-text-secondary px-1">시작일</div>
                          <DatePopover
                            className="w-full"
                            value={searchFilters.startDate}
                            onChange={(val) =>
                              setSearchFilters((prev) => ({ ...prev, startDate: val }))
                            }
                            label="시작일"
                            icon={<CalendarIcon className="size-3.5" />}
                            placeholder="시작일"
                          />
                        </div>
                        <div className="space-y-1">
                          <div className="text-[11px] font-medium text-text-secondary px-1">종료일</div>
                          <DatePopover
                            className="w-full"
                            value={searchFilters.endDate}
                            onChange={(val) =>
                              setSearchFilters((prev) => ({ ...prev, endDate: val }))
                            }
                            label="종료일"
                            icon={<CalendarIcon className="size-3.5" />}
                            placeholder="종료일"
                          />
                        </div>
                      </div>
                      <div className="flex justify-end items-center pt-2">
                        <button
                          type="button"
                          className="text-[11px] text-text-tertiary hover:text-text-secondary transition-colors inline-flex items-center gap-1"
                          onClick={() => {
                            setSearchFilters((prev) => ({
                              ...prev,
                              attendees: "",
                              location: "",
                              exclude: "",
                              startDate: "",
                              endDate: "",
                            }));
                          }}
                        >
                          <Undo2 className="size-3" /> 필터 초기화
                        </button>
                      </div>
                    </div>
                  </div>
                )}

                <div className="flex-1 overflow-y-auto px-3 py-2 pb-10">
                  {searchResultsOpen && (
                    searchResults.length === 0 ? (
                      <div className="py-10 text-center text-xs text-text-tertiary">
                        검색 결과가 없습니다.
                      </div>
                    ) : (
                      (() => {
                        const groups: Record<string, typeof searchResults> = {};
                        searchResults.forEach((event) => {
                          const date = getEventStartDate(event.start);
                          const key = date ? formatSearchDate(date) : "날짜 미지정";
                          if (!groups[key]) groups[key] = [];
                          groups[key].push(event);
                        });

                        return Object.entries(groups).map(([dateLabel, events], gIndex) => (
                          <div key={`group-${dateLabel}-${gIndex}`} className="mb-4">
                            <div className="px-1 mb-1 text-[13px] font-medium text-text-primary border-b border-border-subtle pb-1">
                              {dateLabel}
                            </div>
                            <div className="space-y-0.5">
                              {events.map((event, index) => {
                                const colorId = (event as CalendarEvent).color_id || "default";
                                const colorData =
                                  MONTH_EVENT_COLOR_MAP[colorId] || DEFAULT_MONTH_EVENT_COLOR;
                                const accentColor = colorData.border;
                                return (
                                  <div
                                    key={`search-result-drawer-${event.id}-${index}`}
                                    className="group flex items-center gap-2 rounded-md px-2 py-1.5 text-[14px] text-text-primary hover:bg-bg-subtle transition-colors cursor-pointer"
                                    onClick={() => focusSearchResult(event)}
                                  >
                                    <div
                                      className="w-[3px] self-stretch my-0.5 rounded-full shrink-0"
                                      style={{ backgroundColor: accentColor }}
                                    />
                                    <div className="flex-1 flex flex-col min-w-0">
                                      <div className="font-medium truncate">{event.title}</div>
                                      {event.location && (
                                        <div className="text-[12px] text-text-secondary truncate">
                                          {event.location}
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        ));
                      })()
                    )
                  )}
                </div>
              </div>
            ) : ai.open ? (
              <AiAssistantModal
                assistant={ai}
                variant="drawer"
                showHeaderControls={false}
                onEditAddItem={(item, index) => {
                  const draft = buildAiDraftEvent(item, selectedDate);
                  setAiDraftEvent(draft);
                  setAiDraftIndex(index);
                  setActiveEvent(null);
                  ai.close();
                  openEventDrawer({ reset: true, showForm: true });
                }}
              />
            ) : (
              <EventModal
                open={modalOpen}
                variant="drawer"
                showCloseButton={false}
                resetKey={eventFormResetKey}
                event={aiDraftEvent ?? activeEvent}
                forceCreate={Boolean(aiDraftEvent)}
                defaultDate={selectedDate}
                onClose={closeEventDrawer}
                onCancel={closeEventDrawer}
                onCreate={async (payload) => {
                  closeEventDrawer();
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
                  closeEventDrawer();
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
                onUpdate={async (event, payload) => {
                  closeEventDrawer();
                  await actions.update(event, payload);
                }}
                onUpdateRecurring={async (event, payload) => {
                  closeEventDrawer();
                  await actions.updateRecurring(event, payload);
                }}
                onDeleteOccurrence={async (event) => {
                  closeEventDrawer();
                  await actions.deleteRecurringOccurrence(event);
                }}
                onDelete={async (event) => {
                  closeEventDrawer();
                  await actions.remove(event);
                }}
              />
            )}
          </div>
        </div>
      </div>
      
      {/* 모바일 전체화면 모달 */}
      {isMobile && searchOpen && (
        <div className="fixed inset-0 z-[999] bg-bg-surface">
          {searchUI}
        </div>
      )}
      {isMobile && ai.open && (
        <AiAssistantModal
          assistant={ai}
          variant="modal"
          showHeaderControls={true}
          onEditAddItem={(item, index) => {
            const draft = buildAiDraftEvent(item, selectedDate);
            setAiDraftEvent(draft);
            setAiDraftIndex(index);
            setActiveEvent(null);
            ai.close();
            openEventDrawer({ reset: true, showForm: true });
          }}
        />
      )}
      {isMobile && modalOpen && (
        <EventModal
          open={modalOpen}
          variant="modal"
          showCloseButton={true}
          resetKey={eventFormResetKey}
          event={aiDraftEvent ?? activeEvent}
          forceCreate={Boolean(aiDraftEvent)}
          defaultDate={selectedDate}
          onClose={closeEventDrawer}
          onCancel={closeEventDrawer}
          onCreate={async (payload) => {
            closeEventDrawer();
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
            closeEventDrawer();
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
          onUpdate={async (event, payload) => {
            closeEventDrawer();
            await actions.update(event, payload);
          }}
          onUpdateRecurring={async (event, payload) => {
            closeEventDrawer();
            await actions.updateRecurring(event, payload);
          }}
          onDeleteOccurrence={async (event) => {
            closeEventDrawer();
            await actions.deleteRecurringOccurrence(event);
          }}
          onDelete={async (event) => {
            closeEventDrawer();
            await actions.remove(event);
          }}
        />
      )}
    </div>
  );
}

export default function CalendarPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-bg-canvas" />}>
      <ThemeTokenProvider />
      <CalendarPageInner />
    </Suspense>
  );
}
