"use client";

import { useEffect, useMemo, useState } from "react";
import type { CalendarEvent } from "../lib/types";
import { formatTimeRange } from "../lib/date";

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

export default function SearchEventsModal({
  open,
  onClose,
  events,
}: {
  open: boolean;
  onClose: () => void;
  events: CalendarEvent[];
}) {
  const { visible, closing } = useAnimatedOpen(open);
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return events;
    return events.filter((event) => event.title.toLowerCase().includes(q) || (event.location || "").toLowerCase().includes(q));
  }, [events, query]);

  if (!visible) return null;

  return (
    <div
      className={`fixed inset-0 z-[999] flex items-center justify-center bg-black/40 px-4 ${
        closing ? "animate-overlayOut" : "animate-overlayIn"
      }`}
    >
      <div
        className={`w-full max-w-xl rounded-2xl bg-white dark:bg-[#111418] border border-gray-100 dark:border-gray-800 shadow-xl ${
          closing ? "animate-modalOut" : "animate-modalIn"
        }`}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-800">
          <h3 className="text-lg font-bold text-slate-900 dark:text-white">일정 검색</h3>
          <button className="text-slate-400 hover:text-slate-900 dark:hover:text-white" onClick={onClose} type="button">
            ✕
          </button>
        </div>
        <div className="px-6 py-4 space-y-4">
          <input
            className="w-full rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#1a2632] px-3 py-2 text-sm"
            placeholder="제목 또는 장소 검색"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className="space-y-3 max-h-[50vh] overflow-y-auto">
            {filtered.length === 0 && <p className="text-xs text-slate-400">검색 결과가 없습니다.</p>}
            {filtered.map((event) => (
              <div key={`${event.id}-${event.start}`} className="rounded-xl border border-gray-100 dark:border-gray-700/50 bg-white dark:bg-[#1a2632] p-3">
                <p className="text-sm font-semibold text-slate-900 dark:text-white">{event.title}</p>
                <p className="text-xs text-slate-500">{formatTimeRange(event.start, event.end)}</p>
                {event.location && <p className="text-[10px] text-slate-400">{event.location}</p>}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
