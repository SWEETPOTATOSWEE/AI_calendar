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
      .flatMap((event) => {
        if (event.source === "google") return [String(event.id)];
        if (event.google_event_id) return [String(event.google_event_id)];
        return [];
      })
      .filter((value, index, array) => array.indexOf(value) === index);

    if (localIds.length) {
      await deleteEventsByIds(localIds);
    }

    for (const id of googleIds) {
      await deleteGoogleEventById(id);
    }

    if (onAfterUndo) onAfterUndo();
  }, [stack, onAfterUndo]);

  return {
    stack,
    record,
    undo,
  };
};
