import type { EventRecurrence } from "./types";

export type RecurrencePreview = {
  recurrence?: EventRecurrence | null;
  weekdays?: number[] | null;
  start_date?: string | null;
  end_date?: string | null;
  time?: string | null;
  duration_minutes?: number | null;
  count?: number | null;
  all_day?: boolean;
};

const WEEKDAY_LABELS = ["월", "화", "수", "목", "금", "토", "일"];
const ORDINAL_LABELS: Record<number, string> = {
  1: "첫째",
  2: "둘째",
  3: "셋째",
  4: "넷째",
  5: "다섯째",
  "-1": "마지막",
};

const pad2 = (value: number) => String(value).padStart(2, "0");

const normalizeWeekdays = (weekdays?: number[] | null) => {
  if (!Array.isArray(weekdays) || weekdays.length === 0) return [];
  const unique = new Set<number>();
  for (const day of weekdays) {
    if (Number.isInteger(day) && day >= 0 && day <= 6) {
      unique.add(day);
    }
  }
  return Array.from(unique).sort((a, b) => a - b);
};

const formatWeekdays = (weekdays?: number[] | null) => {
  const list = normalizeWeekdays(weekdays);
  if (list.length === 0) return "";
  return list
    .map((day) => WEEKDAY_LABELS[day])
    .filter(Boolean)
    .join("/");
};

const formatOrdinal = (value?: number | null) => {
  if (!value) return "";
  const label = ORDINAL_LABELS[value];
  if (label) return label;
  if (value < 0) return `${Math.abs(value)}번째 마지막`;
  return `${value}번째`;
};

const formatRecurrenceFrequency = (
  freq?: EventRecurrence["freq"] | null,
  interval?: number | null
) => {
  if (!freq) return "";
  const safeInterval = Math.max(1, Number(interval) || 1);
  const hasInterval = safeInterval > 1;
  switch (freq) {
    case "DAILY":
      return hasInterval ? `매 ${safeInterval}일` : "매일";
    case "WEEKLY":
      return hasInterval ? `매 ${safeInterval}주` : "매주";
    case "MONTHLY":
      return hasInterval ? `매 ${safeInterval}개월` : "매월";
    case "YEARLY":
      return hasInterval ? `매 ${safeInterval}년` : "매년";
    default:
      return "";
  }
};

const formatMinutesTime = (minutes: number) => {
  const normalized = ((minutes % 1440) + 1440) % 1440;
  const hours = Math.floor(normalized / 60);
  const mins = normalized % 60;
  return `${pad2(hours)}:${pad2(mins)}`;
};

const parseTimeToMinutes = (value?: string | null) => {
  if (!value) return null;
  const match = value.trim().match(/^(\d{1,2}):(\d{2})$/);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;
  return hours * 60 + minutes;
};

const formatRecurrenceTime = (item: RecurrencePreview) => {
  if (item.all_day) return "종일";
  if (!item.time) return "";
  const duration = Number(item.duration_minutes || 0);
  const startMinutes = parseTimeToMinutes(item.time);
  if (startMinutes === null || duration <= 0) return item.time;
  const endMinutes = startMinutes + duration;
  return `${formatMinutesTime(startMinutes)}~${formatMinutesTime(endMinutes)}`;
};

const formatRecurrenceDetail = (recurrence: EventRecurrence | null, fallbackWeekdays?: number[] | null) => {
  if (!recurrence) return formatWeekdays(fallbackWeekdays);
  if (recurrence.freq === "WEEKLY") {
    return formatWeekdays(recurrence.byweekday ?? fallbackWeekdays);
  }
  if (recurrence.freq === "MONTHLY") {
    if (recurrence.bysetpos && recurrence.byweekday?.length) {
      const ordinal = formatOrdinal(recurrence.bysetpos);
      const weekdayText = formatWeekdays(recurrence.byweekday);
      if (ordinal && weekdayText) return `${ordinal} ${weekdayText}`;
    }
    if (recurrence.bymonthday?.length) {
      const day = recurrence.bymonthday[0];
      if (day === -1) return "마지막 날";
      if (day > 0) return `${day}일`;
    }
    return "";
  }
  if (recurrence.freq === "YEARLY") {
    const month = recurrence.bymonth?.[0];
    const monthLabel = month ? `${month}월` : "";
    let dayLabel = "";
    if (recurrence.bymonthday?.length) {
      const day = recurrence.bymonthday[0];
      if (day === -1) dayLabel = "마지막 날";
      else if (day > 0) dayLabel = `${day}일`;
    }
    return [monthLabel, dayLabel].filter(Boolean).join(" ");
  }
  return "";
};

const formatRecurrenceRange = (item: RecurrencePreview, recurrence?: EventRecurrence | null) => {
  const endDate = item.end_date || recurrence?.end?.until || "";
  if (endDate) {
    const startDate = item.start_date || "";
    return startDate ? `${startDate}~${endDate}` : `~${endDate}`;
  }
  const count = recurrence?.end?.count ?? item.count ?? null;
  if (count) return `${count}회`;
  return "";
};

export const formatRecurrencePattern = (item: RecurrencePreview) => {
  const recurrence = item.recurrence ?? null;
  const frequencyLabel = formatRecurrenceFrequency(recurrence?.freq, recurrence?.interval);
  const baseLabel = frequencyLabel || (item.weekdays?.length ? "매주" : "");
  if (!baseLabel) return "";
  const detailLabel = formatRecurrenceDetail(recurrence, item.weekdays ?? null);
  return [baseLabel, detailLabel].filter(Boolean).join(" ");
};

export const formatRecurrenceTimeLabel = (item: RecurrencePreview) => formatRecurrenceTime(item);

export const formatRecurrenceDateLabel = (item: RecurrencePreview) => {
  const startDate = item.start_date || "";
  const endDate = item.end_date || item.recurrence?.end?.until || "";
  if (startDate && endDate) return `${startDate}~${endDate}`;
  if (startDate) return startDate;
  if (endDate) return `~${endDate}`;
  return "";
};

export const formatRecurrenceSummary = (item: RecurrencePreview) => {
  const recurrence = item.recurrence ?? null;
  const patternLabel = formatRecurrencePattern(item);
  if (!patternLabel) return "";
  const timeLabel = formatRecurrenceTime(item);
  const rangeLabel = formatRecurrenceRange(item, recurrence);
  const parts = [patternLabel];
  if (timeLabel) parts.push(timeLabel);
  if (rangeLabel) parts.push(rangeLabel);
  return parts.join(" · ");
};
