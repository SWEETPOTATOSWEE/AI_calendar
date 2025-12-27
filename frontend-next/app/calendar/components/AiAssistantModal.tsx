"use client";

import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import {
  ArrowUp,
  Brain,
  Dog,
  Image,
  Plus,
  Rabbit,
  RotateCcw,
  Sparkles,
  StopCircle,
  Trash2,
  X,
} from "lucide-react";
import { formatTimeRange } from "../lib/date";
import { useAiAssistant } from "../lib/use-ai-assistant";

export type AiAssistantModalProps = {
  assistant: ReturnType<typeof useAiAssistant>;
};

const safeArray = <T,>(value?: T[] | null) => (Array.isArray(value) ? value : []);

const ANIMATION_MS = 260;

const pad2 = (value: number) => String(value).padStart(2, "0");
const toLocalISODate = (date: Date) =>
  `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
const parseLocalISODate = (value?: string) => {
  if (!value) return null;
  const parts = value.split("-").map((item) => Number(item));
  if (parts.length !== 3) return null;
  const [year, month, day] = parts;
  if (!year || !month || !day) return null;
  return new Date(year, month - 1, day);
};
const addDays = (date: Date, days: number) => {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
};
const diffDays = (from: Date, to: Date) =>
  Math.round((from.getTime() - to.getTime()) / 86400000);
const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const buildSnapshot = (assistant: ReturnType<typeof useAiAssistant>) => ({
  mode: assistant.mode,
  text: assistant.text,
  reasoningEffort: assistant.reasoningEffort,
  model: assistant.model,
  startDate: assistant.startDate,
  endDate: assistant.endDate,
  attachments: assistant.attachments,
  conversation: assistant.conversation,
  loading: assistant.loading,
  error: assistant.error,
  addPreview: assistant.addPreview,
  deletePreview: assistant.deletePreview,
  selectedAddItems: assistant.selectedAddItems,
  selectedDeleteGroups: assistant.selectedDeleteGroups,
});

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

export default function AiAssistantModal({ assistant }: AiAssistantModalProps) {
  const { visible, closing } = useAnimatedOpen(assistant.open);
  const [snapshot, setSnapshot] = useState(() => buildSnapshot(assistant));
  useEffect(() => {
    if (!assistant.open) return;
    setSnapshot(buildSnapshot(assistant));
  }, [
    assistant.open,
    assistant.mode,
    assistant.text,
    assistant.reasoningEffort,
    assistant.model,
    assistant.startDate,
    assistant.endDate,
    assistant.attachments,
    assistant.loading,
    assistant.error,
    assistant.addPreview,
    assistant.deletePreview,
    assistant.selectedAddItems,
    assistant.selectedDeleteGroups,
  ]);
  const view = closing ? snapshot : buildSnapshot(assistant);
  const addItems = safeArray(view.addPreview?.items);
  const deleteGroups = safeArray(view.deletePreview?.groups);
  const conversation = safeArray(view.conversation);
  const selectedAddCount = addItems.filter((_, index) => view.selectedAddItems[index]).length;
  const selectedDeleteCount = deleteGroups.filter((group) => view.selectedDeleteGroups[group.group_key]).length;
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const [isMultiline, setIsMultiline] = useState(false);
  const today = new Date();
  const todayRef = useRef(today);
  todayRef.current = today;
  const startDateValue = parseLocalISODate(view.startDate) || today;
  const endDateValue = parseLocalISODate(view.endDate) || addDays(startDateValue, 90);
  const derivedStartOffset = clamp(diffDays(startDateValue, today), -365, 364);
  const derivedEndOffset = clamp(diffDays(endDateValue, today), derivedStartOffset + 1, 365);
  const [startOffset, setStartOffset] = useState(derivedStartOffset);
  const [endOffset, setEndOffset] = useState(derivedEndOffset);
  const [activeThumb, setActiveThumb] = useState<"start" | "end" | null>(null);
  const sliderRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<"start" | "end" | null>(null);
  const offsetsRef = useRef({ startOffset: derivedStartOffset, endOffset: derivedEndOffset });
  const startOnTop = startOffset > endOffset - 4;
  const normalizedStartDate = addDays(today, startOffset);
  const normalizedEndDate = addDays(today, endOffset);
  const sliderMin = -365;
  const sliderMax = 365;
  const sliderRange = sliderMax - sliderMin;
  const startPercent = ((startOffset - sliderMin) / sliderRange) * 100;
  const endPercent = ((endOffset - sliderMin) / sliderRange) * 100;

  useEffect(() => {
    if (view.mode !== "delete") return;
    setStartOffset(derivedStartOffset);
    setEndOffset(derivedEndOffset);
  }, [view.mode, derivedStartOffset, derivedEndOffset]);

  useEffect(() => {
    offsetsRef.current = { startOffset, endOffset };
  }, [startOffset, endOffset]);

  const updateStartOffset = useCallback(
    (value: number) => {
    const nextStartOffset = Math.min(value, offsetsRef.current.endOffset - 1);
    const nextStart = addDays(todayRef.current, nextStartOffset);
    setStartOffset(nextStartOffset);
    assistant.setStartDate(toLocalISODate(nextStart));
    },
    [assistant]
  );

  const updateEndOffset = useCallback(
    (value: number) => {
    const nextEndOffset = Math.max(value, offsetsRef.current.startOffset + 1);
    const nextEnd = addDays(todayRef.current, nextEndOffset);
    setEndOffset(nextEndOffset);
    assistant.setEndDate(toLocalISODate(nextEnd));
    },
    [assistant]
  );

  const offsetFromPointer = useCallback(
    (clientX: number) => {
    const slider = sliderRef.current;
    if (!slider) return null;
    const rect = slider.getBoundingClientRect();
    if (!rect.width) return null;
    const ratio = clamp((clientX - rect.left) / rect.width, 0, 1);
    return Math.round(ratio * sliderRange + sliderMin);
    },
    [sliderRange, sliderMin]
  );

  const handlePointerMove = useCallback(
    (event: PointerEvent) => {
      const active = dragRef.current;
      if (!active) return;
      const nextOffset = offsetFromPointer(event.clientX);
      if (nextOffset === null) return;
      if (active === "start") {
        updateStartOffset(nextOffset);
      } else {
        updateEndOffset(nextOffset);
      }
    },
    [offsetFromPointer, updateStartOffset, updateEndOffset]
  );

  useEffect(() => {
    if (!activeThumb) return;
    const onMove = (event: PointerEvent) => handlePointerMove(event);
    const onUp = () => {
      dragRef.current = null;
      setActiveThumb(null);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
    };
  }, [activeThumb, handlePointerMove]);

  const resizeTextarea = (value: string) => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    const newHeight = Math.min(Math.max(el.scrollHeight, 24), 200);
    el.style.height = `${newHeight}px`;
    setIsMultiline(newHeight > 60 || value.includes("\n"));
  };

  useEffect(() => {
    resizeTextarea(view.text);
  }, [view.text]);

  if (!visible) return null;

  const canSend = view.text.trim().length > 0 || view.attachments.length > 0;

  return (
    <div
      className={`fixed inset-0 z-[999] flex items-center justify-center bg-black/50 px-4 ${
        closing ? "animate-overlayOut" : "animate-overlayIn"
      }`}
    >
      <div
        className={`w-full max-w-[760px] max-h-[780px] h-[72vh] flex flex-col rounded-2xl bg-white dark:bg-[#111418] border border-gray-100 dark:border-gray-800 shadow-xl overflow-hidden ${
          closing ? "animate-modalOut" : "animate-modalIn"
        }`}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-800">
          <div className="flex items-center gap-4">
            <button
              className={`size-9 rounded-full flex items-center justify-center ${
                view.mode === "add"
                  ? "bg-primary text-white"
                  : "bg-gray-100 dark:bg-gray-800 text-slate-500"
              }`}
              type="button"
              onClick={() => assistant.setMode("add")}
              aria-label="추가"
            >
              <Plus className="size-4" />
            </button>
            <button
              className={`size-9 rounded-full flex items-center justify-center ${
                view.mode === "delete"
                  ? "bg-red-500 text-white"
                  : "bg-gray-100 dark:bg-gray-800 text-slate-500"
              }`}
              type="button"
              onClick={() => assistant.setMode("delete")}
              aria-label="삭제"
            >
              <Trash2 className="size-4" />
            </button>
            <div className="h-6 w-px bg-gray-200 dark:bg-gray-700 mx-1" />
            <button
              className="size-9 rounded-full flex items-center justify-center bg-gray-100 dark:bg-gray-800 text-slate-500 hover:text-primary"
              type="button"
              onClick={assistant.resetConversation}
              aria-label="대화 초기화"
            >
              <RotateCcw className="size-4" />
            </button>
            <div className="h-6 w-px bg-gray-200 dark:bg-gray-700 mx-1" />
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-semibold text-slate-500">모델</span>
              <div className="flex items-center rounded-full bg-gray-100 dark:bg-gray-800 p-0.5">
                {["nano", "mini"].map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => assistant.setModel(value as "nano" | "mini")}
                    className={`px-3 py-1 text-[11px] font-semibold rounded-full transition-colors ${
                      view.model === value
                        ? "bg-white dark:bg-gray-700 text-slate-900 dark:text-white shadow-sm"
                        : "text-slate-500 hover:text-slate-900 dark:hover:text-white"
                    }`}
                  >
                    {value}
                  </button>
                ))}
              </div>
            </div>
            <div className="h-6 w-px bg-gray-200 dark:bg-gray-700 mx-1" />
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-semibold text-slate-500">추론</span>
              <div className="flex items-center rounded-full bg-gray-100 dark:bg-gray-800 p-0.5">
                {["low", "medium", "high"].map((value) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => assistant.setReasoningEffort(value)}
                    className={`px-3 py-1 text-[11px] font-semibold rounded-full transition-colors ${
                      view.reasoningEffort === value
                        ? "bg-white dark:bg-gray-700 text-slate-900 dark:text-white shadow-sm"
                        : "text-slate-500 hover:text-slate-900 dark:hover:text-white"
                    }`}
                  >
                    {value === "low" ? (
                      <Rabbit className="size-4" />
                    ) : value === "medium" ? (
                      <Dog className="size-4" />
                    ) : value === "high" ? (
                      <Brain className="size-4" />
                    ) : (
                      value
                    )}
                  </button>
                ))}
              </div>
              <span className="text-[10px] text-slate-400">
                {view.reasoningEffort === "low"
                  ? "낮음 · 빠르고 가볍게"
                  : view.reasoningEffort === "medium"
                  ? "중간 · 균형"
                  : "높음 · 더 정밀하게"}
              </span>
            </div>
          </div>
        </div>
        <div className="px-6 py-5 flex-1 min-h-0 flex flex-col gap-4">
          <div className="flex-1 min-h-0 overflow-y-auto space-y-3 rounded-2xl border border-gray-100 dark:border-gray-800 bg-slate-50/70 dark:bg-[#0f1722] p-4">
            {conversation.length === 0 && (
              <div className="text-xs text-slate-400">아직 대화가 없습니다.</div>
            )}
            {conversation.map((msg, index) => (
              <div key={`${msg.role}-${index}`} className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}>
                <div
                  className={`max-w-[70%] rounded-2xl px-4 py-3 text-sm shadow-sm ${
                    msg.role === "user"
                      ? "bg-primary text-white"
                      : "bg-white dark:bg-[#111418] border border-gray-100 dark:border-gray-700/50 text-slate-700 dark:text-slate-200"
                  }`}
                >
                  <p className="whitespace-pre-line">{msg.text}</p>
                </div>
              </div>
            ))}
            {(view.addPreview || view.deletePreview || view.error) && (
              <div className="flex items-start gap-3">
                <span className="mt-1 inline-flex size-6 items-center justify-center rounded-full bg-blue-100 text-primary">
                  <Sparkles className="size-3.5" />
                </span>
                <div className="flex-1 rounded-2xl border border-gray-100 dark:border-gray-700/50 bg-white dark:bg-[#111418] px-4 py-3 text-sm text-slate-700 dark:text-slate-200 shadow-sm">
                  {view.error && <p className="text-xs text-red-500">{view.error}</p>}
                  {view.mode === "add" && addItems.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold text-slate-500">다음 일정이 감지되었습니다.</p>
                      {addItems.map((item, index) => (
                        <label
                          key={`${item.title}-${index}`}
                          className="flex items-start gap-3 rounded-xl border border-gray-100 dark:border-gray-700/50 bg-slate-50 dark:bg-[#1a2632] p-3"
                        >
                          <input
                            type="checkbox"
                            checked={view.selectedAddItems[index] ?? true}
                            onChange={(event) =>
                              assistant.setSelectedAddItems((prev) => ({
                                ...prev,
                                [index]: event.target.checked,
                              }))
                            }
                          />
                          <div>
                            <p className="text-sm font-semibold text-slate-900 dark:text-white">{item.title}</p>
                            <p className="text-xs text-slate-500">
                              {item.type === "recurring" ? "반복" : "단일"} · {formatTimeRange(item.start, item.end)}
                            </p>
                            {item.samples && item.samples.length > 0 && (
                              <p className="text-[10px] text-slate-400 mt-1">
                                예시: {item.samples.slice(0, 3).join(", ")}
                              </p>
                            )}
                          </div>
                        </label>
                      ))}
                    </div>
                  )}
                  {view.mode === "delete" && deleteGroups.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold text-slate-500">삭제 후보를 확인해주세요.</p>
                      {deleteGroups.map((group) => (
                        <label
                          key={group.group_key}
                          className="flex items-start gap-3 rounded-xl border border-gray-100 dark:border-gray-700/50 bg-slate-50 dark:bg-[#1a2632] p-3"
                        >
                          <input
                            type="checkbox"
                            checked={view.selectedDeleteGroups[group.group_key] ?? true}
                            onChange={(event) =>
                              assistant.setSelectedDeleteGroups((prev) => ({
                                ...prev,
                                [group.group_key]: event.target.checked,
                              }))
                            }
                          />
                          <div>
                            <p className="text-sm font-semibold text-slate-900 dark:text-white">{group.title}</p>
                            <p className="text-xs text-slate-500">
                              {group.count || group.ids?.length || 0}건 · {group.time || ""}
                            </p>
                            {group.samples && group.samples.length > 0 && (
                              <p className="text-[10px] text-slate-400 mt-1">
                                예시: {group.samples.slice(0, 3).join(", ")}
                              </p>
                            )}
                          </div>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
            {assistant.progressLabel && (
              <div className="flex items-start gap-3">
                <span className="mt-1 inline-flex size-6 items-center justify-center rounded-full bg-blue-100 text-primary">
                  <Sparkles className="size-3.5" />
                </span>
                <div className="rounded-2xl border border-gray-100 dark:border-gray-700/50 bg-white dark:bg-[#111418] px-4 py-3 text-sm text-slate-500">
                  {assistant.progressLabel}
                </div>
              </div>
            )}
          </div>
          <div
            className={`relative flex w-full overflow-hidden border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] shadow-sm ${
              isMultiline ? "flex-col rounded-3xl" : "flex-row items-end rounded-[28px]"
            }`}
          >
            <div
              className={`flex shrink-0 items-center ${
                isMultiline ? "order-2 w-full justify-between px-3 pb-3" : "order-1 pl-2 pb-2"
              }`}
            >
              <label className="flex size-9 items-center justify-center rounded-full bg-gray-100 text-slate-500 hover:bg-gray-200 cursor-pointer">
                <Image className="size-5" />
                <input
                  className="hidden"
                  type="file"
                  accept="image/*"
                  multiple
                  onChange={(event: ChangeEvent<HTMLInputElement>) => assistant.handleAttach(event.target.files)}
                />
              </label>
              {isMultiline && (
                <button
                  type="button"
                  onClick={assistant.loading ? assistant.interrupt : assistant.preview}
                  aria-label={assistant.loading ? "요청 중단" : "AI에게 부탁하기"}
                  disabled={!assistant.loading && !canSend}
                  className={`flex size-9 items-center justify-center rounded-full ${
                    assistant.loading
                      ? "bg-red-500 text-white hover:bg-red-600"
                      : canSend
                      ? "bg-black text-white hover:bg-gray-800"
                      : "bg-gray-200 text-gray-400"
                  }`}
                >
                  {assistant.loading ? <StopCircle className="size-4" /> : <ArrowUp size={18} />}
                </button>
              )}
            </div>
            <div className={`flex-1 ${isMultiline ? "order-1 w-full" : "order-2"}`}>
              <textarea
                ref={inputRef}
                className={`max-h-[200px] w-full resize-none border-none bg-transparent text-sm text-slate-900 dark:text-slate-100 outline-none focus:outline-none focus:ring-0 overflow-y-auto ${
                  isMultiline ? "p-4 pb-0" : "py-3.5 px-3"
                }`}
                rows={1}
                style={{ minHeight: "24px" }}
                placeholder="AI에게 부탁할 내용을 입력하세요"
                value={view.text}
                onChange={(event) => {
                  assistant.setText(event.target.value);
                  resizeTextarea(event.target.value);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    if (canSend && !assistant.loading) {
                      assistant.preview();
                    }
                  }
                }}
              />
            </div>
            {!isMultiline && (
              <div className="order-3 pb-2 pr-2">
                <button
                  type="button"
                  onClick={assistant.loading ? assistant.interrupt : assistant.preview}
                  aria-label={assistant.loading ? "요청 중단" : "AI에게 부탁하기"}
                  disabled={!assistant.loading && !canSend}
                  className={`flex size-9 items-center justify-center rounded-full ${
                    assistant.loading
                      ? "bg-red-500 text-white hover:bg-red-600"
                      : canSend
                      ? "bg-black text-white hover:bg-gray-800"
                      : "bg-gray-200 text-gray-400"
                  }`}
                >
                  {assistant.loading ? <StopCircle className="size-4" /> : <ArrowUp size={18} />}
                </button>
              </div>
            )}
          </div>
          {view.attachments.length > 0 && (
            <span className="text-[11px] text-slate-400">
              {view.attachments.length}개 첨부됨
            </span>
          )}
          {view.attachments.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {view.attachments.map((item) => (
                <div key={item.id} className="flex items-center gap-2 rounded-full bg-gray-100 dark:bg-gray-800 px-3 py-1 text-xs">
                  <span className="truncate max-w-[140px]">{item.name}</span>
                  <button
                    className="text-slate-400 hover:text-red-500"
                    type="button"
                    onClick={() => assistant.removeAttachment(item.id)}
                  >
                    <X className="size-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
          {view.mode === "delete" && (
            <div className="rounded-2xl border border-gray-100 dark:border-gray-800 bg-white dark:bg-[#111418] px-4 py-3 shadow-sm">
              <div className="flex items-center justify-between text-xs text-slate-500">
                <span>시작 {toLocalISODate(normalizedStartDate)}</span>
                <span>끝 {toLocalISODate(normalizedEndDate)}</span>
              </div>
              <div ref={sliderRef} className="relative mt-4 h-7">
                <div className="absolute left-0 right-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-gray-200 dark:bg-gray-700" />
                <div
                  className="absolute top-1/2 h-1 -translate-y-1/2 rounded-full bg-primary/60"
                  style={{
                    left: `${startPercent}%`,
                    right: `${100 - endPercent}%`,
                  }}
                />
                <button
                  type="button"
                  aria-label="삭제 시작 날짜"
                  onPointerDown={(event) => {
                    event.preventDefault();
                    dragRef.current = "start";
                    setActiveThumb("start");
                    const nextOffset = offsetFromPointer(event.clientX);
                    if (nextOffset !== null) {
                      updateStartOffset(nextOffset);
                    }
                  }}
                  className={`absolute top-1/2 -translate-y-1/2 size-5 rounded-full border border-gray-300 bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-primary/40 touch-none ${
                    activeThumb === "start"
                      ? "z-20"
                      : activeThumb === "end"
                      ? "z-10"
                      : startOnTop
                      ? "z-20"
                      : "z-10"
                  }`}
                  style={{ left: `calc(${startPercent}% - 10px)` }}
                />
                <button
                  type="button"
                  aria-label="삭제 종료 날짜"
                  onPointerDown={(event) => {
                    event.preventDefault();
                    dragRef.current = "end";
                    setActiveThumb("end");
                    const nextOffset = offsetFromPointer(event.clientX);
                    if (nextOffset !== null) {
                      updateEndOffset(nextOffset);
                    }
                  }}
                  className={`absolute top-1/2 -translate-y-1/2 size-5 rounded-full border border-gray-300 bg-white shadow-sm focus:outline-none focus:ring-2 focus:ring-primary/40 touch-none ${
                    activeThumb === "end"
                      ? "z-20"
                      : activeThumb === "start"
                      ? "z-10"
                      : startOnTop
                      ? "z-10"
                      : "z-20"
                  }`}
                  style={{ left: `calc(${endPercent}% - 10px)` }}
                />
              </div>
            </div>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-gray-100 dark:border-gray-800">
          <button
            className="px-4 py-2 rounded-full border border-gray-200 dark:border-gray-700 text-sm font-semibold text-slate-500 hover:text-slate-900 dark:hover:text-white"
            type="button"
            onClick={assistant.close}
          >
            취소
          </button>
          <button
            className={`px-4 py-2 rounded-full text-sm font-semibold transition-colors ${
              view.mode === "delete"
                ? "bg-red-500 text-white hover:bg-red-600"
                : "bg-primary text-white hover:bg-blue-600"
            } ${
              (view.mode === "add" && selectedAddCount === 0) ||
              (view.mode === "delete" && selectedDeleteCount === 0)
                ? "opacity-50 pointer-events-none"
                : ""
            }`}
            type="button"
            onClick={assistant.apply}
            disabled={
              assistant.loading ||
              (view.mode === "add" && selectedAddCount === 0) ||
              (view.mode === "delete" && selectedDeleteCount === 0)
            }
          >
            {view.mode === "delete"
              ? selectedDeleteCount > 0
                ? `${selectedDeleteCount}건 일정 삭제`
                : "일정 삭제"
              : selectedAddCount > 0
              ? `${selectedAddCount}개의 일정 추가`
              : "일정 추가"}
          </button>
        </div>
      </div>
    </div>
  );
}
