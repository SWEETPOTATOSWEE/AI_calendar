"use client";

import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type AnimationEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { Calendar, ChevronLeft, ChevronRight } from "lucide-react";
import {
  addDays,
  addMonths,
  isSameDay,
  startOfMonth,
  startOfWeek,
  toISODate,
} from "../lib/date";

const getScrollParent = (node: HTMLElement | null) => {
  let current = node?.parentElement ?? null;
  while (current) {
    const { overflowY } = window.getComputedStyle(current);
    if (/(auto|scroll|hidden|overlay)/.test(overflowY)) return current;
    current = current.parentElement;
  }
  return document.body;
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

export type DatePopoverProps = {
  value: string;
  onChange: (value: string) => void;
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
  placeholder?: string;
  className?: string;
};

export const DatePopover = ({
  value,
  onChange,
  label,
  icon,
  disabled = false,
  placeholder = "날짜 선택",
  className = "",
}: DatePopoverProps) => {
  const disableAnimation = true;
  const [open, setOpen] = useState(false);
  const [scrollMode, setScrollMode] = useState(false);
  const [viewDate, setViewDate] = useState<Date>(() => parseISODate(value) ?? new Date());
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const [ready, setReady] = useState(false);
  const [openUp, setOpenUp] = useState(false);
  const [closing, setClosing] = useState(false);
  const wrapperRef = useRef<HTMLDivElement | null>(null);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const anchorOffsetRef = useRef<{ x: number; y: number } | null>(null);
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
      const maxLeft = window.innerWidth - popoverRect.width - 8;
      const preferredLeft = anchorX - popoverRect.width / 2;
      const left = Math.min(Math.max(preferredLeft, 8), maxLeft);
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
    if (disableAnimation) {
      setOpen(false);
      setClosing(false);
      setScrollMode(false);
      return;
    }
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
    <div ref={wrapperRef} className={`relative flex-none ${className}`}>
      <button
        type="button"
        className="flex w-full items-center gap-2 rounded-lg border border-border-subtle bg-bg-canvas px-3 py-1.5 text-[14px] font-medium text-text-primary disabled:opacity-60 disabled:cursor-not-allowed cursor-pointer transition-colors hover:border-border-strong"
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
        {icon && <span className="flex-none text-text-secondary">{icon}</span>}
        <span className={`truncate ${value ? "text-text-primary" : "text-text-disabled"}`}>
          {value || placeholder}
        </span>
      </button>
      {open &&
        !disabled &&
        typeof document !== "undefined" &&
        createPortal(
          <div
            ref={popoverRef}
            className={`fixed z-[9999] w-72 overflow-hidden popover-surface border border-border-subtle bg-bg-canvas p-4 shadow-lg ${
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
            <div className="flex items-center justify-between">
              <div className="flex items-center">
                <button
                  type="button"
                  className="flex h-7 items-center justify-center gap-0.5 pr-1 text-sm font-semibold text-text-primary hover:text-token-primary cursor-pointer"
                  onClick={() => setScrollMode((prev) => !prev)}
                  aria-label="월/년도 이동"
                >
                  <span>{formatYearMonthLabel(viewDate)}</span>
                  <ChevronRight
                    className={`size-4 translate-y-[1px] transition-transform ${
                      scrollMode ? "rotate-90" : ""
                    }`}
                  />
                </button>
              </div>
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  className="flex size-7 items-center justify-center rounded-full border border-border-subtle text-text-secondary hover:border-token-primary hover:text-token-primary cursor-pointer"
                  onClick={() => setViewDate((prev) => addMonths(prev, -1))}
                  aria-label="이전 달"
                >
                  <ChevronLeft className="size-4" />
                </button>
                <button
                  type="button"
                  className="flex size-7 items-center justify-center rounded-full border border-border-subtle text-text-secondary hover:border-token-primary hover:text-token-primary cursor-pointer"
                  onClick={() => setViewDate((prev) => addMonths(prev, 1))}
                  aria-label="다음 달"
                >
                  <ChevronRight className="size-4" />
                </button>
              </div>
            </div>
            {scrollMode && (
              <div className="mt-3 grid grid-cols-2 gap-3">
                <div className="max-h-41 overflow-y-auto rounded-lg border border-border-subtle bg-bg-canvas py-2">
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
                          active
                            ? "text-text-primary font-bold bg-bg-subtle"
                            : "text-text-secondary hover:text-text-primary"
                        }`}
                        onClick={() => setViewDate(new Date(year, viewDate.getMonth(), 1))}
                      >
                        {year}년
                      </button>
                    );
                  })}
                </div>
                <div className="max-h-41 overflow-y-auto rounded-lg border border-border-subtle bg-bg-canvas py-2">
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
                          active
                            ? "text-text-primary font-bold bg-bg-subtle"
                            : "text-text-secondary hover:text-text-primary"
                        }`}
                        onClick={() =>
                          setViewDate(new Date(viewDate.getFullYear(), month - 1, 1))
                        }
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
                <div className="mt-3 grid grid-cols-7 text-[11px] font-semibold text-text-disabled">
                  {["월", "화", "수", "목", "금", "토", "일"].map((day) => (
                    <span key={day} className="text-center">
                      {day}
                    </span>
                  ))}
                </div>
                <div className="mt-2 grid grid-cols-7 gap-1 text-[13px]">
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
                            ? "bg-bg-subtle text-text-primary font-bold ring-1 ring-border-strong"
                            : isToday
                              ? "border border-border-strong text-text-primary font-bold"
                              : isCurrentMonth
                                ? "text-text-primary hover:bg-bg-subtle"
                                : "text-text-disabled"
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
