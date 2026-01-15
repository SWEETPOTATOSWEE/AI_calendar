"use client";

import { useCallback, useState } from "react";
import type { CalendarEvent } from "./types";
import { deleteEventsByIds, deleteGoogleEventById } from "./api";

export const useUndoStack = (onAfterUndo?: () => void) => {
  const [stack, setStack] = useState<CalendarEvent[][]>([]);

  const record = useCallback((events: CalendarEvent[]) => {
    if (!events || events.length === 0) return;
    setStack((prev) => [...prev, events]);
  }, []);

  const undo = useCallback(async () => {
    if (stack.length === 0) return;
    const batch = stack[stack.length - 1];
    setStack((prev) => prev.slice(0, -1));

    const localIds = batch
      .filter((event) => event.source === "local" && typeof event.id === "number")
      .map((event) => event.id as number);

    const googleIds = batch
      .filter((event) => event.source === "google")
      .map((event) => ({
        eventId: event.google_event_id ?? String(event.id),
        calendarId: event.calendar_id ?? null,
      }))
      .filter((item) => Boolean(item.eventId));

    if (localIds.length) {
      await deleteEventsByIds(localIds);
    }

    const seen = new Set<string>();
    for (const item of googleIds) {
      const key = `${item.calendarId ?? ""}::${item.eventId}`;
      if (seen.has(key)) continue;
      seen.add(key);
      await deleteGoogleEventById(String(item.eventId), item.calendarId);
    }

    if (onAfterUndo) onAfterUndo();
  }, [stack, onAfterUndo]);

  return {
    stack,
    record,
    undo,
  };
};
