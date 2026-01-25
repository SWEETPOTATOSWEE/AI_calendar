export type CalendarEventSource = "local" | "google" | "google_task";

export type CalendarEvent = {
  id: number | string;
  title: string;
  start: string;
  end?: string | null;
  location?: string | null;
  description?: string | null;
  attendees?: string[] | null;
  reminders?: number[] | null;
  visibility?: "public" | "private" | "default" | null;
  transparency?: "opaque" | "transparent" | null;
  timezone?: string | null;
  meeting_url?: string | null;
  color_id?: string | null;
  calendar_id?: string | null;
  recur?: string | null;
  recurrence?: EventRecurrence | null;
  all_day?: boolean;
  start_date?: string | null;
  time?: string | null;
  duration_minutes?: number | null;
  source: CalendarEventSource;
  google_event_id?: string | null;
};

export type AuthStatus = {
  enabled: boolean;
  configured: boolean;
  has_token: boolean;
  admin?: boolean;
  photo_url?: string | null;
};

export type EventPayload = {
  title: string;
  start: string;
  end?: string | null;
  location?: string | null;
  description?: string | null;
  attendees?: string[] | null;
  reminders?: number[] | null;
  visibility?: "public" | "private" | "default" | null;
  transparency?: "opaque" | "transparent" | null;
  timezone?: string | null;
  meeting_url?: string | null;
  color_id?: string | null;
  recurrence?: EventRecurrence | null;
  all_day?: boolean;
};

export type RecurrenceEnd = {
  until?: string | null;
  count?: number | null;
};

export type EventRecurrence = {
  freq: "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY";
  interval?: number;
  byweekday?: number[] | null;
  bymonthday?: number[] | null;
  bysetpos?: number | null;
  bymonth?: number[] | null;
  end?: RecurrenceEnd | null;
};

export type RecurringEventPayload = {
  type: "recurring";
  title: string;
  start_date: string;
  time?: string | null;
  duration_minutes?: number | null;
  location?: string | null;
  description?: string | null;
  attendees?: string[] | null;
  reminders?: number[] | null;
  visibility?: "public" | "private" | "default" | null;
  transparency?: "opaque" | "transparent" | null;
  timezone?: string | null;
  meeting_url?: string | null;
  color_id?: string | null;
  recurrence: EventRecurrence;
};
