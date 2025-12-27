"use client";

import { useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import EventModal from "./components/EventModal";
import AiAssistantModal from "./components/AiAssistantModal";
import CalendarHeaderActions from "./components/CalendarHeaderActions";
import RecentEventsModal from "./components/RecentEventsModal";
import SearchEventsModal from "./components/SearchEventsModal";
import {
  addDays,
  addMonths,
  endOfMonth,
  endOfWeek,
  formatHourLabel,
  formatLongDate,
  formatMonthYear,
  formatTime,
  formatTimeRange,
  isSameDay,
  parseISODateTime,
  startOfMonth,
  startOfWeek,
  toISODate,
} from "./lib/date";
import type { CalendarEvent, RecurringEventPayload } from "./lib/types";
import { useCalendarData } from "./lib/use-calendar-data";
import { useAiAssistant } from "./lib/use-ai-assistant";
import { useUndoStack } from "./lib/use-undo";
import {
  ChevronLeft,
  ChevronRight,
  Clock,
  History,
  Plus,
  Search,
  Sparkles,
  Undo2,
} from "lucide-react";

type ViewMode = "month" | "week" | "day";

const VIEW_LABELS: Record<ViewMode, string> = {
  month: "월",
  week: "주",
  day: "일",
};

const GRID_START_HOUR = 9;
const GRID_END_HOUR = 17;
const HOUR_HEIGHT_PX = 112;
const GRID_HOURS = Array.from(
  { length: GRID_END_HOUR - GRID_START_HOUR },
  (_, index) => GRID_START_HOUR + index
);

const COLOR_STYLE_MAP: Record<string, string> = {
  "1": "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border-blue-500",
  "2": "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 border-emerald-500",
  "3": "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-300 border-violet-500",
  "4": "bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300 border-rose-500",
  "5": "bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-300 border-orange-500",
  "6": "bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 border-teal-500",
  "7": "bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300 border-indigo-500",
  "8": "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 border-amber-500",
  "9": "bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-300 border-pink-500",
  "10": "bg-slate-100 text-slate-700 dark:bg-slate-700/50 dark:text-slate-300 border-slate-500",
  "11": "bg-cyan-100 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-300 border-cyan-500",
};

const DEFAULT_EVENT_STYLE =
  "bg-slate-100 text-slate-700 dark:bg-slate-700/50 dark:text-slate-300 border-slate-500";

const getEventStyle = (event: CalendarEvent) => {
  const colorKey = event.color_id ? String(event.color_id) : "";
  if (colorKey && colorKey !== "default") {
    const mapped = COLOR_STYLE_MAP[colorKey];
    if (mapped) return mapped;
  }
  return DEFAULT_EVENT_STYLE;
};

const getEventBlockStyle = (event: CalendarEvent, hour: number) => {
  const start = parseISODateTime(event.start);
  if (!start) return null;
  const end = parseISODateTime(event.end || "") || new Date(start.getTime() + 60 * 60000);
  let diffMinutes = Math.round((end.getTime() - start.getTime()) / 60000);
  if (diffMinutes <= 0) diffMinutes = 60;

  const startMinutes = start.getHours() * 60 + start.getMinutes();
  const gridStartMinutes = GRID_START_HOUR * 60;
  const gridEndMinutes = GRID_END_HOUR * 60;
  if (startMinutes < hour * 60 || startMinutes >= (hour + 1) * 60) return null;
  if (startMinutes >= gridEndMinutes) return null;

  const maxMinutes = Math.max(15, Math.min(diffMinutes, gridEndMinutes - startMinutes));
  const topPx = ((startMinutes - hour * 60) / 60) * HOUR_HEIGHT_PX;
  const heightPx = (maxMinutes / 60) * HOUR_HEIGHT_PX;
  return { topPx, heightPx };
};

const eventCoversDate = (event: CalendarEvent, date: Date) => {
  const start = parseISODateTime(event.start);
  if (!start) return false;
  const end = parseISODateTime(event.end || "") || start;
  const startDate = new Date(start.getFullYear(), start.getMonth(), start.getDate());
  const endDate = new Date(end.getFullYear(), end.getMonth(), end.getDate());
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  return target >= startDate && target <= endDate;
};

const getEventDateRange = (event: CalendarEvent) => {
  const start = parseISODateTime(event.start);
  if (!start) return null;
  const end = parseISODateTime(event.end || "") || start;
  const startDate = new Date(start.getFullYear(), start.getMonth(), start.getDate());
  const endDate = new Date(end.getFullYear(), end.getMonth(), end.getDate());
  if (endDate < startDate) return { startDate, endDate: startDate };
  return { startDate, endDate };
};

const isMultiDayEvent = (event: CalendarEvent) => {
  const range = getEventDateRange(event);
  if (!range) return false;
  return range.endDate.getTime() > range.startDate.getTime();
};

const getWeekNumber = (date: Date) => {
  const temp = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
  const dayNum = temp.getUTCDay() || 7;
  temp.setUTCDate(temp.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(temp.getUTCFullYear(), 0, 1));
  return Math.ceil((((temp.getTime() - yearStart.getTime()) / 86400000) + 1) / 7);
};

export default function CalendarPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const today = new Date();
  const [currentMonth, setCurrentMonth] = useState(startOfMonth(today));
  const [selectedDate, setSelectedDate] = useState(today);
  const [activeEvent, setActiveEvent] = useState<CalendarEvent | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [recentOpen, setRecentOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [nowSnapshot] = useState(() => new Date());

  const viewParam = searchParams.get("view");
  const view: ViewMode = viewParam === "week" || viewParam === "day" ? viewParam : "month";

  const weekStart = startOfWeek(selectedDate);
  const weekDays = useMemo(() => {
    return Array.from({ length: 7 }, (_, index) => addDays(weekStart, index));
  }, [weekStart]);

  const monthStart = startOfMonth(currentMonth);
  const monthEnd = endOfMonth(currentMonth);
  const gridStart = startOfWeek(monthStart);
  const gridEnd = endOfWeek(monthEnd);
  const monthDays = useMemo(() => {
    const results: Date[] = [];
    let cursor = gridStart;
    while (cursor <= gridEnd) {
      results.push(new Date(cursor));
      cursor = addDays(cursor, 1);
    }
    return results;
  }, [gridStart, gridEnd]);
  const monthWeeks = useMemo(() => {
    const weeks: Date[][] = [];
    for (let i = 0; i < monthDays.length; i += 7) {
      weeks.push(monthDays.slice(i, i + 7));
    }
    return weeks;
  }, [monthDays]);

  const [rangeStart, rangeEnd] = useMemo(() => {
    if (view === "week") {
      return [toISODate(weekStart), toISODate(addDays(weekStart, 6))];
    }
    if (view === "day") {
      const dayKey = toISODate(selectedDate);
      return [dayKey, dayKey];
    }
    return [toISODate(gridStart), toISODate(gridEnd)];
  }, [view, weekStart, selectedDate, gridStart, gridEnd]);

  const { state, actions } = useCalendarData(rangeStart, rangeEnd);
  const undo = useUndoStack(actions.refresh);
  const ai = useAiAssistant({
    onApplied: actions.refresh,
    onAddApplied: (events) => {
      actions.ingest(events);
      undo.record(events);
    },
    onDeleteApplied: (ids) => {
      actions.removeByIds(ids);
    },
  });

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

  const eventsByMonthDate = useMemo(() => {
    if (view !== "month") return {};
    return monthDays.reduce<Record<string, CalendarEvent[]>>((acc, day) => {
      const key = toISODate(day);
      acc[key] = state.indexes.byDate[key] || [];
      return acc;
    }, {});
  }, [view, monthDays, state.indexes.byDate]);

  const weekEventsByHour = useMemo(() => {
    if (view !== "week") return {};
    return state.indexes.byHour;
  }, [view, state.indexes.byHour]);

  const dayEventsByHour = useMemo(() => {
    if (view !== "day") return {};
    const key = toISODate(selectedDate);
    return state.indexes.byHour[key] || {};
  }, [view, selectedDate, state.indexes.byHour]);

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
    const target = next === "month" ? "/calendar" : `/calendar?view=${next}`;
    router.replace(target, { scroll: false });
    if (next !== "month") {
      setCurrentMonth(startOfMonth(selectedDate));
    }
  };

  const handlePrev = () => {
    if (view === "month") {
      setCurrentMonth(addMonths(currentMonth, -1));
      return;
    }
    if (view === "week") {
      const nextDate = addDays(selectedDate, -7);
      setSelectedDate(nextDate);
      setCurrentMonth(startOfMonth(nextDate));
      return;
    }
    const nextDate = addDays(selectedDate, -1);
    setSelectedDate(nextDate);
    setCurrentMonth(startOfMonth(nextDate));
  };

  const handleNext = () => {
    if (view === "month") {
      setCurrentMonth(addMonths(currentMonth, 1));
      return;
    }
    if (view === "week") {
      const nextDate = addDays(selectedDate, 7);
      setSelectedDate(nextDate);
      setCurrentMonth(startOfMonth(nextDate));
      return;
    }
    const nextDate = addDays(selectedDate, 1);
    setSelectedDate(nextDate);
    setCurrentMonth(startOfMonth(nextDate));
  };

  const handleToday = () => {
    setSelectedDate(today);
    setCurrentMonth(startOfMonth(today));
  };

  const viewTitle =
    view === "month"
      ? String(currentMonth.getMonth() + 1)
      : view === "week"
        ? String(weekStart.getMonth() + 1)
        : formatLongDate(selectedDate);
  const viewBadge = view === "week" ? `${getWeekNumber(weekStart)}주차` : null;

  return (
    <div className="month-shell bg-background-light dark:bg-background-dark text-slate-900 dark:text-white overflow-x-hidden min-h-screen flex flex-col transition-colors duration-200">
      <style>{`
        @keyframes overlayIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes overlayOut {
          from { opacity: 1; }
          to { opacity: 0; }
        }
        @keyframes modalIn {
          0% { opacity: 0.4; transform: translateY(28px) scale(0.1); filter: blur(14px); }
          30% { opacity: 0.75; filter: blur(6px); }
          100% { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
        }
        @keyframes modalOut {
          0% { opacity: 1; transform: translateY(0) scale(1); filter: blur(0); }
          45% { opacity: 0.7; transform: translateY(10px) scale(0.35); filter: blur(10px); }
          100% { opacity: 0; transform: translateY(18px) scale(0.1); filter: blur(22px); }
        }
        .animate-overlayIn {
          animation: overlayIn 220ms cubic-bezier(0.22, 1, 0.36, 1);
        }
        .animate-modalIn {
          animation: modalIn 320ms cubic-bezier(0.22, 1, 0.36, 1);
        }
        .animate-overlayOut {
          animation: overlayOut 240ms cubic-bezier(0.2, 0.95, 0.32, 1) forwards;
        }
        .animate-modalOut {
          animation: modalOut 280ms cubic-bezier(0.2, 0.95, 0.32, 1) forwards;
        }
        @media (prefers-reduced-motion: reduce) {
          .animate-modalIn,
          .animate-overlayIn {
            animation: none !important;
          }
        }
      `}</style>
      <AiAssistantModal assistant={ai} />
      <RecentEventsModal open={recentOpen} onClose={() => setRecentOpen(false)} />
      <SearchEventsModal open={searchOpen} onClose={() => setSearchOpen(false)} events={state.events} />
      <EventModal
        open={modalOpen}
        event={activeEvent}
        defaultDate={selectedDate}
        onClose={() => {
          setModalOpen(false);
          setActiveEvent(null);
        }}
        onCreate={handleCreate}
        onCreateRecurring={handleCreateRecurring}
        onUpdate={actions.update}
        onDelete={actions.remove}
      />

      <header className="flex items-center justify-between whitespace-nowrap px-6 py-3 bg-white/80 dark:bg-[#111418]/80 backdrop-blur-md sticky top-0 z-50 border-b border-gray-100 dark:border-gray-800">
        <div className="flex items-center gap-4 text-slate-900 dark:text-white">
          <div className="h-8 px-3 rounded-lg border border-dashed border-slate-300 dark:border-slate-600 text-[10px] font-semibold uppercase tracking-widest flex items-center justify-center text-slate-400">
            로고 예정
          </div>
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-slate-900 dark:text-white">
              {view === "month" ? currentMonth.getFullYear() : view === "week" ? weekStart.getFullYear() : selectedDate.getFullYear()}
            </span>
            <div className="flex items-center gap-1">
              <button
                className="size-8 flex items-center justify-center rounded-full hover:bg-gray-100 dark:hover:bg-gray-800 text-slate-600 dark:text-slate-300"
              type="button"
              onClick={handlePrev}
              aria-label="이전"
            >
                <ChevronLeft className="size-5" />
              </button>
              <button
                className="px-3 py-1 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-sm font-medium"
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
        </div>
        <div className="flex flex-wrap items-center gap-3 justify-end">
          <div className="hidden md:flex items-center gap-1 bg-gray-100 dark:bg-gray-800 p-1 rounded-full mr-2">
            {(Object.keys(VIEW_LABELS) as ViewMode[]).map((key) => (
              <button
                key={key}
                className={`px-3 py-1 rounded-full text-xs font-medium transition-all ${
                  view === key
                    ? "bg-white dark:bg-gray-700 shadow-sm text-slate-900 dark:text-white"
                    : "text-slate-500 dark:text-gray-400 hover:text-slate-900 dark:hover:text-white"
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
              className="flex items-center justify-center rounded-full size-9 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors text-slate-600 dark:text-slate-300"
              type="button"
              onClick={undo.undo}
              disabled={undo.stack.length === 0}
              aria-label="마지막 작업 되돌리기"
            >
              <Undo2 className="size-5" />
            </button>
            <button
              className="flex items-center justify-center rounded-full size-9 hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors text-slate-600 dark:text-slate-300"
              type="button"
              onClick={() => setRecentOpen(true)}
              aria-label="최근 일정"
            >
              <History className="size-5" />
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
              onClick={() => setModalOpen(true)}
              aria-label="일정 추가"
            >
              <Plus className="size-5" />
            </button>
          </div>
          <CalendarHeaderActions status={state.authStatus} />
          <div
            className="bg-center bg-no-repeat bg-cover rounded-full size-8 border border-gray-200 dark:border-gray-700 shadow-sm"
            data-alt="사용자 프로필 이미지"
            style={{
              backgroundImage:
                "url('https://lh3.googleusercontent.com/aida-public/AB6AXuDtp4EN6aKO3e3qE7dZReqE5nVIXN_43sBCdsgWGm4dzvClBNxW2Pt1ibIGwyQGQMdAIBX_9RVDwfqDlnwBKi8NUIR8rfqDGSj3ORylu9O-CXp3AbsLY8YZ3mR-GbbYWBsxTQB71hnJnS4lk0cKSAhR2Mze8_hVjC0o-hEK8J-0fJFYlA65gMBrartXdJiV-A1yCzwWF3mFEhJe5idk641dS6JWo1bXrr9PhY-ZLclsGGcfXhRrdchRQLXlbMpMc3vMNQXkbvxka-4')",
            }}
          ></div>
        </div>
      </header>

      <main className="flex-1 flex flex-col max-w-[1600px] mx-auto w-full p-4 md:p-6 lg:p-6 gap-6 h-[calc(100vh-80px)]">
        <div className="flex flex-col lg:flex-row gap-6 h-full flex-1 min-h-0">
          <aside className="order-2 lg:order-1 w-full lg:w-[320px] xl:w-[360px] flex flex-col gap-4 overflow-hidden">
            <div className="hidden lg:block bg-gradient-to-br from-primary to-blue-600 rounded-2xl p-5 text-white shadow-lg shadow-blue-500/20 relative overflow-hidden">
              <div className="relative z-10 flex flex-col gap-4">
                <div className="flex items-start">
                  <span className="bg-white/20 backdrop-blur-md px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider">다음 일정</span>
                </div>
                <div>
                  {upNextLabel ? (
                    <div className="text-blue-100 text-xs font-medium mb-1">{upNextLabel}</div>
                  ) : null}
                  <h3 className="text-xl font-bold leading-tight mb-2">
                    {upcomingEvent ? upcomingEvent.title : "예정된 일정 없음"}
                  </h3>
                  <div className="flex items-center gap-2 text-blue-100 text-xs">
                    <Clock className="size-4" />
                    <span>{upcomingEvent ? formatTimeRange(upcomingEvent.start, upcomingEvent.end) : ""}</span>
                  </div>
                </div>
              </div>
            </div>
            <div className="flex-1 bg-white dark:bg-[#111418] rounded-2xl p-5 shadow-sm border border-gray-100 dark:border-gray-800 overflow-y-auto no-scrollbar">
              <div className="relative pl-6 border-l-2 border-gray-100 dark:border-gray-800 space-y-6">
                {selectedEvents.length === 0 && (
                  <p className="text-xs text-slate-400">해당 날짜에 일정이 없습니다.</p>
                )}
                {selectedEvents.map((event) => (
                  <div key={event.id} className="relative group">
                    <div className="absolute -left-[30px] top-1 bg-white dark:bg-[#111418] border-2 border-gray-200 dark:border-gray-700 size-3.5 rounded-full group-hover:border-primary transition-colors"></div>
                    <div className="flex flex-col gap-1">
                      <span className="text-xs font-medium text-slate-500">{formatTime(event.start)}</span>
                      <button
                        className="p-2.5 rounded-lg bg-background-light dark:bg-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800 transition-colors cursor-pointer group-hover:shadow-sm text-left"
                        type="button"
                        onClick={() => {
                          setActiveEvent(event);
                          setModalOpen(true);
                        }}
                      >
                        <p className="text-slate-900 dark:text-white font-semibold text-sm">{event.title}</p>
                        <p className="text-slate-500 text-[10px]">{event.location || ""}</p>
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </aside>

          <div className="order-1 lg:order-2 flex-1 flex flex-col gap-4 h-full min-w-0">
            <div className="lg:hidden bg-gradient-to-br from-primary to-blue-600 rounded-2xl p-5 text-white shadow-lg shadow-blue-500/20 relative overflow-hidden">
              <div className="relative z-10 flex flex-col gap-4">
                <div className="flex items-start">
                  <span className="bg-white/20 backdrop-blur-md px-2.5 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider">다음 일정</span>
                </div>
                <div>
                  {upNextLabel ? (
                    <div className="text-blue-100 text-xs font-medium mb-1">{upNextLabel}</div>
                  ) : null}
                  <h3 className="text-xl font-bold leading-tight mb-2">
                    {upcomingEvent ? upcomingEvent.title : "예정된 일정 없음"}
                  </h3>
                  <div className="flex items-center gap-2 text-blue-100 text-xs">
                    <Clock className="size-4" />
                    <span>{upcomingEvent ? formatTimeRange(upcomingEvent.start, upcomingEvent.end) : ""}</span>
                  </div>
                </div>
              </div>
            </div>
            <section className="flex-1 bg-white dark:bg-[#111418] rounded-2xl shadow-sm border border-gray-100 dark:border-gray-800 flex flex-col overflow-hidden h-full">
            <div className="flex items-center gap-3 px-6 py-4 border-b border-gray-100 dark:border-gray-800 shrink-0">
              <h2
                className={`text-slate-900 dark:text-white ${
                  view === "month" ? "text-3xl font-black" : "text-lg font-bold"
                }`}
              >
                {viewTitle}
              </h2>
              {viewBadge && (
                <span className="text-xs font-semibold text-slate-500">{viewBadge}</span>
              )}
            </div>
            <div className="flex-1 p-4 flex flex-col h-full overflow-hidden">
              {view === "month" && (
                <>
                  <div className="grid grid-cols-7 mb-2 shrink-0">
                    {["월", "화", "수", "목", "금", "토", "일"].map((label) => (
                      <div key={label} className="text-center text-xs font-semibold text-slate-400 uppercase tracking-wider">
                        {label}
                      </div>
                    ))}
                  </div>
                  <div className="flex flex-col gap-px bg-gray-100 dark:bg-gray-800 flex-1 border border-gray-100 dark:border-gray-800 rounded-lg overflow-hidden">
                    {monthWeeks.map((weekDays, weekIndex) => {
                      const weekStartDate = new Date(
                        weekDays[0].getFullYear(),
                        weekDays[0].getMonth(),
                        weekDays[0].getDate()
                      );
                      const weekEndDate = new Date(
                        weekDays[6].getFullYear(),
                        weekDays[6].getMonth(),
                        weekDays[6].getDate()
                      );
                      const spanCandidates = state.events
                        .map((event) => {
                          const range = getEventDateRange(event);
                          if (!range || range.endDate < weekStartDate || range.startDate > weekEndDate) {
                            return null;
                          }
                          if (!isMultiDayEvent(event)) return null;
                          const spanStart = range.startDate < weekStartDate ? weekStartDate : range.startDate;
                          const spanEnd = range.endDate > weekEndDate ? weekEndDate : range.endDate;
                          const startIndex = Math.round(
                            (spanStart.getTime() - weekStartDate.getTime()) / 86400000
                          );
                          const endIndex = Math.round((spanEnd.getTime() - weekStartDate.getTime()) / 86400000);
                          return { event, startIndex, endIndex };
                        })
                        .filter((item): item is { event: CalendarEvent; startIndex: number; endIndex: number } => Boolean(item))
                        .sort((a, b) => a.startIndex - b.startIndex || a.endIndex - b.endIndex);

                      const tracks: number[] = [];
                      const spans = spanCandidates.map((item) => {
                        let trackIndex = 0;
                        while (trackIndex < tracks.length && item.startIndex <= tracks[trackIndex]) {
                          trackIndex += 1;
                        }
                        if (trackIndex === tracks.length) {
                          tracks.push(item.endIndex);
                        } else {
                          tracks[trackIndex] = item.endIndex;
                        }
                        return { ...item, trackIndex };
                      });

                      return (
                        <div key={`week-${weekIndex}`} className="relative grid grid-cols-7 gap-px bg-gray-100 dark:bg-gray-800 min-h-[120px]">
                          {weekDays.map((day) => {
                            const isCurrent = day.getMonth() === currentMonth.getMonth();
                            const isToday = isSameDay(day, today);
                            const isSelected = isSameDay(day, selectedDate);
                            const dayKey = toISODate(day);
                            const events = (eventsByMonthDate[dayKey] || []).filter((event) => !isMultiDayEvent(event));
                            return (
                              <button
                                key={dayKey}
                                type="button"
                                className={`bg-white dark:bg-[#111418] p-1.5 flex flex-col gap-1 transition-colors text-left ${
                                  isCurrent ? "hover:bg-gray-50 dark:hover:bg-gray-800/50" : "opacity-40"
                                } ${
                                  isSelected ? "bg-blue-50/50 dark:bg-blue-900/10 ring-1 ring-inset ring-primary" : ""
                                }`}
                                onClick={() => {
                                  setSelectedDate(day);
                                  if (day.getMonth() !== currentMonth.getMonth()) {
                                    setCurrentMonth(startOfMonth(day));
                                  }
                                }}
                                onDoubleClick={() => {
                                  setSelectedDate(day);
                                  setActiveEvent(null);
                                  setModalOpen(true);
                                }}
                              >
                                <span
                                  className={`text-xs font-medium ml-1 inline-flex size-6 items-center justify-center rounded-full self-start ${
                                    isToday ? "bg-primary text-white" : "text-slate-700 dark:text-slate-300"
                                  }`}
                                >
                                  {day.getDate()}
                                </span>
                                {events.slice(0, 3).map((event) => (
                                  <span
                                    key={`${dayKey}-${event.id}`}
                                    className={`text-[10px] px-1.5 py-0.5 rounded-sm truncate border-l-2 font-medium ${getEventStyle(
                                      event
                                    )}`}
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setActiveEvent(event);
                                      setModalOpen(true);
                                    }}
                                  >
                                    {event.title}
                                  </span>
                                ))}
                                {events.length > 3 && (
                                  <span className="text-[10px] text-slate-400">+{events.length - 3}건</span>
                                )}
                              </button>
                            );
                          })}
                          {spans.length > 0 && (
                            <div className="absolute inset-x-0 top-9 bottom-2 grid grid-cols-7 auto-rows-[18px] gap-y-1 px-1 pointer-events-none">
                              {spans.map((item) => (
                                <button
                                  key={`span-${item.event.id}-${weekIndex}`}
                                  type="button"
                                  className={`pointer-events-auto col-span-1 rounded-md border-l-4 px-2 text-[10px] font-medium truncate text-left ${getEventStyle(
                                    item.event
                                  )}`}
                                  style={{
                                    gridColumn: `${item.startIndex + 1} / ${item.endIndex + 2}`,
                                    gridRow: item.trackIndex + 1,
                                  }}
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    setActiveEvent(item.event);
                                    setModalOpen(true);
                                  }}
                                >
                                  {item.event.title}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </>
              )}

              {view === "week" && (
                <div className="flex-1 overflow-auto no-scrollbar">
                  <div className="min-w-[800px] border border-[#e5e7eb] dark:border-[#2a3441] rounded-xl bg-white dark:bg-[#1c2632] shadow-sm overflow-hidden">
                    <table className="w-full border-collapse table-fixed">
                      <thead>
                        <tr className="bg-[#fcfdfd] dark:bg-[#151e29] border-b border-[#e5e7eb] dark:border-[#2a3441]">
                          <th className="w-20 px-4 py-3 border-r border-[#e5e7eb] dark:border-[#2a3441]">
                            <div className="flex justify-center">
                              <Clock className="size-5 text-[#617589] dark:text-gray-500" />
                            </div>
                          </th>
                          {weekDays.map((day) => (
                            <th
                              key={day.toISOString()}
                              className="px-2 py-3 border-r border-[#e5e7eb] dark:border-[#2a3441] last:border-r-0"
                            >
                              <div className="flex flex-col items-center gap-1">
                                <span className="text-xs font-medium text-[#617589] dark:text-gray-400 uppercase">
                                  {day.toLocaleDateString("ko-KR", { weekday: "short" })}
                                </span>
                                <div className="flex items-center justify-center size-8 rounded-full text-sm font-bold text-[#111418] dark:text-white">
                                  {day.getDate()}
                                </div>
                              </div>
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {GRID_HOURS.map((hour) => (
                          <tr key={hour} className="h-28 border-b border-[#e5e7eb] dark:border-[#2a3441] last:border-b-0">
                            <td className="text-xs font-medium text-[#617589] dark:text-gray-500 text-center align-top pt-2 border-r border-[#e5e7eb] dark:border-[#2a3441]">
                              {formatHourLabel(hour)}
                            </td>
                            {weekDays.map((day) => {
                              const dayKey = toISODate(day);
                              const events = weekEventsByHour[dayKey]?.[hour] || [];
                              return (
                                <td
                                  key={`${dayKey}-${hour}`}
                                  className="relative overflow-visible p-1 align-top border-r border-[#e5e7eb] dark:border-[#2a3441] last:border-r-0"
                                >
                                  {events.slice(0, 2).map((event) => {
                                    const layout = getEventBlockStyle(event, hour);
                                    if (!layout) return null;
                                    return (
                                      <button
                                        key={event.id}
                                        className={`absolute left-1 right-1 border-l-4 rounded p-2 text-left text-[10px] font-medium cursor-pointer hover:shadow-md transition-all ${getEventStyle(
                                          event
                                        )}`}
                                        style={{ top: `${layout.topPx}px`, height: `${layout.heightPx}px` }}
                                        type="button"
                                        onClick={() => {
                                          setActiveEvent(event);
                                          setModalOpen(true);
                                        }}
                                      >
                                        <p className="text-xs font-bold">{event.title}</p>
                                        <p className="text-[10px] mt-1">{formatTimeRange(event.start, event.end)}</p>
                                      </button>
                                    );
                                  })}
                                </td>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {view === "day" && (
                <div className="flex-1 overflow-y-auto relative custom-scrollbar">
                  <div className="relative min-h-[1000px] w-full pb-10">
                    {GRID_HOURS.map((hour) => (
                      <div key={hour} className="grid grid-cols-[60px_1fr] h-28 group relative">
                        <div className="border-r border-[#f0f2f4] dark:border-gray-800 pr-3 text-right">
                          <span className="text-xs font-medium text-[#617589] dark:text-gray-500 -translate-y-2.5 block">
                            {formatHourLabel(hour)}
                          </span>
                        </div>
                        <div className="border-b border-[#f0f2f4] dark:border-gray-800/60 relative p-1 overflow-visible">
                          <div className="absolute inset-0 group-hover:bg-[#fafafa] dark:group-hover:bg-[#131d27] transition-colors -z-10"></div>
                          <div className="relative">
                            {(dayEventsByHour[hour] || []).map((event) => {
                              const layout = getEventBlockStyle(event, hour);
                              if (!layout) return null;
                              return (
                              <button
                                key={event.id}
                                className={`absolute left-2 right-4 border-l-4 rounded-r-md px-3 py-2 text-left text-xs font-semibold cursor-pointer hover:shadow-md transition-shadow ${getEventStyle(
                                  event
                                )}`}
                                style={{ top: `${layout.topPx}px`, height: `${layout.heightPx}px` }}
                                type="button"
                                onClick={() => {
                                  setActiveEvent(event);
                                  setModalOpen(true);
                                }}
                              >
                                <p className="text-sm font-bold">{event.title}</p>
                                <p className="text-[10px] mt-1">{formatTimeRange(event.start, event.end)}</p>
                              </button>
                              );
                            })}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {state.loading && <p className="text-xs text-slate-400 mt-3">일정 불러오는 중...</p>}
              {state.error && <p className="text-xs text-red-500 mt-3">{state.error}</p>}
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
