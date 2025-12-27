"use client";

import { useEffect, useMemo, useState } from "react";
import type {
  CalendarEvent,
  EventPayload,
  EventRecurrence,
  RecurringEventPayload,
} from "../lib/types";
import { formatTime, parseISODateTime, toISODate } from "../lib/date";

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
  onClose: () => void;
  onCreate: (payload: EventPayload) => Promise<CalendarEvent | void | null>;
  onCreateRecurring: (payload: RecurringEventPayload) => Promise<CalendarEvent[] | null>;
  onUpdate: (event: CalendarEvent, payload: EventPayload) => Promise<void>;
  onDelete: (event: CalendarEvent) => Promise<void>;
};

const ANIMATION_MS = 260;

const useAnimatedOpen = (open: boolean) => {
  const [visible, setVisible] = useState(open);
  const [closing, setClosing] = useState(false);

  useEffect(() => {
    if (open) {
      setVisible(true);
      setClosing(false);
      return;
    }
    if (visible) {
      setClosing(true);
      const timer = setTimeout(() => {
        setVisible(false);
        setClosing(false);
      }, ANIMATION_MS);
      return () => clearTimeout(timer);
    }
  }, [open, visible]);

  return { visible, closing };
};

export default function EventModal({
  open,
  event,
  defaultDate,
  onClose,
  onCreate,
  onCreateRecurring,
  onUpdate,
  onDelete,
}: EventModalProps) {
  const { visible, closing } = useAnimatedOpen(open);
  const [stableEvent, setStableEvent] = useState<CalendarEvent | null>(event ?? null);
  const [stableDefaultDate, setStableDefaultDate] = useState<Date | null>(defaultDate ?? null);
  const [form, setForm] = useState(() => buildInitialState(event, defaultDate));
  const [activeTab, setActiveTab] = useState<"basic" | "advanced">("basic");
  const isEdit = Boolean(stableEvent);
  const isRecurring = stableEvent?.recur === "recurring";

  useEffect(() => {
    if (!open) return;
    setStableEvent(event ?? null);
    setStableDefaultDate(defaultDate ?? null);
    setForm(buildInitialState(event, defaultDate));
    setActiveTab("basic");
  }, [open, event, defaultDate]);

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
      className={`fixed inset-0 z-[999] flex items-center justify-center bg-black/40 px-4 ${
        closing ? "animate-overlayOut" : "animate-overlayIn"
      }`}
    >
      <div
        className={`w-full max-w-2xl rounded-2xl bg-white dark:bg-[#111418] border border-gray-100 dark:border-gray-800 shadow-xl ${
          closing ? "animate-modalOut" : "animate-modalIn"
        }`}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-800">
          <div className="flex items-center gap-3">
            <h3 className="text-lg font-bold text-slate-900 dark:text-white">
            {isEdit ? "일정 수정" : "일정 추가"}
            </h3>
            <div className="flex items-center gap-1 rounded-full bg-gray-100 dark:bg-gray-800 p-1 text-xs">
              {(["basic", "advanced"] as const).map((tab) => (
                <button
                  key={tab}
                  type="button"
                  className={`px-3 py-1 rounded-full transition-all ${
                    activeTab === tab
                      ? "bg-white dark:bg-gray-700 text-slate-900 dark:text-white shadow-sm"
                      : "text-slate-500 dark:text-gray-400 hover:text-slate-900 dark:hover:text-white"
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
              <div>
                <label className="text-xs font-semibold text-slate-500">제목</label>
                <input
                  className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                  value={form.title}
                  onChange={(event) => setForm((prev) => ({ ...prev, title: event.target.value }))}
                  disabled={isRecurring}
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-500">색상</label>
                <div className="mt-2 flex flex-wrap gap-2">
                  {COLOR_OPTIONS.map((option) => {
                    const active = form.colorId === option.id;
                    return (
                      <button
                        key={option.id}
                        type="button"
                        className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-semibold transition-colors ${
                          active
                            ? "border-primary text-primary"
                            : "border-gray-200 text-slate-500 hover:border-primary/30"
                        }`}
                        onClick={() => setForm((prev) => ({ ...prev, colorId: option.id }))}
                        disabled={isRecurring}
                      >
                        <span className={`h-2.5 w-2.5 rounded-full ${option.chip}`}></span>
                        {option.label}
                      </button>
                    );
                  })}
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-semibold text-slate-500">시작 날짜</label>
                  <input
                    type="date"
                    className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                    value={form.startDate}
                    onChange={(event) => setForm((prev) => ({ ...prev, startDate: event.target.value }))}
                    disabled={isRecurring}
                  />
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-500">시작 시간</label>
                  <input
                    type="time"
                    className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                    value={form.startTime}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, startTime: normalizeTime(event.target.value) }))
                    }
                    disabled={form.allDay || isRecurring}
                  />
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-500">종료 날짜</label>
                  <input
                    type="date"
                    className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                    value={form.endDate}
                    onChange={(event) => setForm((prev) => ({ ...prev, endDate: event.target.value }))}
                    disabled={isRecurring}
                  />
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-500">종료 시간</label>
                  <input
                    type="time"
                    className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                    value={form.endTime}
                    onChange={(event) =>
                      setForm((prev) => ({ ...prev, endTime: normalizeTime(event.target.value) }))
                    }
                    disabled={form.allDay || isRecurring}
                  />
                </div>
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-500">장소</label>
                <input
                  className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                  value={form.location}
                  onChange={(event) => setForm((prev) => ({ ...prev, location: event.target.value }))}
                  disabled={isRecurring}
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-500">알림</label>
                <div className="mt-2 flex flex-wrap gap-2">
                  {REMINDER_OPTIONS.map((option) => {
                    const active = form.reminders.includes(option.value);
                    return (
                      <button
                        key={option.value}
                        type="button"
                        className={`px-3 py-1 rounded-full text-xs font-semibold border transition-colors ${
                          active
                            ? "bg-primary text-white border-primary"
                            : "border-gray-200 text-slate-500 hover:border-primary/30"
                        }`}
                        onClick={() =>
                          setForm((prev) => ({
                            ...prev,
                            reminders: active
                              ? prev.reminders.filter((item) => item !== option.value)
                              : [...prev.reminders, option.value],
                          }))
                        }
                        disabled={isRecurring}
                      >
                        {option.label}
                      </button>
                    );
                  })}
                </div>
              </div>
              <label className="flex items-center gap-2 text-xs text-slate-500">
                <input
                  type="checkbox"
                  checked={form.allDay}
                  onChange={(event) => setForm((prev) => ({ ...prev, allDay: event.target.checked }))}
                  disabled={isRecurring}
                />
                종일
              </label>
            </>
          )}

          {activeTab === "advanced" && (
            <>
              <div>
                <label className="text-xs font-semibold text-slate-500">설명</label>
                <textarea
                  rows={3}
                  className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                  value={form.description}
                  onChange={(event) => setForm((prev) => ({ ...prev, description: event.target.value }))}
                  disabled={isRecurring}
                />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-semibold text-slate-500">참석자</label>
                  <input
                    className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                    placeholder="email1, email2"
                    value={form.attendees}
                    onChange={(event) => setForm((prev) => ({ ...prev, attendees: event.target.value }))}
                    disabled={isRecurring}
                  />
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-500">회의 링크</label>
                  <input
                    className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                    placeholder="https://meet.google.com/..."
                    value={form.meetingUrl}
                    onChange={(event) => setForm((prev) => ({ ...prev, meetingUrl: event.target.value }))}
                    disabled={isRecurring}
                  />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-semibold text-slate-500">공개 범위</label>
                  <select
                    className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                    value={form.visibility}
                    onChange={(event) => setForm((prev) => ({ ...prev, visibility: event.target.value }))}
                    disabled={isRecurring}
                  >
                    <option value="default">기본</option>
                    <option value="public">공개</option>
                    <option value="private">비공개</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs font-semibold text-slate-500">표시 상태</label>
                  <select
                    className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                    value={form.transparency}
                    onChange={(event) => setForm((prev) => ({ ...prev, transparency: event.target.value }))}
                    disabled={isRecurring}
                  >
                    <option value="opaque">바쁨</option>
                    <option value="transparent">한가함</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="text-xs font-semibold text-slate-500">시간대</label>
                <select
                  className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                  value={form.timezone}
                  onChange={(event) => setForm((prev) => ({ ...prev, timezone: event.target.value }))}
                  disabled={isRecurring}
                >
                  {TIMEZONE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="rounded-xl border border-gray-100 dark:border-gray-700/60 bg-gray-50/40 dark:bg-[#1a2632]/40 p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-semibold text-slate-800 dark:text-white">반복 일정</p>
                    <p className="text-xs text-slate-500">RRULE 없이 반복 규칙을 설정할 수 있어요.</p>
                  </div>
                  <label className="flex items-center gap-2 text-xs text-slate-500">
                    <input
                      type="checkbox"
                      checked={form.recurrenceEnabled}
                      onChange={(event) =>
                        setForm((prev) => ({
                          ...prev,
                          recurrenceEnabled: event.target.checked,
                        }))
                      }
                      disabled={isEdit}
                    />
                    사용
                  </label>
                </div>
                {isEdit && (
                  <p className="text-[10px] text-slate-400">
                    기존 일정은 반복 규칙으로 전환할 수 없습니다.
                  </p>
                )}
                {form.recurrenceEnabled && (
                  <div className="space-y-3">
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="text-xs font-semibold text-slate-500">빈도</label>
                        <select
                          className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                          value={form.recurrenceFrequency}
                          onChange={(event) =>
                            setForm((prev) => ({
                              ...prev,
                              recurrenceFrequency: event.target.value as EventRecurrence["freq"],
                            }))
                          }
                          disabled={isEdit}
                        >
                          <option value="DAILY">매일</option>
                          <option value="WEEKLY">매주</option>
                          <option value="MONTHLY">매월</option>
                          <option value="YEARLY">매년</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-xs font-semibold text-slate-500">간격</label>
                        <input
                          type="number"
                          min={1}
                          className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                          value={form.recurrenceInterval}
                          onChange={(event) =>
                            setForm((prev) => ({
                              ...prev,
                              recurrenceInterval: Number(event.target.value || 1),
                            }))
                          }
                          disabled={isEdit}
                        />
                      </div>
                    </div>

                    {form.recurrenceFrequency === "WEEKLY" && (
                      <div>
                        <label className="text-xs font-semibold text-slate-500">요일 선택</label>
                        <div className="mt-2 flex flex-wrap gap-2">
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
                    )}

                    {form.recurrenceFrequency === "MONTHLY" && (
                      <div className="space-y-2">
                        <label className="text-xs font-semibold text-slate-500">월간 규칙</label>
                        <div className="flex gap-2">
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
                        {form.recurrenceMonthlyMode === "date" ? (
                          <div className="grid grid-cols-2 gap-3">
                            <div>
                              <label className="text-xs font-semibold text-slate-500">매월 날짜</label>
                              <input
                                type="number"
                                min={1}
                                max={31}
                                className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                                value={form.recurrenceMonthDay}
                                onChange={(event) =>
                                  setForm((prev) => ({
                                    ...prev,
                                    recurrenceMonthDay: Number(event.target.value || 1),
                                  }))
                                }
                                disabled={isEdit}
                              />
                            </div>
                          </div>
                        ) : (
                          <div className="grid grid-cols-2 gap-3">
                            <div>
                              <label className="text-xs font-semibold text-slate-500">순서</label>
                              <select
                                className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                                value={form.recurrenceWeekdayPos}
                                onChange={(event) =>
                                  setForm((prev) => ({
                                    ...prev,
                                    recurrenceWeekdayPos: Number(event.target.value),
                                  }))
                                }
                                disabled={isEdit}
                              >
                                <option value={1}>첫 번째</option>
                                <option value={2}>두 번째</option>
                                <option value={3}>세 번째</option>
                                <option value={4}>네 번째</option>
                                <option value={-1}>마지막</option>
                              </select>
                            </div>
                            <div>
                              <label className="text-xs font-semibold text-slate-500">요일</label>
                              <select
                                className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                                value={form.recurrenceWeekday}
                                onChange={(event) =>
                                  setForm((prev) => ({
                                    ...prev,
                                    recurrenceWeekday: Number(event.target.value),
                                  }))
                                }
                                disabled={isEdit}
                              >
                                {WEEKDAY_OPTIONS.map((day) => (
                                  <option key={day.value} value={day.value}>
                                    {day.label}
                                  </option>
                                ))}
                              </select>
                            </div>
                          </div>
                        )}
                      </div>
                    )}

                    {form.recurrenceFrequency === "YEARLY" && (
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="text-xs font-semibold text-slate-500">월</label>
                          <input
                            type="number"
                            min={1}
                            max={12}
                            className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                            value={form.recurrenceYearMonth}
                            onChange={(event) =>
                              setForm((prev) => ({
                                ...prev,
                                recurrenceYearMonth: Number(event.target.value || 1),
                              }))
                            }
                            disabled={isEdit}
                          />
                        </div>
                        <div>
                          <label className="text-xs font-semibold text-slate-500">일</label>
                          <input
                            type="number"
                            min={1}
                            max={31}
                            className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                            value={form.recurrenceYearDay}
                            onChange={(event) =>
                              setForm((prev) => ({
                                ...prev,
                                recurrenceYearDay: Number(event.target.value || 1),
                              }))
                            }
                            disabled={isEdit}
                          />
                        </div>
                      </div>
                    )}

                    <div className="grid grid-cols-3 gap-3">
                      <div className="col-span-1">
                        <label className="text-xs font-semibold text-slate-500">종료</label>
                        <select
                          className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                          value={form.recurrenceEndMode}
                          onChange={(event) =>
                            setForm((prev) => ({ ...prev, recurrenceEndMode: event.target.value }))
                          }
                          disabled={isEdit}
                        >
                          <option value="none">없음</option>
                          <option value="until">날짜</option>
                          <option value="count">횟수</option>
                        </select>
                      </div>
                      {form.recurrenceEndMode === "until" && (
                        <div className="col-span-2">
                          <label className="text-xs font-semibold text-slate-500">종료 날짜</label>
                          <input
                            type="date"
                            className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                            value={form.recurrenceEndDate}
                            onChange={(event) =>
                              setForm((prev) => ({ ...prev, recurrenceEndDate: event.target.value }))
                            }
                            disabled={isEdit}
                          />
                        </div>
                      )}
                      {form.recurrenceEndMode === "count" && (
                        <div className="col-span-2">
                          <label className="text-xs font-semibold text-slate-500">횟수</label>
                          <input
                            type="number"
                            min={1}
                            className="mt-1 w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
                            value={form.recurrenceEndCount}
                            onChange={(event) =>
                              setForm((prev) => ({ ...prev, recurrenceEndCount: event.target.value }))
                            }
                            disabled={isEdit}
                          />
                        </div>
                      )}
                    </div>
                    {recurrenceError && (
                      <p className="text-xs text-red-500">{recurrenceError}</p>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
        <div className="flex items-center justify-between px-6 py-4 border-t border-gray-100 dark:border-gray-800">
          {stableEvent ? (
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
            <button className="px-4 py-2 rounded-lg border" onClick={onClose} type="button">
              취소
            </button>
            <button
              className="px-4 py-2 rounded-lg bg-primary text-white disabled:opacity-50"
              onClick={async () => {
                if (isRecurring) return;
                if (form.recurrenceEnabled && recurringPayload && !isEdit) {
                  await onCreateRecurring(recurringPayload);
                  onClose();
                  return;
                }
                if (stableEvent) {
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
