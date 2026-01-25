const pad = (value: number) => String(value).padStart(2, "0");

export const toISODate = (date: Date) => {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
};

export const toISODateTime = (date: Date) => {
  return `${toISODate(date)}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

export const parseISODateTime = (value?: string | null) => {
  if (!value) return null;
  const [datePart, timePart] = value.split("T");
  if (!datePart || !timePart) return null;
  const [year, month, day] = datePart.split("-").map(Number);
  const [hour, minute] = timePart.split(":").map(Number);
  if (!year || !month || !day || Number.isNaN(hour) || Number.isNaN(minute)) return null;
  return new Date(year, month - 1, day, hour, minute);
};

export const startOfMonth = (date: Date) => new Date(date.getFullYear(), date.getMonth(), 1);

export const endOfMonth = (date: Date) => new Date(date.getFullYear(), date.getMonth() + 1, 0);

export const startOfWeek = (date: Date) => {
  const day = date.getDay();
  const diff = day;
  const result = new Date(date);
  result.setDate(date.getDate() - diff);
  result.setHours(0, 0, 0, 0);
  return result;
};

export const endOfWeek = (date: Date) => {
  const start = startOfWeek(date);
  const result = new Date(start);
  result.setDate(start.getDate() + 6);
  result.setHours(23, 59, 59, 999);
  return result;
};

export const addDays = (date: Date, days: number) => {
  const result = new Date(date);
  result.setDate(date.getDate() + days);
  return result;
};

export const addMonths = (date: Date, months: number) => {
  const result = new Date(date);
  result.setMonth(date.getMonth() + months);
  return result;
};

export const isSameDay = (a: Date, b: Date) => {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
};

export const formatMonthYear = (date: Date) => {
  return date.toLocaleDateString("ko-KR", { month: "long", year: "numeric" });
};

export const formatLongDate = (date: Date) => {
  return date.toLocaleDateString("ko-KR", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
};

export const formatSearchDate = (date: Date) => {
  const year = date.getFullYear();
  const month = date.getMonth() + 1;
  const day = date.getDate();
  const weekday = date.toLocaleDateString("ko-KR", { weekday: "short" });
  return `${year} ${month}/${day} (${weekday})`;
};

export const formatShortDate = (date: Date) => {
  return date.toLocaleDateString("ko-KR", { month: "long", day: "numeric", year: "numeric" });
};

export const formatWeekday = (date: Date) => {
  return date.toLocaleDateString("ko-KR", { weekday: "long" });
};

export const formatTime = (value?: string | null) => {
  const parsed = parseISODateTime(value);
  if (!parsed) return "";
  return parsed.toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
};

export const formatTimeRange = (start?: string | null, end?: string | null) => {
  const startLabel = formatTime(start);
  const endLabel = formatTime(end);
  if (!startLabel && !endLabel) return "";
  if (!endLabel) return startLabel;
  return `${startLabel} - ${endLabel}`;
};

export const getHoursRange = (startHour: number, endHour: number) => {
  const hours: number[] = [];
  for (let hour = startHour; hour <= endHour; hour += 1) {
    hours.push(hour);
  }
  return hours;
};

export const formatHourLabel = (hour: number) => {
  const date = new Date(2023, 0, 1, hour, 0, 0);
  return date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false });
};
