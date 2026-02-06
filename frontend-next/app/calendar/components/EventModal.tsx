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
  FileText,
  Link,
  MapPin,
  Minus,
  Pencil,
  Plus,
  Users,
} from "lucide-react";
import { DatePopover } from "./DatePopover";
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

const COMPOSE_TABS = ["event", "task"] as const;
type ComposeMode = typeof COMPOSE_TABS[number];
const COMPOSE_LABELS: Record<ComposeMode, string> = {
  event: "일정",
  task: "할 일",
};

const COLOR_OPTIONS = [
  { id: "default", label: "블루", chip: "bg-token-primary" },
  { id: "1", label: "그린", chip: "bg-token-success" },
  { id: "2", label: "레드", chip: "bg-token-error" },
  { id: "3", label: "오렌지", chip: "bg-token-warning" },
  { id: "4", label: "청록", chip: "bg-token-info" },
];
const DEFAULT_COLOR_IDS = new Set(["default", "1", "2", "3", "4"]);

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

const calcEndTimeFromDuration = (startTime: string, durationMinutes: number) => {
  const start = new Date(`1970-01-01T${to24Hour(startTime)}`);
  if (Number.isNaN(start.getTime()) || !Number.isFinite(durationMinutes)) {
    return { endTime: startTime, dayOffset: 0 };
  }
  const end = new Date(start.getTime() + durationMinutes * 60000);
  const dayOffset = end.getDate() - start.getDate();
  const endTime = end.toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return { endTime, dayOffset };
};

const buildInitialState = (event?: CalendarEvent | null, defaultDate?: Date | null) => {
  const isRecurring = event?.recur === "recurring";
  const seed = parseISODateTime(event?.start) || defaultDate || new Date();
  const startDate = isRecurring && event?.start_date ? event.start_date : toISODate(seed);
  const startTimeSeed = formatTime(event?.start) || "09:00";
  const recurringStartTime = event?.time ? normalizeTime(event.time) : "";
  const startTime = isRecurring
    ? event?.all_day
      ? "00:00"
      : recurringStartTime || startTimeSeed
    : startTimeSeed;
  const endSeed = parseISODateTime(event?.end || "") || seed;
  let endDate = isRecurring ? startDate : toISODate(endSeed);
  let endTime = formatTime(event?.end) || "10:00";
  if (isRecurring) {
    if (event?.all_day) {
      endTime = "23:59";
    } else if (typeof event?.duration_minutes === "number") {
      const { endTime: computedEnd, dayOffset } = calcEndTimeFromDuration(
        startTime,
        event.duration_minutes
      );
      endTime = computedEnd;
      if (dayOffset) {
        const nextDate = addDays(new Date(startDate), dayOffset);
        endDate = toISODate(nextDate);
      }
    } else {
      endTime = startTime;
    }
  }
  const weekdayIndex = getWeekdayIndex(seed);
  const monthDay = seed.getDate();
  const weekdayPos = getWeekdayPos(seed);
  const recurrence = event?.recurrence || null;
  const recurrenceEnabled = Boolean(recurrence);
  const recurrenceFrequency = recurrenceEnabled ? recurrence?.freq || "WEEKLY" : "";
  const recurrenceInterval = recurrenceEnabled ? recurrence?.interval || 1 : "";
  const recurrenceWeekdays = recurrenceEnabled
    ? recurrence?.byweekday?.length
      ? recurrence.byweekday
      : [weekdayIndex]
    : [];
  const recurrenceMonthlyMode =
    recurrence?.freq === "MONTHLY" && recurrence?.bysetpos && recurrence?.byweekday?.length
      ? "weekday"
      : "date";
  const recurrenceMonthDay =
    recurrenceEnabled && recurrence?.bymonthday?.length && recurrence.bymonthday[0] !== -1
      ? recurrence.bymonthday[0]
      : monthDay;
  const recurrenceWeekday = recurrenceEnabled ? recurrence?.byweekday?.[0] ?? weekdayIndex : weekdayIndex;
  const recurrenceWeekdayPos = recurrenceEnabled ? recurrence?.bysetpos ?? weekdayPos : weekdayPos;
  const recurrenceYearMonth = recurrenceEnabled ? recurrence?.bymonth?.[0] ?? seed.getMonth() + 1 : seed.getMonth() + 1;
  const recurrenceYearDay =
    recurrenceEnabled && recurrence?.bymonthday?.length && recurrence.bymonthday[0] !== -1
      ? recurrence.bymonthday[0]
      : monthDay;
  const recurrenceEndMode = recurrenceEnabled
    ? recurrence?.end?.until
      ? "until"
      : recurrence?.end?.count
        ? "count"
        : "none"
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
    startTime,
    endDate,
    endTime,
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
    recurrenceEndDate: recurrenceEnabled ? recurrence?.end?.until || "" : "",
    recurrenceEndCount: recurrenceEnabled && recurrence?.end?.count ? String(recurrence.end.count) : "",
  };
};

export type EventModalProps = {
  open: boolean;
  event?: CalendarEvent | null;
  defaultDate?: Date | null;
  forceCreate?: boolean;
  variant?: "modal" | "drawer";
  showCloseButton?: boolean;
  resetKey?: number;
  composeMode?: ComposeMode;
  onComposeModeChange?: (mode: ComposeMode) => void;
  onClose: () => void;
  onCancel?: () => void;
  onCreate: (payload: EventPayload) => Promise<CalendarEvent | void | null>;
  onCreateRecurring: (payload: RecurringEventPayload) => Promise<CalendarEvent[] | null>;
  onUpdate: (event: CalendarEvent, payload: EventPayload) => Promise<void>;
  onUpdateRecurring: (event: CalendarEvent, payload: RecurringEventPayload) => Promise<void>;
  onDeleteOccurrence: (event: CalendarEvent) => Promise<void>;
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
          disabled ? "text-text-disabled" : "text-text-primary"
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
        <ChevronsUpDown className={`size-4 text-text-disabled ${iconClassName ?? ""}`} />
      </button>
      {open && !disabled && (
        <div
          className={`absolute left-1/2 z-20 min-w-full -translate-x-1/2 ${
            openUp ? "bottom-full mb-2" : "top-full mt-2"
          }`}
        >
          <div
            ref={menuRef}
            className={`min-w-full overflow-hidden popover-surface popover-animate border border-border-subtle bg-bg-canvas shadow-lg ${
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
                  className={`flex w-full justify-start rounded-md px-3 py-2 text-[15px] text-left transition-colors hover:bg-bg-subtle ${
                    active ? "font-semibold text-text-brand" : "font-medium text-text-primary"
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
  const disableAnimation = true;
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const selectedRef = useRef<HTMLButtonElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const anchorOffsetRef = useRef<{ x: number; y: number } | null>(null);
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
      const anchorOffset = anchorOffsetRef.current;
      const anchorX = rect.left + (anchorOffset?.x ?? rect.width / 2);
      const anchorY = rect.top + (anchorOffset?.y ?? rect.height);
      const spaceBelow = window.innerHeight - anchorY;
      const spaceAbove = anchorY;
      const shouldOpenUp = spaceBelow < popoverRect.height && spaceAbove > spaceBelow;
      const top = shouldOpenUp
        ? Math.max(8, anchorY - popoverRect.height - 8)
        : Math.min(window.innerHeight - popoverRect.height - 8, anchorY + 8);
      const left = Math.min(Math.max(anchorX - popoverRect.width / 2, 8), window.innerWidth - popoverRect.width - 8);
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
    if (disableAnimation) {
      setOpen(false);
      setClosing(false);
      setReady(false);
      return;
    }
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
        className="flex items-center justify-center gap-2 rounded-lg border border-border-subtle bg-bg-canvas px-3 py-1 text-[15px] font-medium text-text-primary disabled:opacity-60 disabled:cursor-not-allowed cursor-pointer"
        onClick={(event) => {
          if (open) {
            if (disableAnimation) {
              setOpen(false);
              setReady(false);
              return;
            }
            if (closing) {
              setClosing(false);
            } else {
              setClosing(true);
            }
            return;
          }
          const rect = wrapperRef.current?.getBoundingClientRect();
          if (rect) {
            const isKeyboard = event.clientX === 0 && event.clientY === 0;
            const offsetX = isKeyboard ? rect.width / 2 : event.clientX - rect.left;
            const offsetY = isKeyboard ? rect.height : event.clientY - rect.top;
            anchorOffsetRef.current = { x: offsetX, y: offsetY };
          }
          setReady(false);
          setClosing(false);
          setOpen(true);
        }}
        disabled={disabled}
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <span className={value ? "text-text-primary" : "text-text-disabled"}>
          {value ? formatTimeLabel(value) : placeholder}
        </span>
      </button>
      {open &&
        !disabled &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={popoverRef}
            className={`fixed z-[9999] w-40 overflow-hidden popover-surface border border-border-subtle bg-bg-canvas p-2 shadow-lg ${
              !disableAnimation && (ready || closing) ? "popover-animate" : ""
            } ${!disableAnimation && closing ? "is-closing" : ""}`}
            style={{
              top: position.top,
              left: position.left,
              visibility: ready ? "visible" : "hidden",
              pointerEvents: ready ? "auto" : "none",
            }}
            data-side={openUp ? "top" : "bottom"}
            data-align="end"
            onAnimationEnd={disableAnimation ? undefined : handlePopoverAnimationEnd}
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
                    active ? "bg-subtle text-text-primary font-bold" : "text-text-primary hover:bg-bg-subtle"
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
  variant = "modal",
  showCloseButton = true,
  resetKey = 0,
  composeMode = "event",
  onComposeModeChange,
  onClose,
  onCancel = onClose,
  onCreate,
  onCreateRecurring,
  onUpdate,
  onUpdateRecurring,
  onDeleteOccurrence,
  onDelete,
}: EventModalProps) {
  const { visible } = useAnimatedOpen(open);
  const [stableEvent, setStableEvent] = useState<CalendarEvent | null>(event ?? null);
  const [stableDefaultDate, setStableDefaultDate] = useState<Date | null>(defaultDate ?? null);
  const [form, setForm] = useState(() => buildInitialState(event, defaultDate));
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [showAllColors, setShowAllColors] = useState(false);
  const [descriptionMultiline, setDescriptionMultiline] = useState(false);
  const [deleteMenuOpen, setDeleteMenuOpen] = useState(false);
  const composeTabIndex = COMPOSE_TABS.indexOf(composeMode);
  const composeToggleStyle = useMemo(
    () =>
      ({
        "--seg-count": String(COMPOSE_TABS.length),
        "--seg-index": String(composeTabIndex),
      }) as CSSProperties,
    [composeTabIndex]
  );
  const isEdit = Boolean(stableEvent) && !forceCreate;
  const isRecurring = !forceCreate && stableEvent?.recur === "recurring";
  const descriptionRef = useRef<HTMLTextAreaElement | null>(null);
  const deleteMenuRef = useRef<HTMLDivElement | null>(null);
  const isDrawer = variant === "drawer";
  const lastResetKeyRef = useRef<number | null>(null);
  const resetFormState = (nextEvent: CalendarEvent | null, nextDefaultDate: Date | null) => {
    setStableEvent(nextEvent);
    setStableDefaultDate(nextDefaultDate);
    setForm(buildInitialState(nextEvent, nextDefaultDate));
    setAdvancedOpen(false);
    setShowAllColors(false);
    setDeleteMenuOpen(false);
  };
  const resizeDescription = () => {
    if (!descriptionRef.current) return;
    const lineHeight = parseFloat(getComputedStyle(descriptionRef.current).lineHeight || "0") || 22;
    const maxHeight = lineHeight * 12;
    descriptionRef.current.style.height = "auto";
    const nextHeight = Math.min(descriptionRef.current.scrollHeight, maxHeight);
    descriptionRef.current.style.height = `${nextHeight}px`;
    descriptionRef.current.style.overflowY =
      descriptionRef.current.scrollHeight > maxHeight ? "auto" : "hidden";
    const isLong = descriptionRef.current.scrollHeight > lineHeight * 1.6;
    const isShort = descriptionRef.current.scrollHeight < lineHeight * 1.2;
    setDescriptionMultiline((prev) => (prev ? !isShort : isLong));
  };

  useEffect(() => {
    if (!open) return;
    if (isDrawer) {
      if (lastResetKeyRef.current !== resetKey) {
        resetFormState(event ?? null, defaultDate ?? null);
        lastResetKeyRef.current = resetKey;
      }
      return;
    }
    resetFormState(event ?? null, defaultDate ?? null);
  }, [open, event, defaultDate, isDrawer, resetKey]);

  useEffect(() => {
    if (!deleteMenuOpen) return;
    const handleClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (deleteMenuRef.current && !deleteMenuRef.current.contains(target)) {
        setDeleteMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => {
      document.removeEventListener("mousedown", handleClick);
    };
  }, [deleteMenuOpen]);

  useLayoutEffect(() => {
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
  const handleClose = () => {
    onClose();
  };
  const handleCancel = () => {
    onCancel();
  };

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
    if (!form.recurrenceFrequency) return null;
    if (!form.recurrenceInterval) return null;
    const interval = Math.max(1, Number(form.recurrenceInterval) || 1);
    const rule: EventRecurrence = {
      freq: form.recurrenceFrequency as EventRecurrence["freq"],
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
    if (!form.recurrenceFrequency) return "반복 빈도를 선택해 주세요.";
    if (!form.recurrenceInterval) return "반복 간격을 입력해 주세요.";
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
    <div
      className={
        isDrawer
          ? "flex h-full flex-col"
          : "fixed inset-0 z-[999] flex items-center justify-center bg-black/40 md:px-4"
      }
    >
      <div
        className={
          isDrawer
            ? "flex h-full flex-col bg-bg-surface"
            : "w-full h-full md:h-auto md:max-w-2xl md:rounded-2xl bg-bg-surface border border-border-subtle shadow-xl flex flex-col"
        }
      >
        <div
          className={
            isDrawer
              ? "flex items-center justify-between px-3 py-3 border-b border-border-subtle"
              : "flex items-center justify-between px-6 py-4 border-b border-border-subtle shrink-0"
          }
        >
          <div className="flex items-center gap-3">
            <div
              className="relative flex items-center rounded-full bg-bg-subtle p-1 text-xs segmented-toggle"
              style={composeToggleStyle}
            >
              <span className="segmented-indicator">
                <span
                  key={composeMode}
                  className="view-indicator-pulse block h-full w-full rounded-full bg-bg-surface shadow-sm"
                />
              </span>
              {COMPOSE_TABS.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  className={`relative z-10 flex-1 px-3 py-1 text-[14px] transition-all ${
                    composeMode === tab
                      ? "text-text-brand !font-bold"
                      : "text-text-secondary font-medium hover:text-text-brand"
                  }`}
                  onClick={() => onComposeModeChange?.(tab)}
                >
                  {COMPOSE_LABELS[tab]}
                </button>
              ))}
            </div>
          </div>
          {showCloseButton && (
            <button
              className="text-text-disabled hover:text-text-primary"
              onClick={onClose}
              type="button"
            >
              ✕
            </button>
          )}
        </div>
        <div
          className={
            isDrawer
              ? "flex-1 px-3 py-3 space-y-4 overflow-y-auto"
              : "flex-1 md:flex-none px-6 py-5 space-y-4 overflow-y-auto md:max-h-[70vh]"
          }
        >
              <div className="rounded-lg border border-border-subtle bg-bg-canvas">
                <div className="flex min-h-12 items-center px-4">
                  <label className="sr-only">제목</label>
                  <input
                    className="h-10 w-full -translate-y-[1px] appearance-none border-none bg-transparent py-0 text-[15px] leading-none font-medium text-text-primary placeholder:text-[15px] placeholder:font-normal placeholder:text-text-disabled focus:outline-none focus:ring-0"
                    placeholder="제목"
                    autoComplete="off"
                    value={form.title}
                    onChange={(event) => setForm((prev) => ({ ...prev, title: event.target.value }))}
                  />
                </div>
              </div>
              <div className="rounded-lg border border-border-subtle bg-bg-canvas">
                <div className="flex h-12 items-center justify-between px-4 py-2 text-[14px] font-medium text-text-secondary">
                  <span>하루종일</span>
                  <div className="ml-auto flex justify-end">
                    <label className="relative inline-flex size-6 items-center justify-center">
                    <input
                      className="peer size-6 appearance-none rounded-full border border-border-subtle bg-bg-canvas shadow-sm transition-colors checked:border-bg-brand checked:bg-bg-brand focus:outline-none focus-visible:ring-2 focus-visible:ring-bg-brand/20 disabled:opacity-50"
                      type="checkbox"
                      checked={form.allDay}
                      onChange={(event) => setForm((prev) => ({ ...prev, allDay: event.target.checked }))}
                    />
                    <span className="pointer-events-none absolute text-text-disabled transition-colors peer-checked:text-text-on-brand">
                      <Check className="size-4" />
                    </span>
                    </label>
                  </div>
                </div>
                <div className="h-px bg-border-subtle mx-4" />
                <div className="flex h-12 items-center px-4 py-2">
                  <span className="w-7 shrink-0 text-[14px] font-medium text-text-primary">시작</span>
                  <label className="sr-only">시작 날짜</label>
                  <div className="flex items-center gap-2 ml-auto">
                    <DatePopover
                      label="시작 날짜"
                      icon={<Calendar className="w-4 h-4" />}
                      value={form.startDate}
                      onChange={(value) => setForm((prev) => ({ ...prev, startDate: value }))}
                      placeholder="날짜 선택"
                    />
                    {!form.allDay && (
                      <TimePopover
                        label="시작 시간"
                        icon={<Clock className="w-4 h-4" />}
                        value={form.startTime}
                        onChange={(value) =>
                          setForm((prev) => ({ ...prev, startTime: normalizeTime(value) }))
                        }
                        placeholder="시간 선택"
                      />
                    )}
                  </div>
                </div>
                <div className="h-px bg-border-subtle mx-4" />
                <div className="flex h-12 items-center px-4 py-2">
                  <span className="w-7 shrink-0 text-[14px] font-medium text-text-primary">종료</span>
                  <label className="sr-only">종료 날짜</label>
                  <div className="flex items-center gap-2 ml-auto">
                    <DatePopover
                      label="종료 날짜"
                      icon={<Calendar className="w-4 h-4" />}
                      value={form.endDate}
                      onChange={(value) => setForm((prev) => ({ ...prev, endDate: value }))}
                      placeholder="날짜 선택"
                    />
                    {!form.allDay && (
                      <TimePopover
                        label="종료 시간"
                        icon={<Clock className="w-4 h-4" />}
                        value={form.endTime}
                        onChange={(value) =>
                          setForm((prev) => ({ ...prev, endTime: normalizeTime(value) }))
                        }
                        placeholder="시간 선택"
                      />
                    )}
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-border-subtle bg-bg-canvas">
                <div className="flex min-h-12 items-center gap-2 px-4">
                  <MapPin className="size-4 text-text-secondary" />
                  <label className="sr-only">장소</label>
                  <input
                    className="h-10 w-full -translate-y-[1px] appearance-none border-none bg-transparent py-0 text-[15px] leading-none font-medium text-text-primary placeholder:text-[15px] placeholder:font-normal placeholder:text-text-disabled focus:outline-none focus:ring-0"
                    placeholder="장소"
                    value={form.location}
                    onChange={(event) => setForm((prev) => ({ ...prev, location: event.target.value }))}
                  />
                </div>
              </div>
              <div className="rounded-lg border border-border-subtle bg-bg-canvas px-4 min-h-12 flex items-center">
                <div className="flex w-full items-center justify-between gap-2">
                  <label className="text-[14px] font-medium text-text-primary whitespace-nowrap shrink-0">알림</label>
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
                        className={`px-3 py-1 rounded-full text-[13px] transition-colors ${
                          active
                            ? "text-text-brand font-bold underline"
                            : "text-text-secondary font-medium"
                        }`}
                        onClick={() =>
                          setForm((prev) => ({
                            ...prev,
                            reminders: active
                              ? prev.reminders.filter((item) => item !== value)
                              : [...prev.reminders, value],
                          }))
                        }
                      >
                        {label}
                      </button>
                    );
                  });
                  })()}
                  <div className="relative">
                    <button
                      type="button"
                      className="flex size-7 items-center justify-center rounded-full border border-border-subtle text-text-disabled hover:border-token-primary/40"
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
                      aria-expanded={customReminderOpen}
                      aria-haspopup="dialog"
                      aria-label="알림 직접 추가"
                    >
                      <Plus className="size-4" />
                    </button>
                    {customReminderOpen && (
                      <div
                        className={`absolute right-0 bottom-full mb-2 w-52 max-w-[calc(100vw-2rem)] popover-surface popover-animate border border-border-subtle bg-bg-canvas shadow-lg p-3 z-10 ${
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
                            className="w-20 rounded-lg border border-border-subtle bg-bg-canvas px-2 py-1 text-xs"
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
                            className="px-2 py-1 text-xs text-text-disabled"
                            onClick={closeCustomReminder}
                          >
                            취소
                          </button>
                          <button
                            type="button"
                            className="px-3 py-1 rounded-full text-xs font-semibold bg-token-primary text-white"
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
              <div className="rounded-lg border border-border-subtle bg-bg-canvas px-4 py-2 min-h-12 flex items-center">
                <div className="flex w-full items-center justify-between gap-2">
                  <label className="text-[14px] font-medium text-text-primary whitespace-nowrap shrink-0">색상</label>
                  <div className="ml-auto flex flex-wrap items-center justify-end gap-1">
                    {visibleColorOptions.map((option) => {
                      const active = form.colorId === option.id;
                      return (
                        <button
                          key={option.id}
                          type="button"
                          className={`flex items-center rounded-full p-1 text-[13px] font-medium transition-colors ${
                            active
                              ? "text-text-brand font-semibold"
                              : "text-text-secondary"
                          }`}
                          onClick={() => setForm((prev) => ({ ...prev, colorId: option.id }))}
                          aria-label={option.label}
                          title={option.label}
                        >
                          <span
                            className={`relative rounded-full ${option.chip} ${
                              active ? "h-6 w-6" : "h-5 w-5"
                            }`}
                          >
                            {active ? (
                              <Check className="absolute inset-0 m-auto size-3 text-black" />
                            ) : null}
                          </span>
                        </button>
                      );
                    })}
                    {!showAllColors && (
                      <button
                        type="button"
                        className="relative size-8 rounded-full text-text-secondary transition-colors hover:text-text-brand"
                        onClick={() => setShowAllColors(true)}
                        aria-label="모든 색상"
                        title="모든 색상"
                      >
                        <Plus className="absolute inset-0 m-auto size-4" />
                      </button>
                    )}
                    {showAllColors && (
                      <button
                        type="button"
                        className="relative size-8 rounded-full text-text-secondary transition-colors hover:text-text-brand"
                        onClick={() => setShowAllColors(false)}
                        aria-label="닫기"
                        title="닫기"
                      >
                        <Minus className="absolute inset-0 m-auto size-4" />
                      </button>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-2 px-1">
                <p className="text-[14px] font-medium text-text-primary">반복</p>
                <button
                  type="button"
                  className="flex size-6 items-center justify-center text-text-disabled hover:text-token-primary disabled:opacity-50"
                  onClick={() =>
                    setForm((prev) => ({
                      ...prev,
                      recurrenceEnabled: !prev.recurrenceEnabled,
                    }))
                  }
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
                  <div className="grid grid-cols-2 gap-3">
                    <div className="rounded-lg border border-border-subtle bg-bg-canvas px-4 py-1 min-h-12 flex items-center">
                      <div className="flex w-full items-center justify-between gap-2">
                        <label className="text-[14px] font-medium text-text-secondary whitespace-nowrap shrink-0">
                          빈도
                        </label>
                        <CustomSelect
                          value={form.recurrenceFrequency}
                          options={[
                            { value: "", label: "선택" },
                            { value: "DAILY", label: "매일" },
                            { value: "WEEKLY", label: "매주" },
                            { value: "MONTHLY", label: "매월" },
                            { value: "YEARLY", label: "매년" },
                          ]}
                          onChange={(nextValue) =>
                            setForm((prev) => ({
                              ...prev,
                              recurrenceFrequency: nextValue as EventRecurrence["freq"] | "",
                            }))
                          }
                          wrapperClassName="ml-auto"
                        />
                      </div>
                    </div>
                    <div className="rounded-lg border border-border-subtle bg-bg-canvas px-4 py-1 min-h-12 flex items-center">
                      <div className="flex w-full items-center justify-between gap-2">
                        <label className="text-[14px] font-medium text-text-secondary whitespace-nowrap shrink-0">
                          간격
                        </label>
                        <div className="ml-auto flex items-center gap-2">
                          <input
                            type="number"
                            min={1}
                            className="flex-none border-none bg-transparent px-2 py-2 text-[15px] font-medium text-text-primary focus:outline-none focus:ring-0"
                            style={{ width: recurrenceIntervalWidth }}
                            value={form.recurrenceInterval}
                            onChange={(event) =>
                              setForm((prev) => ({
                                ...prev,
                                recurrenceInterval: event.target.value
                                  ? Number(event.target.value)
                                  : "",
                              }))
                            }
                          />
                          {recurrenceIntervalUnit ? (
                            <span className="text-[14px] font-medium text-text-secondary">
                              {recurrenceIntervalUnit}
                            </span>
                          ) : null}
                        </div>
                      </div>
                    </div>
                  </div>

                  {form.recurrenceFrequency === "WEEKLY" && (
                    <div className="rounded-lg border border-border-subtle bg-bg-canvas px-4 py-2 min-h-12 flex items-center">
                      <div className="flex w-full items-center justify-between gap-2">
                        <label className="text-[14px] font-medium text-text-primary whitespace-nowrap shrink-0">
                          요일 선택
                        </label>
                        <div className="ml-auto flex flex-wrap justify-end gap-2">
                          {WEEKDAY_OPTIONS.map((day) => {
                            const active = form.recurrenceWeekdays.includes(day.value);
                            return (
                              <button
                                key={day.value}
                                type="button"
                                className={`px-3 py-1 rounded-full text-xs border transition-colors ${
                                  active
                                    ? "bg-bg-brand text-white border-border-brand font-bold"
                                    : "border-border-subtle text-text-disabled font-semibold hover:border-border-brand/30"
                                }`}
                                onClick={() =>
                                  setForm((prev) => ({
                                    ...prev,
                                    recurrenceWeekdays: active
                                      ? prev.recurrenceWeekdays.filter((item) => item !== day.value)
                                      : [...prev.recurrenceWeekdays, day.value],
                                  }))
                                }
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
                    <div className="rounded-lg border border-border-subtle bg-bg-canvas">
                      <div className="flex h-12 items-center px-4 py-2">
                        <span className="w-14 shrink-0 text-[14px] font-medium text-text-primary">월간</span>
                        <div className="flex w-full items-center justify-end gap-2">
                          {(["date", "weekday"] as const).map((mode) => (
                            <button
                              key={mode}
                              type="button"
                              className={`px-3 py-1 rounded-full text-xs border transition-colors ${
                                form.recurrenceMonthlyMode === mode
                                  ? "bg-bg-brand text-white border-border-brand font-bold"
                                  : "border-border-subtle text-text-disabled font-semibold hover:border-border-brand/30"
                              }`}
                              onClick={() =>
                                setForm((prev) => ({ ...prev, recurrenceMonthlyMode: mode }))
                              }
                            >
                              {mode === "date" ? "같은 날짜" : "요일 기준"}
                            </button>
                          ))}
                        </div>
                      </div>
                      <div className="h-px bg-border-subtle mx-4" />
                      {form.recurrenceMonthlyMode === "date" ? (
                        <div className="flex h-12 items-center px-4 py-2">
                          <span className="w-14 shrink-0 text-[14px] font-medium text-text-primary">날짜</span>
                          <label className="sr-only">매월 날짜</label>
                          <div className="flex w-full items-center justify-end gap-1">
                            <input
                              type="number"
                              min={1}
                              max={31}
                              className="flex-none border-none bg-transparent px-2 py-2 text-[15px] font-medium text-text-primary focus:outline-none focus:ring-0"
                              style={{ width: recurrenceMonthDayWidth }}
                              value={form.recurrenceMonthDay}
                              onChange={(event) =>
                                setForm((prev) => ({
                                  ...prev,
                                  recurrenceMonthDay: Number(event.target.value || 1),
                                }))
                              }
                            />
                            <span className="text-[14px] font-medium text-text-secondary">일</span>
                          </div>
                        </div>
                      ) : (
                        <div className="flex h-12 items-center px-4 py-2">
                          <span className="w-14 shrink-0 text-[14px] font-medium text-text-primary">요일</span>
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
                            />
                          </div>
                        </div>
                      )}
                    </div>
                  )}

                  {form.recurrenceFrequency === "YEARLY" && (
                    <div className="grid grid-cols-2 gap-3">
                      <div className="rounded-lg border border-border-subtle bg-bg-canvas">
                        <div className="flex h-12 items-center justify-between gap-2 px-4 py-2">
                          <span className="text-[14px] font-medium text-text-primary">날짜</span>
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
                              />
                            </div>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}

                  <div className="rounded-lg border border-border-subtle bg-bg-canvas px-4 py-1 min-h-12 flex items-center">
                    <div className="flex w-full items-center justify-between gap-2">
                      <label className="text-[14px] font-medium text-text-primary">종료</label>
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
                        />
                        {form.recurrenceEndMode === "until" && (
                          <>
                            <label className="sr-only">종료 날짜</label>
                            <input
                              type="date"
                              className="w-auto flex-none border-none bg-transparent px-3 py-2 text-[15px] font-medium text-text-primary focus:outline-none focus:ring-0"
                              value={form.recurrenceEndDate}
                              onChange={(event) =>
                                setForm((prev) => ({ ...prev, recurrenceEndDate: event.target.value }))
                              }
                            />
                          </>
                        )}
                        {form.recurrenceEndMode === "count" && (
                          <>
                            <label className="sr-only">횟수</label>
                            <input
                              type="number"
                              min={1}
                              className="flex-none border-none bg-transparent px-2 py-2 text-[15px] font-medium text-text-primary focus:outline-none focus:ring-0"
                              style={{ width: recurrenceCountWidth }}
                              value={form.recurrenceEndCount}
                              onChange={(event) =>
                                setForm((prev) => ({ ...prev, recurrenceEndCount: event.target.value }))
                              }
                            />
                            <span className="text-[14px] font-medium text-text-secondary">회</span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                  {recurrenceError && (
                    <p className="text-xs text-token-error">{recurrenceError}</p>
                  )}
                </div>
              )}
              <div className="flex items-center gap-2 px-1">
                <p className="text-[14px] font-medium text-text-primary">고급</p>
                <button
                  type="button"
                  className="flex size-6 items-center justify-center text-text-disabled hover:text-token-primary disabled:opacity-50"
                  onClick={() => setAdvancedOpen((prev) => !prev)}
                  aria-label={advancedOpen ? "고급 옵션 접기" : "고급 옵션 펼치기"}
                >
                  {advancedOpen ? <ChevronUp className="size-5" /> : <ChevronDown className="size-5" />}
                </button>
              </div>
              {advancedOpen && (
                <>
              <div className="rounded-lg border border-border-subtle bg-bg-canvas">
                <div
                  className={`flex min-h-12 gap-2 px-4 py-2 ${
                    descriptionMultiline ? "items-start" : "items-center"
                  }`}
                >
                  <FileText className={`size-4 text-text-disabled ${descriptionMultiline ? "mt-1" : ""}`} />
                  <label className="sr-only">설명</label>
                  <textarea
                    ref={descriptionRef}
                    rows={1}
                    className="w-full resize-none border-none bg-transparent text-[15px] font-medium text-text-primary placeholder:text-[15px] placeholder:font-normal placeholder:text-text-disabled focus:outline-none focus:ring-0"
                    placeholder="설명"
                    value={form.description}
                    onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
                  />
                </div>
              </div>
              <div className="rounded-lg border border-border-subtle bg-bg-canvas">
                <div className="flex h-12 items-center gap-2 px-4 py-2">
                  <Users className="size-4 text-text-disabled" />
                  <label className="sr-only">참석자</label>
                  <input
                    className="w-full -translate-y-[1px] border-none bg-transparent text-[15px] font-medium text-text-primary placeholder:text-[15px] placeholder:font-normal placeholder:text-text-disabled focus:outline-none focus:ring-0"
                    placeholder="참석자"
                    value={form.attendees}
                    onChange={(event) => setForm((prev) => ({ ...prev, attendees: event.target.value }))}
                  />
                </div>
                <div className="h-px bg-border-subtle mx-4" />
                <div className="flex h-12 items-center gap-2 px-4 py-2">
                  <Link className="size-4 text-text-disabled" />
                  <label className="sr-only">회의 링크</label>
                  <input
                    className="w-full -translate-y-[1px] border-none bg-transparent text-[15px] font-medium text-text-primary placeholder:text-[15px] placeholder:font-normal placeholder:text-text-disabled focus:outline-none focus:ring-0"
                    placeholder="회의 링크"
                    value={form.meetingUrl}
                    onChange={(event) => setForm((prev) => ({ ...prev, meetingUrl: event.target.value }))}
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-border-subtle bg-bg-canvas px-4 py-1 min-h-12 flex items-center">
                  <div className="flex w-full items-center justify-between gap-2">
                    <span className="text-[14px] font-medium text-text-primary whitespace-nowrap shrink-0">공개</span>
                    <label className="sr-only">공개 범위</label>
                    <CustomSelect
                      value={form.visibility}
                      options={[
                        { value: "default", label: "기본" },
                        { value: "public", label: "공개" },
                        { value: "private", label: "비공개" },
                      ]}
                      onChange={(nextValue) => setForm((prev) => ({ ...prev, visibility: nextValue }))}
                      wrapperClassName="ml-auto"
                    />
                  </div>
                </div>
                <div className="rounded-lg border border-border-subtle bg-bg-canvas px-4 py-1 min-h-12 flex items-center">
                  <div className="flex w-full items-center justify-between gap-2">
                    <span className="text-[14px] font-medium text-text-primary whitespace-nowrap shrink-0">표시</span>
                    <label className="sr-only">표시 상태</label>
                    <CustomSelect
                      value={form.transparency}
                      options={[
                        { value: "opaque", label: "바쁨" },
                        { value: "transparent", label: "한가함" },
                      ]}
                      onChange={(nextValue) => setForm((prev) => ({ ...prev, transparency: nextValue }))}
                      wrapperClassName="ml-auto"
                    />
                  </div>
                </div>
              </div>
              <div className="rounded-lg border border-border-subtle bg-bg-canvas px-4 py-1 min-h-12 flex items-center">
                <div className="flex w-full items-center justify-between gap-2">
                  <label className="text-[14px] font-medium text-text-primary whitespace-nowrap shrink-0">
                    시간대
                  </label>
                  <CustomSelect
                    value={form.timezone}
                    options={TIMEZONE_OPTIONS.map((option) => ({
                      value: option.value,
                      label: option.label,
                    }))}
                    onChange={(nextValue) => setForm((prev) => ({ ...prev, timezone: nextValue }))}
                    wrapperClassName="ml-auto"
                  />
                </div>
              </div>
                </>
              )}
        </div>
        <div
          className={
            isDrawer
              ? "flex items-center justify-between px-3 py-3 border-t border-border-subtle"
              : "flex items-center justify-between px-6 py-4 border-t border-border-subtle shrink-0"
          }
        >
          {stableEvent && !forceCreate ? (
            <div className="relative" ref={deleteMenuRef}>
              <button
                className="text-sm font-semibold text-token-error hover:opacity-80"
                onClick={async () => {
                  if (!stableEvent) return;
                  if (isRecurring) {
                    setDeleteMenuOpen((prev) => !prev);
                    return;
                  }
                  await onDelete(stableEvent);
                  handleClose();
                }}
                type="button"
              >
                삭제
              </button>
              {isRecurring && deleteMenuOpen && (
                <div className="absolute left-0 bottom-full mb-2 z-50 w-max">
                  <div
                    className="min-w-full overflow-hidden whitespace-nowrap popover-surface popover-animate border border-border-subtle bg-bg-canvas shadow-lg"
                    data-side="top"
                    data-align="start"
                  >
                    <button
                      type="button"
                      className="flex w-full justify-start rounded-md px-3 py-2 text-[15px] text-left font-medium text-text-primary transition-colors hover:bg-bg-subtle"
                      onClick={async () => {
                        if (!stableEvent) return;
                        await onDeleteOccurrence(stableEvent);
                        setDeleteMenuOpen(false);
                        handleClose();
                      }}
                    >
                      이 일정만 삭제
                    </button>
                    <button
                      type="button"
                      className="flex w-full justify-start rounded-md px-3 py-2 text-[15px] text-left font-medium text-token-error transition-colors hover:bg-token-error/10"
                      onClick={async () => {
                        if (!stableEvent) return;
                        await onDelete(stableEvent);
                        setDeleteMenuOpen(false);
                        handleClose();
                      }}
                    >
                      반복 일정 전체 삭제
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <button
              className="px-4 py-2 rounded-lg border border-border-subtle text-[14px] font-semibold text-text-primary"
              onClick={handleCancel}
              type="button"
            >
              취소
            </button>
            <button
              className="px-4 py-2 rounded-lg bg-bg-brand text-[14px] font-semibold text-white disabled:opacity-50"
              onClick={async () => {
                if (form.recurrenceEnabled && recurringPayload && !isEdit) {
                  await onCreateRecurring(recurringPayload);
                  handleClose();
                  return;
                }
                if (!forceCreate && stableEvent) {
                  if (form.recurrenceEnabled && recurringPayload) {
                    if (isRecurring) {
                      await onUpdateRecurring(stableEvent, recurringPayload);
                    } else {
                      const created = await onCreateRecurring(recurringPayload);
                      if (created && created.length) {
                        await onDelete(stableEvent);
                      }
                    }
                  } else if (isRecurring) {
                    const created = await onCreate(payload);
                    if (created) {
                      await onDelete(stableEvent);
                    }
                  } else {
                    await onUpdate(stableEvent, payload);
                  }
                } else {
                  await onCreate(payload);
                }
                handleClose();
              }}
              type="button"
              disabled={Boolean(recurrenceError)}
            >
              저장
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
