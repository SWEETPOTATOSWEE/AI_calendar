"use client";

import { useCallback, useEffect, useState } from "react";
import { listRecentEvents } from "../lib/api";
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

export default function RecentEventsModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { visible, closing } = useAnimatedOpen(open);
  const [events, setEvents] = useState<CalendarEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await listRecentEvents();
      setEvents(data || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "최근 일정 불러오기 실패");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    refresh();
  }, [open, refresh]);

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
          <h3 className="text-lg font-bold text-slate-900 dark:text-white">최근 추가한 일정</h3>
          <button className="text-slate-400 hover:text-slate-900 dark:hover:text-white" onClick={onClose} type="button">
            ✕
          </button>
        </div>
        <div className="px-6 py-4 space-y-3 max-h-[60vh] overflow-y-auto">
          {loading && <p className="text-xs text-slate-400">불러오는 중...</p>}
          {error && <p className="text-xs text-red-500">{error}</p>}
          {!loading && !error && events.length === 0 && (
            <p className="text-xs text-slate-400">최근 일정이 없습니다.</p>
          )}
          {events.map((event) => (
            <div key={`${event.id}-${event.start}`} className="rounded-xl border border-gray-100 dark:border-gray-700/50 bg-white dark:bg-[#1a2632] p-3">
              <p className="text-sm font-semibold text-slate-900 dark:text-white">{event.title}</p>
              <p className="text-xs text-slate-500">
                {formatTimeRange(event.start, event.end)} {event.location ? `• ${event.location}` : ""}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
