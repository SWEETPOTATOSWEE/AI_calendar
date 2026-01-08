"use client";

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type AnimationEvent,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import {
  Calendar,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  ChevronsUpDown,
  Clock,
  Plus,
} from "lucide-react";
import type {
  CalendarEvent,
  EventPayload,
  EventRecurrence,
  RecurringEventPayload,
} from "../lib/types";
import {
  addDays,
  addMonths,
  formatTime,
  isSameDay,
  parseISODateTime,
  startOfMonth,
  startOfWeek,
  toISODate,
} from "../lib/date";

const WEEKDAY_OPTIONS = [
  { label: "월", value: 0 },
  { label: "화", value: 1 },
  { label: "수", value: 2 },
  { label: "목", value: 3 },
  { label: "금", value: 4 },
  { label: "토", value: 5 },
  { label: "일", value: 6 },
];

const REMINDER_OPTIONS = [
  { label: "10분 전", value: 10 },
  { label: "30분 전", value: 30 },
  { label: "1시간 전", value: 60 },
  { label: "1일 전", value: 1440 },
];

const EVENT_MODAL_TABS = ["basic", "advanced"] as const;

const COLOR_OPTIONS = [
  { id: "default", label: "기본", chip: "bg-slate-300" },
  { id: "1", label: "블루", chip: "bg-blue-500" },
  { id: "2", label: "그린", chip: "bg-emerald-500" },
  { id: "3", label: "퍼플", chip: "bg-violet-500" },
  { id: "4", label: "레드", chip: "bg-rose-500" },
  { id: "5", label: "오렌지", chip: "bg-orange-500" },
  { id: "6", label: "청록", chip: "bg-teal-500" },
  { id: "7", label: "인디고", chip: "bg-indigo-500" },
  { id: "8", label: "앰버", chip: "bg-amber-500" },
  { id: "9", label: "핑크", chip: "bg-pink-500" },
  { id: "10", label: "슬레이트", chip: "bg-slate-500" },
  { id: "11", label: "민트", chip: "bg-cyan-500" },
];
const DEFAULT_COLOR_IDS = new Set(["default", "2", "4", "5", "8"]);

const TIMEZONE_OPTIONS = [
  { label: "한국 (Asia/Seoul)", value: "Asia/Seoul" },
  { label: "UTC", value: "UTC" },
  { label: "미국 동부 (America/New_York)", value: "America/New_York" },
  { label: "영국 (Europe/London)", value: "Europe/London" },
  { label: "일본 (Asia/Tokyo)", value: "Asia/Tokyo" },
];

const getWeekdayIndex = (date: Date) => (date.getDay() + 6) % 7;

const getWeekdayPos = (date: Date) => {
  const day = date.getDate();
  const lastDay = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  if (day + 7 > lastDay) return -1;
  return Math.ceil(day / 7);
};

const parseAttendees = (value: string) => {
  const cleaned = value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  return cleaned.length ? cleaned : null;
};

const formatReminderLabel = (minutes: number) => {
  if (minutes <= 0 || !Number.isFinite(minutes)) return "알림";
  if (minutes % 1440 === 0) return `${minutes / 1440}일 전`;
  if (minutes % 60 === 0) return `${minutes / 60}시간 전`;
  return `${minutes}분 전`;
};

const normalizeTime = (value: string) => {
  const trimmed = value.trim();
  if (!trimmed) return "";
  const parsed = new Date(`1970-01-01T${trimmed}`);
  if (!Number.isNaN(parsed.getTime())) {
    return parsed.toLocaleTimeString("ko-KR", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }
  return trimmed;
};

const to24Hour = (value: string) => {
  if (!value) return "00:00";
  const date = new Date(`1970-01-01 ${value}`);
  if (Number.isNaN(date.getTime())) return "00:00";
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
};

const calcDurationMinutes = (startTime: string, endTime: string) => {
  if (!startTime || !endTime) return 60;
  const start = new Date(`1970-01-01T${to24Hour(startTime)}`);
  const end = new Date(`1970-01-01T${to24Hour(endTime)}`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return 60;
  let diff = (end.getTime() - start.getTime()) / 60000;
  if (diff <= 0) diff += 24 * 60;
  if (diff <= 0) diff = 60;
  return Math.round(diff);
};

const buildInitialState = (event?: CalendarEvent | null, defaultDate?: Date | null) => {
  const seed = parseISODateTime(event?.start) || defaultDate || new Date();
  const endSeed = parseISODateTime(event?.end || "") || seed;
  const startDate = toISODate(seed);
  const endDate = toISODate(endSeed);
  const weekdayIndex = getWeekdayIndex(seed);
  const monthDay = seed.getDate();
  const weekdayPos = getWeekdayPos(seed);
  const recurrence = event?.recurrence || null;
  const recurrenceEnabled = Boolean(recurrence);
  const recurrenceFrequency = recurrence?.freq || "WEEKLY";
  const recurrenceInterval = recurrence?.interval || 1;
  const recurrenceWeekdays = recurrence?.byweekday?.length ? recurrence.byweekday : [weekdayIndex];
  const recurrenceMonthlyMode =
    recurrence?.freq === "MONTHLY" && recurrence?.bysetpos && recurrence?.byweekday?.length
      ? "weekday"
      : "date";
  const recurrenceMonthDay =
    recurrence?.bymonthday?.length && recurrence.bymonthday[0] !== -1
      ? recurrence.bymonthday[0]
      : monthDay;
  const recurrenceWeekday = recurrence?.byweekday?.[0] ?? weekdayIndex;
  const recurrenceWeekdayPos = recurrence?.bysetpos ?? weekdayPos;
  const recurrenceYearMonth = recurrence?.bymonth?.[0] ?? seed.getMonth() + 1;
  const recurrenceYearDay =
    recurrence?.bymonthday?.length && recurrence.bymonthday[0] !== -1
      ? recurrence.bymonthday[0]
      : monthDay;
  const recurrenceEndMode = recurrence?.end?.until
    ? "until"
    : recurrence?.end?.count
      ? "count"
      : "none";

  return {
    title: event?.title || "",
    location: event?.location || "",
    description: event?.description || "",
    attendees: (event?.attendees || []).join(", "),
    reminders: event?.reminders || [],
    visibility: event?.visibility || "default",
    transparency: event?.transparency || "opaque",
    timezone: event?.timezone || "Asia/Seoul",
    meetingUrl: event?.meeting_url || "",
    colorId: event?.color_id || "default",
    allDay: Boolean(event?.all_day),
    startDate,
    startTime: formatTime(event?.start) || "09:00",
    endDate,
    endTime: formatTime(event?.end) || "10:00",
    recurrenceEnabled,
    recurrenceFrequency,
    recurrenceInterval,
    recurrenceWeekdays,
    recurrenceMonthlyMode,
    recurrenceMonthDay,
    recurrenceWeekday,
    recurrenceWeekdayPos,
    recurrenceYearMonth,
    recurrenceYearDay,
    recurrenceEndMode,
    recurrenceEndDate: recurrence?.end?.until || "",
    recurrenceEndCount: recurrence?.end?.count ? String(recurrence.end.count) : "",
  };
};

export type EventModalProps = {
  open: boolean;
  event?: CalendarEvent | null;
  defaultDate?: Date | null;
  forceCreate?: boolean;
  onClose: () => void;
  onCreate: (payload: EventPayload) => Promise<CalendarEvent | void | null>;
  onCreateRecurring: (payload: RecurringEventPayload) => Promise<CalendarEvent[] | null>;
  onUpdate: (event: CalendarEvent, payload: EventPayload) => Promise<void>;
  onDelete: (event: CalendarEvent) => Promise<void>;
};

const useAnimatedOpen = (open: boolean) => {
  return { visible: open };
};

const getScrollParent = (node: HTMLElement | null) => {
  let current = node?.parentElement ?? null;
  while (current) {
    const { overflowY } = window.getComputedStyle(current);
    if (/(auto|scroll|hidden|overlay)/.test(overflowY)) return current;
    current = current.parentElement;
  }
  return document.body;
};

type SelectOption<T extends string | number> = {
  value: T;
  label: string;
};

type CustomSelectProps<T extends string | number> = {
  value: T;
  options: SelectOption<T>[];
  onChange: (value: T) => void;
  disabled?: boolean;
  wrapperClassName?: string;
  buttonClassName?: string;
  menuClassName?: string;
  optionClassName?: string;
  iconClassName?: string;
};

const CustomSelect = <T extends string | number>({
  value,
  options,
  onChange,
  disabled = false,
  wrapperClassName,
  buttonClassName,
  menuClassName,
  optionClassName,
  iconClassName,
}: CustomSelectProps<T>) => {
  const [open, setOpen] = useState(false);
  const [closing, setClosing] = useState(false);
  const [openUp, setOpenUp] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const selected = options.find((option) => option.value === value) ?? options[0];

  useEffect(() => {
    if (!open) return;
    const handleClick = (event: MouseEvent) => {
      if (!containerRef.current || containerRef.current.contains(event.target as Node)) return;
      closeMenu();
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  useEffect(() => {
    if (open) {
      setClosing(false);
    }
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return;
    const scrollParent = getScrollParent(containerRef.current);
    const scrollTarget =
      scrollParent === document.body || scrollParent === document.documentElement ? window : scrollParent;
    const updatePlacement = () => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const boundaryRect =
        scrollParent === document.body || scrollParent === document.documentElement
          ? { top: 0, bottom: window.innerHeight }
          : scrollParent.getBoundingClientRect();
      const estimatedHeight = Math.min(options.length * 36 + 8, 240);
      const menuHeight = menuRef.current?.getBoundingClientRect().height || estimatedHeight;
      const spaceBelow = boundaryRect.bottom - rect.bottom;
      const spaceAbove = rect.top - boundaryRect.top;
      setOpenUp(spaceBelow < menuHeight || spaceAbove >= spaceBelow);
    };
    updatePlacement();
    window.addEventListener("resize", updatePlacement);
    scrollTarget.addEventListener("scroll", updatePlacement, { passive: true });
    return () => {
      window.removeEventListener("resize", updatePlacement);
      scrollTarget.removeEventListener("scroll", updatePlacement);
    };
  }, [open, options.length]);

  useEffect(() => {
    if (disabled && open) {
      setOpen(false);
      setClosing(false);
    }
  }, [disabled, open]);

  const closeMenu = () => {
    if (!open || closing) return;
    setClosing(true);
  };

  const handleMenuAnimationEnd = (event: AnimationEvent<HTMLDivElement>) => {
    if (!closing || event.currentTarget !== event.target) return;
    if (event.animationName !== "popover-out") return;
    setOpen(false);
    setClosing(false);
  };

  return (
    <div ref={containerRef} className={`relative ${wrapperClassName ?? ""}`}>
      <button
        type="button"
        className={`inline-flex items-center justify-start gap-2 rounded-lg border border-transparent bg-transparent text-left focus:outline-none focus:ring-0 ${
          disabled ? "text-slate-400" : "text-[#111827]"
        } px-3 py-2 text-[15px] font-medium ${buttonClassName ?? ""}`}
        onClick={() => {
          if (open) {
            if (closing) {
              setClosing(false);
            } else {
              setClosing(true);
            }
            return;
          }
          setOpen(true);
        }}
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        <span className="truncate text-left">{selected?.label ?? ""}</span>
        <ChevronsUpDown className={`size-4 text-slate-400 ${iconClassName ?? ""}`} />
      </button>
      {open && !disabled && (
        <div
          className={`absolute left-1/2 z-20 min-w-full -translate-x-1/2 ${
            openUp ? "bottom-full mb-2" : "top-full mt-2"
          }`}
        >
          <div
            ref={menuRef}
            className={`min-w-full overflow-hidden popover-surface popover-animate border border-gray-200 bg-white shadow-lg dark:border-gray-700 dark:bg-[#111418] ${
              closing ? "is-closing" : ""
            } ${menuClassName ?? ""}`}
            role="listbox"
            data-side={openUp ? "top" : "bottom"}
            data-align="center"
            onAnimationEnd={handleMenuAnimationEnd}
          >
            <div className="max-h-60 overflow-y-auto">
              {options.map((option) => {
                const active = option.value === value;
                return (
                <button
                  key={String(option.value)}
                  type="button"
                  className={`flex w-full justify-start rounded-md px-3 py-2 text-[15px] text-left transition-colors hover:bg-gray-50 dark:hover:bg-[#1a2632] ${
                    active ? "font-semibold text-[#2563EB]" : "font-medium text-[#111827]"
                  } ${optionClassName ?? ""}`}
                    onClick={() => {
                      onChange(option.value);
                      closeMenu();
                    }}
                    role="option"
                    aria-selected={active}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

const parseISODate = (value: string) => {
  if (!value) return null;
  const [yearText, monthText, dayText] = value.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
};

const formatYearMonthLabel = (date: Date) => {
  return `${date.getFullYear()}년 ${date.getMonth() + 1}월`;
};

const formatTimeLabel = (value: string) => {
  if (!value) return "";
  const [hourText, minuteText] = value.split(":");
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return value;
  const date = new Date(2020, 0, 1, hour, minute);
  return date.toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  });
};

const buildTimeOptions = (stepMinutes: number) => {
  const options: Array<{ value: string; label: string }> = [];
  for (let hour = 0; hour < 24; hour += 1) {
    for (let minute = 0; minute < 60; minute += stepMinutes) {
      const value = `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
      options.push({ value, label: formatTimeLabel(value) });
    }
  }
  return options;
};

type DatePopoverProps = {
  value: string;
  onChange: (value: string) => void;
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  placeholder?: string;
};

const DatePopover = ({
  value,
  onChange,
  label,
  icon,
  disabled = false,
  placeholder = "날짜 선택",
}: DatePopoverProps) => {
  const [open, setOpen] = useState(false);
  const [scrollMode, setScrollMode] = useState(false);
  const [viewDate, setViewDate] = useState<Date>(() => parseISODate(value) ?? new Date());
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const [ready, setReady] = useState(false);
  const [openUp, setOpenUp] = useState(false);
  const [closing, setClosing] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const positionRef = useRef(position);
  const prevScrollModeRef = useRef(scrollMode);
  const yearRefs = useRef<Record<number, HTMLButtonElement | null>>({});
  const monthRefs = useRef<Record<number, HTMLButtonElement | null>>({});

  const selectedDate = parseISODate(value);
  const today = new Date();

  const yearRange = useMemo(() => {
    const baseYear = viewDate.getFullYear();
    return Array.from({ length: 21 }, (_, idx) => baseYear - 10 + idx);
  }, [viewDate]);

  useEffect(() => {
    if (!open) return;
    const handleClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (wrapperRef.current?.contains(target)) return;
      if (popoverRef.current?.contains(target)) return;
      closePopover();
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  useEffect(() => {
    if (open) {
      setReady(false);
      setClosing(false);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    setViewDate(parseISODate(value) ?? new Date());
  }, [open, value]);

  useEffect(() => {
    positionRef.current = position;
  }, [position]);

  useLayoutEffect(() => {
    if (!open) return;
    const scrollModeChanged = prevScrollModeRef.current !== scrollMode;
    prevScrollModeRef.current = scrollMode;
    const scrollParent = getScrollParent(wrapperRef.current);
    const scrollTarget =
      scrollParent === document.body || scrollParent === document.documentElement ? window : scrollParent;
    const updatePlacement = () => {
      if (!wrapperRef.current || !popoverRef.current) return;
      const rect = wrapperRef.current.getBoundingClientRect();
      const popoverRect = popoverRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      const shouldOpenUp = spaceBelow < popoverRect.height && spaceAbove > spaceBelow;
      const top = shouldOpenUp
        ? Math.max(8, rect.top - popoverRect.height - 8)
        : Math.min(window.innerHeight - popoverRect.height - 8, rect.bottom + 8);
      const maxLeft = window.innerWidth - popoverRect.width - 8;
      const preferredLeft = rect.right - popoverRect.width;
      const left = scrollModeChanged
        ? Math.min(Math.max(positionRef.current.left, 8), maxLeft)
        : Math.min(Math.max(preferredLeft, 8), maxLeft);
      setPosition({ top, left });
      setOpenUp(shouldOpenUp);
      setReady(true);
    };
    const frame = window.requestAnimationFrame(updatePlacement);
    window.addEventListener("resize", updatePlacement);
    scrollTarget.addEventListener("scroll", updatePlacement, { passive: true });
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updatePlacement);
      scrollTarget.removeEventListener("scroll", updatePlacement);
    };
  }, [open, scrollMode]);

  useEffect(() => {
    if (!open || !scrollMode) return;
    yearRefs.current[viewDate.getFullYear()]?.scrollIntoView({ block: "center" });
    monthRefs.current[viewDate.getMonth() + 1]?.scrollIntoView({ block: "center" });
  }, [open, scrollMode, viewDate]);

  const monthStart = startOfMonth(viewDate);
  const calendarStart = startOfWeek(monthStart);
  const days = Array.from({ length: 42 }, (_, idx) => addDays(calendarStart, idx));

  const handleDateSelect = (date: Date) => {
    onChange(toISODate(date));
    closePopover();
  };

  const closePopover = () => {
    if (!open || closing) return;
    setClosing(true);
    setScrollMode(false);
  };

  const handlePopoverAnimationEnd = (event: AnimationEvent<HTMLDivElement>) => {
    if (!closing || event.currentTarget !== event.target) return;
    if (event.animationName !== "popover-out") return;
    setOpen(false);
    setClosing(false);
  };

  return (
    <div ref={wrapperRef} className="relative flex-none">
      <button
        type="button"
        className="flex items-center justify-center gap-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-[#0f141a] px-3 py-1 text-[15px] font-medium text-[#111827] disabled:opacity-60 disabled:cursor-not-allowed cursor-pointer"
        onClick={() => {
          if (open) {
            if (closing) {
              setClosing(false);
            } else {
              setClosing(true);
            }
            return;
          }
          setOpen(true);
        }}
        disabled={disabled}
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <span className={value ? "" : "text-[#9CA3AF]"}>{value || placeholder}</span>
      </button>
      {open &&
        !disabled &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={popoverRef}
            className={`fixed z-[9999] w-72 overflow-hidden popover-surface border border-gray-100 dark:border-gray-700 bg-white dark:bg-[#111418] p-4 shadow-lg ${
              ready || closing ? "popover-animate" : ""
            } ${closing ? "is-closing" : ""}`}
            style={{
              top: position.top,
              left: position.left,
              visibility: ready ? "visible" : "hidden",
              pointerEvents: ready ? "auto" : "none",
            }}
            data-side={openUp ? "top" : "bottom"}
            data-align="end"
            onAnimationEnd={handlePopoverAnimationEnd}
          >
          <div className="flex items-center justify-between">
            <div className="flex items-center">
              <button
                type="button"
                className="flex h-7 items-center justify-center gap-0.5 pr-1 text-sm font-semibold text-[#111827] hover:text-blue-600 cursor-pointer"
                onClick={() => setScrollMode((prev) => !prev)}
                aria-label="월/년도 이동"
              >
                <span>{formatYearMonthLabel(viewDate)}</span>
                <ChevronRight className={`size-4 translate-y-[1px] transition-transform ${scrollMode ? "rotate-90" : ""}`} />
              </button>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                className="flex size-7 items-center justify-center rounded-full border border-gray-200 text-slate-500 hover:border-blue-200 hover:text-blue-600 cursor-pointer"
                onClick={() => setViewDate((prev) => addMonths(prev, -1))}
                aria-label="이전 달"
              >
                <ChevronLeft className="size-4" />
              </button>
              <button
                type="button"
                className="flex size-7 items-center justify-center rounded-full border border-gray-200 text-slate-500 hover:border-blue-200 hover:text-blue-600 cursor-pointer"
                onClick={() => setViewDate((prev) => addMonths(prev, 1))}
                aria-label="다음 달"
              >
                <ChevronRight className="size-4" />
              </button>
            </div>
          </div>
          {scrollMode && (
            <div className="mt-3 grid grid-cols-2 gap-3">
              <div className="max-h-40 overflow-y-auto rounded-lg border border-gray-100 bg-gray-50 dark:border-gray-700 dark:bg-[#0f141a] py-2">
                {yearRange.map((year) => {
                  const active = year === viewDate.getFullYear();
                  return (
                    <button
                      key={year}
                      type="button"
                      ref={(node) => {
                        yearRefs.current[year] = node;
                      }}
                      className={`flex w-full items-center justify-center px-3 py-2 text-sm font-medium transition-colors cursor-pointer ${
                        active ? "text-blue-600" : "text-slate-600 hover:text-slate-900"
                      }`}
                      onClick={() => setViewDate(new Date(year, viewDate.getMonth(), 1))}
                    >
                      {year}년
                    </button>
                  );
                })}
              </div>
              <div className="max-h-40 overflow-y-auto rounded-lg border border-gray-100 bg-gray-50 dark:border-gray-700 dark:bg-[#0f141a] py-2">
                {Array.from({ length: 12 }, (_, idx) => idx + 1).map((month) => {
                  const active = month === viewDate.getMonth() + 1;
                  return (
                    <button
                      key={month}
                      type="button"
                      ref={(node) => {
                        monthRefs.current[month] = node;
                      }}
                      className={`flex w-full items-center justify-center px-3 py-2 text-sm font-medium transition-colors cursor-pointer ${
                        active ? "text-blue-600" : "text-slate-600 hover:text-slate-900"
                      }`}
                      onClick={() => setViewDate(new Date(viewDate.getFullYear(), month - 1, 1))}
                    >
                      {month}월
                    </button>
                  );
                })}
              </div>
            </div>
          )}
          {!scrollMode && (
            <>
              <div className="mt-3 grid grid-cols-7 text-xs text-slate-400">
                {["월", "화", "수", "목", "금", "토", "일"].map((day) => (
                  <span key={day} className="text-center">
                    {day}
                  </span>
                ))}
              </div>
              <div className="mt-2 grid grid-cols-7 gap-1 text-sm">
                {days.map((date) => {
                  const isCurrentMonth = date.getMonth() === viewDate.getMonth();
                  const isSelected = selectedDate ? isSameDay(selectedDate, date) : false;
                  const isToday = isSameDay(today, date);
                  return (
                    <button
                      key={date.toISOString()}
                      type="button"
                      className={`flex h-8 w-full items-center justify-center rounded-full transition-colors cursor-pointer ${
                        isSelected
                          ? "bg-blue-600 text-white"
                          : isToday
                            ? "border border-blue-200 text-blue-600"
                            : isCurrentMonth
                              ? "text-slate-700 hover:bg-slate-100"
                              : "text-slate-300"
                      }`}
                      onClick={() => handleDateSelect(date)}
                    >
                      {date.getDate()}
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>,
        document.body
      )}
    </div>
  );
};

type TimePopoverProps = {
  value: string;
  onChange: (value: string) => void;
  label: string;
  icon: ReactNode;
  disabled?: boolean;
  placeholder?: string;
};

const TimePopover = ({
  value,
  onChange,
  label,
  icon,
  disabled = false,
  placeholder = "시간 선택",
}: TimePopoverProps) => {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const selectedRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const [ready, setReady] = useState(false);
  const [openUp, setOpenUp] = useState(false);
  const [closing, setClosing] = useState(false);
  const timeOptions = useMemo(() => buildTimeOptions(15), []);

  useEffect(() => {
    if (!open) return;
    const handleClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (wrapperRef.current?.contains(target)) return;
      if (popoverRef.current?.contains(target)) return;
      closePopover();
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  useEffect(() => {
    if (open) {
      setReady(false);
      setClosing(false);
    }
  }, [open]);

  useLayoutEffect(() => {
    if (!open) return;
    const scrollParent = getScrollParent(wrapperRef.current);
    const scrollTarget =
      scrollParent === document.body || scrollParent === document.documentElement ? window : scrollParent;
    const updatePlacement = () => {
      if (!wrapperRef.current || !popoverRef.current) return;
      const rect = wrapperRef.current.getBoundingClientRect();
      const popoverRect = popoverRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom;
      const spaceAbove = rect.top;
      const shouldOpenUp = spaceBelow < popoverRect.height && spaceAbove > spaceBelow;
      const top = shouldOpenUp
        ? Math.max(8, rect.top - popoverRect.height - 8)
        : Math.min(window.innerHeight - popoverRect.height - 8, rect.bottom + 8);
      const left = Math.min(
        Math.max(rect.right - popoverRect.width, 8),
        window.innerWidth - popoverRect.width - 8
      );
      setPosition({ top, left });
      setOpenUp(shouldOpenUp);
      setReady(true);
    };
    const frame = window.requestAnimationFrame(updatePlacement);
    window.addEventListener("resize", updatePlacement);
    scrollTarget.addEventListener("scroll", updatePlacement, { passive: true });
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener("resize", updatePlacement);
      scrollTarget.removeEventListener("scroll", updatePlacement);
    };
  }, [open]);

  useEffect(() => {
    if (open) {
      selectedRef.current?.scrollIntoView({ block: "center" });
    }
  }, [open, value]);

  const closePopover = () => {
    if (!open || closing) return;
    setClosing(true);
  };

  const handlePopoverAnimationEnd = (event: AnimationEvent<HTMLDivElement>) => {
    if (!closing || event.currentTarget !== event.target) return;
    if (event.animationName !== "popover-out") return;
    setOpen(false);
    setClosing(false);
  };

  return (
    <div ref={wrapperRef} className="relative flex-none">
      <button
        type="button"
        className="flex items-center justify-center gap-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-[#0f141a] px-3 py-1 text-[15px] font-medium text-[#111827] disabled:opacity-60 disabled:cursor-not-allowed cursor-pointer"
        onClick={() => {
          if (open) {
            if (closing) {
              setClosing(false);
            } else {
              setClosing(true);
            }
            return;
          }
          setOpen(true);
        }}
        disabled={disabled}
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <span className={value ? "" : "text-[#9CA3AF]"}>
          {value ? formatTimeLabel(value) : placeholder}
        </span>
      </button>
      {open &&
        !disabled &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={popoverRef}
            className={`fixed z-[9999] w-40 overflow-hidden popover-surface border border-gray-100 dark:border-gray-700 bg-white dark:bg-[#111418] p-2 shadow-lg ${
              ready || closing ? "popover-animate" : ""
            } ${closing ? "is-closing" : ""}`}
            style={{
              top: position.top,
              left: position.left,
              visibility: ready ? "visible" : "hidden",
              pointerEvents: ready ? "auto" : "none",
            }}
            data-side={openUp ? "top" : "bottom"}
            data-align="end"
            onAnimationEnd={handlePopoverAnimationEnd}
          >
          <div className="max-h-56 overflow-y-auto pr-1">
            {timeOptions.map((option) => {
              const active = option.value === value;
              return (
                <button
                  key={option.value}
                  type="button"
                  ref={active ? selectedRef : null}
                  className={`flex w-full items-center justify-center rounded-lg px-3 py-2 text-sm font-medium transition-colors cursor-pointer ${
                    active ? "bg-blue-600 text-white" : "text-slate-700 hover:bg-slate-100"
                  }`}
                  onClick={() => {
                    onChange(option.value);
                    closePopover();
                  }}
                >
                  {option.label}
                </button>
              );
            })}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};

export default function EventModal({
  open,
  event,
  defaultDate,
  forceCreate = false,
  onClose,
  onCreate,
  onCreateRecurring,
  onUpdate,
  onDelete,
}: EventModalProps) {
  const { visible } = useAnimatedOpen(open);
  const [stableEvent, setStableEvent] = useState<CalendarEvent | null>(event ?? null);
  const [stableDefaultDate, setStableDefaultDate] = useState<Date | null>(defaultDate ?? null);
  const [form, setForm] = useState(() => buildInitialState(event, defaultDate));
  const [activeTab, setActiveTab] = useState<"basic" | "advanced">("basic");
  const [showAllColors, setShowAllColors] = useState(false);
  const activeTabIndex = EVENT_MODAL_TABS.indexOf(activeTab);
  const tabToggleStyle = useMemo(
    () =>
      ({
        "--seg-count": String(EVENT_MODAL_TABS.length),
        "--seg-index": String(activeTabIndex),
      }) as CSSProperties,
    [activeTabIndex]
  );
  const isEdit = Boolean(stableEvent) && !forceCreate;
  const isRecurring = !forceCreate && stableEvent?.recur === "recurring";
  const descriptionRef = useRef<HTMLTextAreaElement | null>(null);
  const resizeDescription = () => {
    if (!descriptionRef.current) return;
    const lineHeight = parseFloat(getComputedStyle(descriptionRef.current).lineHeight || "0") || 22;
    const maxHeight = lineHeight * 12;
    descriptionRef.current.style.height = "auto";
    const nextHeight = Math.min(descriptionRef.current.scrollHeight, maxHeight);
    descriptionRef.current.style.height = `${nextHeight}px`;
    descriptionRef.current.style.overflowY =
      descriptionRef.current.scrollHeight > maxHeight ? "auto" : "hidden";
  };

  useEffect(() => {
    if (!open) return;
    setStableEvent(event ?? null);
    setStableDefaultDate(defaultDate ?? null);
    setForm(buildInitialState(event, defaultDate));
    setActiveTab("basic");
    setShowAllColors(false);
    requestAnimationFrame(() => resizeDescription());
  }, [open, event, defaultDate]);

  useEffect(() => {
    resizeDescription();
  }, [form.description]);

  const visibleColorOptions = useMemo(() => {
    if (showAllColors) return COLOR_OPTIONS;
    return COLOR_OPTIONS.filter((option) => DEFAULT_COLOR_IDS.has(option.id));
  }, [showAllColors]);

  const [customReminderOpen, setCustomReminderOpen] = useState(false);
  const [customReminderClosing, setCustomReminderClosing] = useState(false);
  const [customReminderValue, setCustomReminderValue] = useState("1");
  const [customReminderUnit, setCustomReminderUnit] = useState<"days" | "hours" | "minutes">("days");
  const [customReminderValues, setCustomReminderValues] = useState<number[]>([]);

  const closeCustomReminder = () => {
    if (!customReminderOpen || customReminderClosing) return;
    setCustomReminderClosing(true);
  };

  const handleCustomReminderAnimationEnd = (event: AnimationEvent<HTMLDivElement>) => {
    if (!customReminderClosing || event.currentTarget !== event.target) return;
    if (event.animationName !== "popover-out") return;
    setCustomReminderOpen(false);
    setCustomReminderClosing(false);
  };

  const recurrenceRule = useMemo<EventRecurrence | null>(() => {
    if (!form.recurrenceEnabled) return null;
    const interval = Math.max(1, Number(form.recurrenceInterval) || 1);
    const rule: EventRecurrence = {
      freq: form.recurrenceFrequency,
      interval,
      byweekday: null,
      bymonthday: null,
      bysetpos: null,
      bymonth: null,
      end: null,
    };

    if (form.recurrenceFrequency === "WEEKLY") {
      rule.byweekday = form.recurrenceWeekdays.length ? form.recurrenceWeekdays : null;
    }

    if (form.recurrenceFrequency === "MONTHLY") {
      if (form.recurrenceMonthlyMode === "weekday") {
        rule.byweekday = [form.recurrenceWeekday];
        rule.bysetpos = form.recurrenceWeekdayPos;
      } else {
        rule.bymonthday = [form.recurrenceMonthDay];
      }
    }

    if (form.recurrenceFrequency === "YEARLY") {
      rule.bymonth = [form.recurrenceYearMonth];
      rule.bymonthday = [form.recurrenceYearDay];
    }

    if (form.recurrenceEndMode === "until" && form.recurrenceEndDate) {
      rule.end = { until: form.recurrenceEndDate, count: null };
    }
    if (form.recurrenceEndMode === "count") {
      const countValue = Number(form.recurrenceEndCount);
      if (Number.isFinite(countValue) && countValue > 0) {
        rule.end = { until: null, count: Math.round(countValue) };
      }
    }

    return rule;
  }, [form]);

  const recurrenceError = useMemo(() => {
    if (!form.recurrenceEnabled) return "";
    if (form.recurrenceFrequency === "WEEKLY" && form.recurrenceWeekdays.length === 0) {
      return "반복 요일을 선택해 주세요.";
    }
    if (form.recurrenceFrequency === "MONTHLY" && form.recurrenceMonthlyMode === "weekday") {
      if (!form.recurrenceWeekdayPos) return "월간 반복 순서를 선택해 주세요.";
    }
    if (form.recurrenceEndMode === "until" && !form.recurrenceEndDate) {
      return "반복 종료 날짜를 선택해 주세요.";
    }
    if (form.recurrenceEndMode === "count" && !form.recurrenceEndCount) {
      return "반복 횟수를 입력해 주세요.";
    }
    return "";
  }, [form]);

  const recurrenceIntervalUnit = useMemo(() => {
    switch (form.recurrenceFrequency) {
      case "DAILY":
        return "일";
      case "WEEKLY":
        return "주";
      case "MONTHLY":
        return "개월";
      case "YEARLY":
        return "년";
      default:
        return "";
    }
  }, [form.recurrenceFrequency]);

  const recurrenceIntervalWidth = useMemo(() => {
    const raw = String(form.recurrenceInterval ?? "").trim();
    const length = raw.length || 1;
    const clamped = Math.min(Math.max(length, 1), 6);
    return `calc(${clamped}ch + 2.75rem)`;
  }, [form.recurrenceInterval]);

  const recurrenceCountWidth = useMemo(() => {
    const raw = String(form.recurrenceEndCount ?? "").trim();
    const length = raw.length || 1;
    const clamped = Math.min(Math.max(length, 1), 6);
    return `calc(${clamped}ch + 2.75rem)`;
  }, [form.recurrenceEndCount]);

  const recurrenceMonthDayWidth = useMemo(() => {
    const raw = String(form.recurrenceMonthDay ?? "").trim();
    const length = raw.length || 1;
    const clamped = Math.min(Math.max(length, 1), 2);
    return `calc(${clamped}ch + 2.75rem)`;
  }, [form.recurrenceMonthDay]);


  const payload = useMemo<EventPayload>(() => {
    const startClock = form.allDay ? "00:00" : to24Hour(form.startTime);
    const endClock = form.allDay ? "23:59" : to24Hour(form.endTime);
    const start = `${form.startDate}T${startClock}`;
    const end = form.endDate && endClock ? `${form.endDate}T${endClock}` : null;
    return {
      title: form.title.trim() || "제목 없음",
      start,
      end,
      location: form.location.trim() || null,
      description: form.description.trim() || null,
      attendees: parseAttendees(form.attendees),
      reminders: form.reminders.length ? form.reminders : null,
      visibility: form.visibility,
      transparency: form.transparency,
      timezone: form.timezone || null,
      meeting_url: form.meetingUrl.trim() || null,
      color_id: form.colorId,
      all_day: form.allDay,
    };
  }, [form]);

  const recurringPayload = useMemo<RecurringEventPayload | null>(() => {
    if (!form.recurrenceEnabled || !recurrenceRule) return null;
    const durationMinutes = form.allDay ? null : calcDurationMinutes(form.startTime, form.endTime);
    return {
      type: "recurring",
      title: form.title.trim() || "제목 없음",
      start_date: form.startDate,
      time: form.allDay ? null : to24Hour(form.startTime),
      duration_minutes: durationMinutes,
      location: form.location.trim() || null,
      description: form.description.trim() || null,
      attendees: parseAttendees(form.attendees),
      reminders: form.reminders.length ? form.reminders : null,
      visibility: form.visibility,
      transparency: form.transparency,
      timezone: form.timezone || null,
      meeting_url: form.meetingUrl.trim() || null,
      color_id: form.colorId,
      recurrence: recurrenceRule,
    };
  }, [form, recurrenceRule]);

  if (!visible) return null;

  return (
    <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/40 px-4">
      <div
        className="w-full max-w-2xl rounded-2xl bg-[#F9FAFB] border border-gray-100 shadow-xl"
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-800">
          <div className="flex items-center gap-3">
            <h3 className="text-[18px] font-semibold text-[#111827]">
              {isEdit ? "일정 수정" : "새 일정"}
            </h3>
            <div
              className="relative flex items-center rounded-full bg-gray-100 dark:bg-gray-800 p-1 text-xs segmented-toggle"
              style={tabToggleStyle}
            >
              <span className="segmented-indicator">
                <span
                  key={activeTab}
                  className="view-indicator-pulse block h-full w-full rounded-full bg-white dark:bg-gray-700 shadow-sm"
                />
              </span>
              {EVENT_MODAL_TABS.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  className={`relative z-10 flex-1 px-3 py-1 text-[14px] transition-colors ${
                    activeTab === tab
                      ? "text-[#2563EB] font-semibold"
                      : "text-[#6B7280] font-medium hover:text-[#2563EB]"
                  }`}
                  onClick={() => setActiveTab(tab)}
                >
                  {tab === "basic" ? "기본" : "고급"}
                </button>
              ))}
            </div>
          </div>
          <button className="text-slate-400 hover:text-slate-900 dark:hover:text-white" onClick={onClose} type="button">
            ✕
          </button>
        </div>
        <div className="px-6 py-5 space-y-4 max-h-[70vh] overflow-y-auto">
          {isRecurring && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-700">
              반복 일정은 수정할 수 없습니다. 삭제 후 새로 등록해 주세요.
            </div>
          )}
          {activeTab === "basic" && (
            <>
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632]">
                <div className="flex h-12 items-center px-4 py-2">
                  <label className="sr-only">제목</label>
                  <input
                    className="w-full border-none bg-transparent text-[15px] font-medium text-[#111827] placeholder:text-[15px] placeholder:font-normal placeholder:text-[#9CA3AF] focus:outline-none focus:ring-0"
                    placeholder="제목"
                    value={form.title}
                    onChange={(event) => setForm((prev) => ({ ...prev, title: event.target.value }))}
                    disabled={isRecurring}
                  />
                </div>
              </div>
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632]">
                <div className="flex h-12 items-center justify-between px-4 py-2 text-[14px] font-medium text-[#4B5563]">
                  <span>하루종일</span>
                  <div className="ml-auto flex justify-end">
                    <label className="relative inline-flex size-6 items-center justify-center">
                    <input
                      className="peer size-6 appearance-none rounded-full border border-slate-300 bg-white shadow-sm transition-colors checked:border-blue-500 checked:bg-blue-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-1 focus-visible:ring-offset-white disabled:opacity-50 dark:border-slate-600 dark:bg-[#111418] dark:checked:border-blue-400 dark:checked:bg-blue-400 dark:focus-visible:ring-blue-300 dark:focus-visible:ring-offset-[#111418]"
                      type="checkbox"
                      checked={form.allDay}
                      onChange={(event) => setForm((prev) => ({ ...prev, allDay: event.target.checked }))}
                      disabled={isRecurring}
                    />
                    <span className="pointer-events-none absolute text-slate-300 transition-colors peer-checked:text-white">
                      <Check className="size-4" />
                    </span>
                    </label>
                  </div>
                </div>
                <div className="h-px bg-gray-200 dark:bg-gray-700 mx-4" />
                <div className="flex h-12 items-center px-4 py-2">
                  <span className="w-7 shrink-0 text-[14px] font-medium text-[#374151]">시작</span>
                  <label className="sr-only">시작 날짜</label>
                  <div className="flex items-center gap-2 ml-auto">
                    <DatePopover
                      label="시작 날짜"
                      value={form.startDate}
                      onChange={(value) => setForm((prev) => ({ ...prev, startDate: value }))}
                      disabled={isRecurring}
                      placeholder="날짜 선택"
                    />
                    {!form.allDay && (
                      <TimePopover
                        label="시작 시간"
                        value={form.startTime}
                        onChange={(value) =>
                          setForm((prev) => ({ ...prev, startTime: normalizeTime(value) }))
                        }
                        disabled={isRecurring}
                        placeholder="시간 선택"
                      />
                    )}
                  </div>
                </div>
                <div className="h-px bg-gray-200 dark:bg-gray-700 mx-4" />
                <div className="flex h-12 items-center px-4 py-2">
                  <span className="w-7 shrink-0 text-[14px] font-medium text-[#374151]">종료</span>
                  <label className="sr-only">종료 날짜</label>
                  <div className="flex items-center gap-2 ml-auto">
                    <DatePopover
                      label="종료 날짜"
                      value={form.endDate}
                      onChange={(value) => setForm((prev) => ({ ...prev, endDate: value }))}
                      disabled={isRecurring}
                      placeholder="날짜 선택"
                    />
                    {!form.allDay && (
                      <TimePopover
                        label="종료 시간"
                        value={form.endTime}
                        onChange={(value) =>
                          setForm((prev) => ({ ...prev, endTime: normalizeTime(value) }))
                        }
                        disabled={isRecurring}
                        placeholder="시간 선택"
                      />
                    )}
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632]">
                <div className="flex h-12 items-center px-4 py-2">
                  <label className="sr-only">장소</label>
                  <input
                    className="w-full border-none bg-transparent text-[15px] font-medium text-[#111827] placeholder:text-[15px] placeholder:font-normal placeholder:text-[#9CA3AF] focus:outline-none focus:ring-0"
                    placeholder="장소"
                    value={form.location}
                    onChange={(event) => setForm((prev) => ({ ...prev, location: event.target.value }))}
                    disabled={isRecurring}
                  />
                </div>
              </div>
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-4 min-h-12 flex items-center">
                <div className="flex w-full items-center justify-between gap-2">
                  <label className="text-[14px] font-medium text-[#374151] whitespace-nowrap shrink-0">알림</label>
                  <div className="flex flex-wrap justify-end gap-2">
                  {(() => {
                    const baseValues = REMINDER_OPTIONS.map((option) => option.value);
                    const customSorted = [...customReminderValues].sort((a, b) => a - b);
                    const orderedValues = [...baseValues, ...customSorted];
                    return orderedValues.map((value) => {
                    const active = form.reminders.includes(value);
                    const baseOption = REMINDER_OPTIONS.find((option) => option.value === value);
                    const label = baseOption?.label ?? formatReminderLabel(value);
                    return (
                      <button
                        key={value}
                        type="button"
                        className={`px-3 py-1 rounded-full text-[13px] font-medium border transition-colors ${
                          active
                            ? "border-[#2563EB] text-[#2563EB] font-semibold"
                            : "border-gray-200 text-[#374151] hover:border-[#2563EB]/30"
                        }`}
                        onClick={() =>
                          setForm((prev) => ({
                            ...prev,
                            reminders: active
                              ? prev.reminders.filter((item) => item !== value)
                              : [...prev.reminders, value],
                          }))
                        }
                        disabled={isRecurring}
                      >
                        {label}
                      </button>
                    );
                  });
                  })()}
                  <div className="relative">
                    <button
                      type="button"
                      className="flex size-7 items-center justify-center rounded-full border border-gray-300 text-slate-500 hover:border-primary/40"
                      onClick={() => {
                        if (customReminderOpen) {
                          if (customReminderClosing) {
                            setCustomReminderClosing(false);
                          } else {
                            setCustomReminderClosing(true);
                          }
                          return;
                        }
                        setCustomReminderOpen(true);
                      }}
                      disabled={isRecurring}
                      aria-expanded={customReminderOpen}
                      aria-haspopup="dialog"
                      aria-label="알림 직접 추가"
                    >
                      <Plus className="size-4" />
                    </button>
                    {customReminderOpen && !isRecurring && (
                      <div
                        className={`absolute right-0 bottom-full mb-2 w-52 max-w-[calc(100vw-2rem)] popover-surface popover-animate border border-gray-100 dark:border-gray-700 bg-white dark:bg-[#111418] shadow-lg p-3 z-10 ${
                          customReminderClosing ? "is-closing" : ""
                        }`}
                        data-side="top"
                        data-align="end"
                        onAnimationEnd={handleCustomReminderAnimationEnd}
                      >
                        <div className="flex items-center gap-2">
                          <input
                            type="number"
                            min="1"
                            className="w-20 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-2 py-1 text-xs"
                            value={customReminderValue}
                            onChange={(event) => setCustomReminderValue(event.target.value)}
                          />
                          <CustomSelect
                            value={customReminderUnit}
                            options={[
                              { value: "days", label: "일 전" },
                              { value: "hours", label: "시간 전" },
                              { value: "minutes", label: "분 전" },
                            ]}
                            onChange={(nextValue) =>
                              setCustomReminderUnit(nextValue as "days" | "hours" | "minutes")
                            }
                            wrapperClassName="flex-1"
                            buttonClassName="px-2 py-1 text-xs font-medium"
                            optionClassName="text-xs font-medium"
                          />
                        </div>
                        <div className="mt-3 flex justify-end gap-2">
                          <button
                            type="button"
                            className="px-2 py-1 text-xs text-slate-500"
                            onClick={closeCustomReminder}
                          >
                            취소
                          </button>
                          <button
                            type="button"
                            className="px-3 py-1 rounded-full text-xs font-semibold bg-primary text-white"
                            onClick={() => {
                              const numericValue = Number(customReminderValue);
                              if (!Number.isFinite(numericValue) || numericValue <= 0) return;
                              const multiplier =
                                customReminderUnit === "days"
                                  ? 1440
                                  : customReminderUnit === "hours"
                                    ? 60
                                    : 1;
                              const minutesValue = Math.round(numericValue * multiplier);
                              setCustomReminderValues((prev) =>
                                prev.includes(minutesValue) ? prev : [...prev, minutesValue]
                              );
                              setForm((prev) => ({
                                ...prev,
                                reminders: Array.from(new Set([...prev.reminders, minutesValue])),
                              }));
                              closeCustomReminder();
                            }}
                          >
                            추가
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>
              </div>
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-4 py-2 min-h-12 flex items-center">
                <div className="flex w-full items-center justify-between gap-2">
                  <label className="text-[14px] font-medium text-[#374151] whitespace-nowrap shrink-0">색상</label>
                  <div className="ml-auto flex flex-wrap justify-end gap-2">
                    {visibleColorOptions.map((option) => {
                      const active = form.colorId === option.id;
                      return (
                        <button
                          key={option.id}
                          type="button"
                        className={`flex items-center gap-2 rounded-full border px-3 py-1 text-[13px] font-medium transition-colors ${
                          active
                            ? "border-[#2563EB] text-[#2563EB] font-semibold"
                            : "border-gray-200 text-[#374151] hover:border-[#2563EB]/30"
                        }`}
                          onClick={() => setForm((prev) => ({ ...prev, colorId: option.id }))}
                          disabled={isRecurring}
                        >
                          <span className={`h-2.5 w-2.5 rounded-full ${option.chip}`}></span>
                          {option.label}
                        </button>
                      );
                    })}
                    {!showAllColors && (
                      <button
                        type="button"
                        className="rounded-full border border-gray-300 px-3 py-1 text-[13px] font-medium text-[#374151] transition-colors hover:border-[#2563EB]/40 hover:text-[#2563EB]"
                        onClick={() => setShowAllColors(true)}
                        disabled={isRecurring}
                      >
                        모든 색상
                      </button>
                    )}
                    {showAllColors && (
                      <button
                        type="button"
                        className="rounded-full border border-gray-300 px-3 py-1 text-[13px] font-medium text-[#374151] transition-colors hover:border-[#2563EB]/40 hover:text-[#2563EB]"
                        onClick={() => setShowAllColors(false)}
                        disabled={isRecurring}
                      >
                        닫기
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </>
          )}

          {activeTab === "advanced" && (
            <>
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632]">
                <div className="flex min-h-12 items-center px-4 py-2">
                  <label className="sr-only">설명</label>
                  <textarea
                    ref={descriptionRef}
                    rows={1}
                    className="w-full resize-none border-none bg-transparent text-[15px] font-medium text-[#111827] placeholder:text-[15px] placeholder:font-normal placeholder:text-[#9CA3AF] focus:outline-none focus:ring-0"
                    placeholder="설명"
                    value={form.description}
                    onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
                    disabled={isRecurring}
                  />
                </div>
              </div>
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632]">
                <div className="flex h-12 items-center px-4 py-2">
                  <label className="sr-only">참석자</label>
                  <input
                    className="w-full border-none bg-transparent text-[15px] font-medium text-[#111827] placeholder:text-[15px] placeholder:font-normal placeholder:text-[#9CA3AF] focus:outline-none focus:ring-0"
                    placeholder="참석자"
                    value={form.attendees}
                    onChange={(event) => setForm((prev) => ({ ...prev, attendees: event.target.value }))}
                    disabled={isRecurring}
                  />
                </div>
                <div className="h-px bg-gray-200 dark:bg-gray-700 mx-4" />
                <div className="flex h-12 items-center px-4 py-2">
                  <label className="sr-only">회의 링크</label>
                  <input
                    className="w-full border-none bg-transparent text-[15px] font-medium text-[#111827] placeholder:text-[15px] placeholder:font-normal placeholder:text-[#9CA3AF] focus:outline-none focus:ring-0"
                    placeholder="회의 링크"
                    value={form.meetingUrl}
                    onChange={(event) => setForm((prev) => ({ ...prev, meetingUrl: event.target.value }))}
                    disabled={isRecurring}
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-4 py-1 min-h-12 flex items-center">
                  <div className="flex w-full items-center justify-between gap-2">
                    <span className="text-[14px] font-medium text-[#374151] whitespace-nowrap shrink-0">공개</span>
                    <label className="sr-only">공개 범위</label>
                    <CustomSelect
                      value={form.visibility}
                      options={[
                        { value: "default", label: "기본" },
                        { value: "public", label: "공개" },
                        { value: "private", label: "비공개" },
                      ]}
                      onChange={(nextValue) => setForm((prev) => ({ ...prev, visibility: nextValue }))}
                      disabled={isRecurring}
                      wrapperClassName="ml-auto"
                    />
                  </div>
                </div>
                <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-4 py-1 min-h-12 flex items-center">
                  <div className="flex w-full items-center justify-between gap-2">
                    <span className="text-[14px] font-medium text-[#374151] whitespace-nowrap shrink-0">표시</span>
                    <label className="sr-only">표시 상태</label>
                    <CustomSelect
                      value={form.transparency}
                      options={[
                        { value: "opaque", label: "바쁨" },
                        { value: "transparent", label: "한가함" },
                      ]}
                      onChange={(nextValue) => setForm((prev) => ({ ...prev, transparency: nextValue }))}
                      disabled={isRecurring}
                      wrapperClassName="ml-auto"
                    />
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-4 py-1 min-h-12 flex items-center">
                <div className="flex w-full items-center justify-between gap-2">
                  <label className="text-[14px] font-medium text-[#374151] whitespace-nowrap shrink-0">
                    시간대
                  </label>
                  <CustomSelect
                    value={form.timezone}
                    options={TIMEZONE_OPTIONS.map((option) => ({
                      value: option.value,
                      label: option.label,
                    }))}
                    onChange={(nextValue) => setForm((prev) => ({ ...prev, timezone: nextValue }))}
                    disabled={isRecurring}
                    wrapperClassName="ml-auto"
                  />
                </div>
              </div>
              <div className="flex items-center gap-2 px-1">
                <p className="text-[14px] font-medium text-[#4B5563]">반복</p>
                <button
                  type="button"
                  className="flex size-6 items-center justify-center text-slate-500 hover:text-primary disabled:opacity-50"
                  onClick={() =>
                    setForm((prev) => ({
                      ...prev,
                      recurrenceEnabled: !prev.recurrenceEnabled,
                    }))
                  }
                  disabled={isEdit}
                  aria-label={form.recurrenceEnabled ? "반복 일정 접기" : "반복 일정 펼치기"}
                >
                  {form.recurrenceEnabled ? (
                    <ChevronUp className="size-5" />
                  ) : (
                    <ChevronDown className="size-5" />
                  )}
                </button>
              </div>
              {form.recurrenceEnabled && (
                <div className="space-y-3">
                  {isEdit && (
                    <p className="text-[10px] text-slate-400">
                      기존 일정은 반복 규칙으로 전환할 수 없습니다.
                    </p>
                  )}
                    <div className="grid grid-cols-2 gap-3">
                      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-4 py-1 min-h-12 flex items-center">
                        <div className="flex w-full items-center justify-between gap-2">
                          <label className="text-[14px] font-medium text-[#4B5563] whitespace-nowrap shrink-0">
                            빈도
                          </label>
                          <CustomSelect
                            value={form.recurrenceFrequency}
                            options={[
                              { value: "DAILY", label: "매일" },
                              { value: "WEEKLY", label: "매주" },
                              { value: "MONTHLY", label: "매월" },
                              { value: "YEARLY", label: "매년" },
                            ]}
                            onChange={(nextValue) =>
                              setForm((prev) => ({
                                ...prev,
                                recurrenceFrequency: nextValue as EventRecurrence["freq"],
                              }))
                            }
                            disabled={isEdit}
                            wrapperClassName="ml-auto"
                          />
                        </div>
                      </div>
                      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-4 py-1 min-h-12 flex items-center">
                        <div className="flex w-full items-center justify-between gap-2">
                          <label className="text-[14px] font-medium text-[#4B5563] whitespace-nowrap shrink-0">
                            간격
                          </label>
                          <div className="ml-auto flex items-center gap-2">
                            <input
                              type="number"
                              min={1}
                              className="flex-none border-none bg-transparent px-2 py-2 text-[15px] font-medium text-[#111827] focus:outline-none focus:ring-0"
                              style={{ width: recurrenceIntervalWidth }}
                              value={form.recurrenceInterval}
                              onChange={(event) =>
                                setForm((prev) => ({
                                  ...prev,
                                  recurrenceInterval: Number(event.target.value || 1),
                                }))
                              }
                              disabled={isEdit}
                            />
                            {recurrenceIntervalUnit ? (
                              <span className="text-[14px] font-medium text-[#4B5563]">
                                {recurrenceIntervalUnit}
                              </span>
                            ) : null}
                          </div>
                        </div>
                      </div>
                    </div>

                    {form.recurrenceFrequency === "WEEKLY" && (
                      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-4 py-2 min-h-12 flex items-center">
                        <div className="flex w-full items-center justify-between gap-2">
                          <label className="text-[14px] font-medium text-[#374151] whitespace-nowrap shrink-0">
                            요일 선택
                          </label>
                          <div className="ml-auto flex flex-wrap justify-end gap-2">
                            {WEEKDAY_OPTIONS.map((day) => {
                              const active = form.recurrenceWeekdays.includes(day.value);
                              return (
                                <button
                                  key={day.value}
                                  type="button"
                                  className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
                                    active
                                      ? "bg-primary text-white border-primary"
                                      : "border-gray-200 text-slate-500 hover:border-primary/30"
                                  }`}
                                  onClick={() =>
                                    setForm((prev) => ({
                                      ...prev,
                                      recurrenceWeekdays: active
                                        ? prev.recurrenceWeekdays.filter((item) => item !== day.value)
                                        : [...prev.recurrenceWeekdays, day.value],
                                    }))
                                  }
                                  disabled={isEdit}
                                >
                                  {day.label}
                                </button>
                              );
                            })}
                          </div>
                        </div>
                      </div>
                    )}

                    {form.recurrenceFrequency === "MONTHLY" && (
                      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632]">
                        <div className="flex h-12 items-center px-4 py-2">
                          <span className="w-14 shrink-0 text-[14px] font-medium text-[#374151]">월간</span>
                          <div className="flex w-full items-center justify-end gap-2">
                            {(["date", "weekday"] as const).map((mode) => (
                              <button
                                key={mode}
                                type="button"
                                className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
                                  form.recurrenceMonthlyMode === mode
                                    ? "bg-primary text-white border-primary"
                                    : "border-gray-200 text-slate-500 hover:border-primary/30"
                                }`}
                                onClick={() =>
                                  setForm((prev) => ({ ...prev, recurrenceMonthlyMode: mode }))
                                }
                                disabled={isEdit}
                              >
                                {mode === "date" ? "같은 날짜" : "요일 기준"}
                              </button>
                            ))}
                          </div>
                        </div>
                        <div className="h-px bg-gray-200 dark:bg-gray-700 mx-4" />
                        {form.recurrenceMonthlyMode === "date" ? (
                          <div className="flex h-12 items-center px-4 py-2">
                            <span className="w-14 shrink-0 text-[14px] font-medium text-[#374151]">날짜</span>
                            <label className="sr-only">매월 날짜</label>
                            <div className="flex w-full items-center justify-end gap-1">
                              <input
                                type="number"
                                min={1}
                                max={31}
                                className="flex-none border-none bg-transparent px-2 py-2 text-[15px] font-medium text-[#111827] focus:outline-none focus:ring-0"
                                style={{ width: recurrenceMonthDayWidth }}
                                value={form.recurrenceMonthDay}
                                onChange={(event) =>
                                  setForm((prev) => ({
                                    ...prev,
                                    recurrenceMonthDay: Number(event.target.value || 1),
                                  }))
                                }
                                disabled={isEdit}
                              />
                              <span className="text-[14px] font-medium text-[#4B5563]">일</span>
                            </div>
                          </div>
                        ) : (
                          <div className="flex h-12 items-center px-4 py-2">
                            <span className="w-14 shrink-0 text-[14px] font-medium text-[#374151]">요일</span>
                            <label className="sr-only">월간 규칙 요일</label>
                            <div className="flex w-full items-center justify-end gap-2">
                              <CustomSelect
                                value={form.recurrenceWeekdayPos}
                                options={[
                                  { value: 1, label: "첫 번째" },
                                  { value: 2, label: "두 번째" },
                                  { value: 3, label: "세 번째" },
                                  { value: 4, label: "네 번째" },
                                  { value: -1, label: "마지막" },
                                ]}
                                onChange={(nextValue) =>
                                  setForm((prev) => ({
                                    ...prev,
                                    recurrenceWeekdayPos: Number(nextValue),
                                  }))
                                }
                                disabled={isEdit}
                              />
                              <CustomSelect
                                value={form.recurrenceWeekday}
                                options={WEEKDAY_OPTIONS.map((day) => ({
                                  value: day.value,
                                  label: `${day.label}요일`,
                                }))}
                                onChange={(nextValue) =>
                                  setForm((prev) => ({
                                    ...prev,
                                    recurrenceWeekday: Number(nextValue),
                                  }))
                                }
                                disabled={isEdit}
                              />
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {form.recurrenceFrequency === "YEARLY" && (
                      <div className="grid grid-cols-2 gap-3">
                        <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632]">
                          <div className="flex h-12 items-center justify-between gap-2 px-4 py-2">
                            <span className="text-[14px] font-medium text-[#374151]">날짜</span>
                            <label className="sr-only">월</label>
                            <div className="flex items-center justify-end gap-0">
                              <div className="flex items-center gap-1">
                                <CustomSelect
                                  value={form.recurrenceYearMonth}
                                  options={Array.from({ length: 12 }, (_, idx) => idx + 1).map(
                                    (month) => ({
                                      value: month,
                                      label: `${month}월`,
                                    })
                                  )}
                                  onChange={(nextValue) =>
                                    setForm((prev) => ({
                                      ...prev,
                                      recurrenceYearMonth: Number(nextValue),
                                    }))
                                  }
                                  disabled={isEdit}
                                />
                              </div>
                              <div className="flex items-center gap-1">
                                <label className="sr-only">일</label>
                                <CustomSelect
                                  value={form.recurrenceYearDay}
                                  options={Array.from({ length: 31 }, (_, idx) => idx + 1).map(
                                    (day) => ({
                                      value: day,
                                      label: `${day}일`,
                                    })
                                  )}
                                  onChange={(nextValue) =>
                                    setForm((prev) => ({
                                      ...prev,
                                      recurrenceYearDay: Number(nextValue),
                                    }))
                                  }
                                  disabled={isEdit}
                                />
                              </div>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}

                    <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-4 py-1 min-h-12 flex items-center">
                      <div className="flex w-full items-center justify-between gap-2">
                        <label className="text-[14px] font-medium text-[#374151]">종료</label>
                        <div className="ml-auto flex items-center gap-2">
                          <CustomSelect
                            value={form.recurrenceEndMode}
                            options={[
                              { value: "none", label: "없음" },
                              { value: "until", label: "날짜" },
                              { value: "count", label: "횟수" },
                            ]}
                            onChange={(nextValue) =>
                              setForm((prev) => ({ ...prev, recurrenceEndMode: nextValue }))
                            }
                            disabled={isEdit}
                          />
                          {form.recurrenceEndMode === "until" && (
                            <>
                              <label className="sr-only">종료 날짜</label>
                              <input
                                type="date"
                                className="w-auto flex-none border-none bg-transparent px-3 py-2 text-[15px] font-medium text-[#111827] focus:outline-none focus:ring-0"
                                value={form.recurrenceEndDate}
                                onChange={(event) =>
                                  setForm((prev) => ({ ...prev, recurrenceEndDate: event.target.value }))
                                }
                                disabled={isEdit}
                              />
                            </>
                          )}
                          {form.recurrenceEndMode === "count" && (
                            <>
                              <label className="sr-only">횟수</label>
                              <input
                                type="number"
                                min={1}
                                className="flex-none border-none bg-transparent px-2 py-2 text-[15px] font-medium text-[#111827] focus:outline-none focus:ring-0"
                                style={{ width: recurrenceCountWidth }}
                                value={form.recurrenceEndCount}
                                onChange={(event) =>
                                  setForm((prev) => ({ ...prev, recurrenceEndCount: event.target.value }))
                                }
                                disabled={isEdit}
                              />
                              <span className="text-[14px] font-medium text-[#4B5563]">회</span>
                            </>
                          )}
                        </div>
                      </div>
                    </div>
                    {recurrenceError && (
                      <p className="text-xs text-red-500">{recurrenceError}</p>
                    )}
                  </div>
                )}
            </>
          )}
        </div>
        <div className="flex items-center justify-between px-6 py-4 border-t border-gray-100 dark:border-gray-800">
          {stableEvent && !forceCreate ? (
            <button
              className="text-sm font-semibold text-red-500 hover:text-red-600"
              onClick={async () => {
                if (!stableEvent) return;
                await onDelete(stableEvent);
                onClose();
              }}
              type="button"
            >
              삭제
            </button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <button
              className="px-4 py-2 rounded-lg border text-[14px] font-semibold text-[#374151]"
              onClick={onClose}
              type="button"
            >
              취소
            </button>
            <button
              className="px-4 py-2 rounded-lg bg-primary text-[14px] font-semibold text-white disabled:opacity-50"
              onClick={async () => {
                if (isRecurring) return;
                if (form.recurrenceEnabled && recurringPayload && !isEdit) {
                  await onCreateRecurring(recurringPayload);
                  onClose();
                  return;
                }
                if (!forceCreate && stableEvent) {
                  await onUpdate(stableEvent, payload);
                } else {
                  await onCreate(payload);
                }
                onClose();
              }}
              type="button"
              disabled={Boolean(recurrenceError) || isRecurring}
            >
              저장
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
