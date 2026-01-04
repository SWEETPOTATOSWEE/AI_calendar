"use client";

import { useEffect, useRef, useState, type ChangeEvent, type CSSProperties } from "react";
import {
  ArrowUp,
  Calendar,
  Check,
  Clock,
  Image,
  RefreshCcw,
  MapPin,
  Pencil,
  Plus,
  Rabbit,
  RotateCcw,
  Sparkles,
  StopCircle,
  Trash2,
  Turtle,
  X,
} from "lucide-react";
import { formatShortDate, formatTimeRange, parseISODateTime } from "../lib/date";
import {
  formatRecurrenceDateLabel,
  formatRecurrencePattern,
  formatRecurrenceTimeLabel,
} from "../lib/recurrence-summary";
import { useAiAssistant, type AddPreviewItem } from "../lib/use-ai-assistant";

export type AiAssistantModalProps = {
  assistant: ReturnType<typeof useAiAssistant>;
  onEditAddItem?: (item: AddPreviewItem, index: number) => void;
};

const safeArray = <T,>(value?: T[] | null) => (Array.isArray(value) ? value : []);

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
const EMPTY_MESSAGE = "이곳에 일정을 입력하면 캘린더에 추가할 수 있어요.";

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
  return { visible: open, closing: false };
};

export default function AiAssistantModal({ assistant, onEditAddItem }: AiAssistantModalProps) {
  const { visible, closing } = useAnimatedOpen(assistant.open);
  const tooltipClass =
    "pointer-events-none absolute left-1/2 top-full z-[9999] mt-2 w-max max-w-[360px] -translate-x-1/2 rounded-full border border-[#1F2937] bg-[#111827] px-3 py-1 text-[12px] font-medium leading-[1.4] text-white opacity-0 transition-opacity group-hover:opacity-100 group-disabled:opacity-100 group-disabled:text-[#D1D5DB] group-disabled:bg-[#374151] group-disabled:border-[#374151] whitespace-nowrap text-center";
  const [snapshot, setSnapshot] = useState(() => buildSnapshot(assistant));
  const prevLoadingRef = useRef(assistant.loading);
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
  useEffect(() => {
    if (!assistant.open) {
      prevLoadingRef.current = assistant.loading;
      return;
    }
    if (prevLoadingRef.current && !assistant.loading) {
    }
    prevLoadingRef.current = assistant.loading;
  }, [assistant.loading, assistant.open]);
  const addItems = safeArray(view.addPreview?.items);
  const deleteGroups = safeArray(view.deletePreview?.groups);
  const conversation = safeArray(view.conversation);
  const selectedAddCount = addItems.filter((_, index) => view.selectedAddItems[index]).length;
  const selectedDeleteCount = deleteGroups.filter((group) => view.selectedDeleteGroups[group.group_key]).length;
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const [isMultiline, setIsMultiline] = useState(false);
  const isThinking = assistant.progressLabel === "생각 중";
  const hasPanels = Boolean(view.addPreview || view.deletePreview || assistant.progressLabel || view.error);
  const showConversation = conversation.length > 0 || hasPanels;
  const showEmptyState = assistant.open && !showConversation;
  const emptyMessage = EMPTY_MESSAGE;
  const startDateValue = parseLocalISODate(view.startDate);
  const endDateValue = parseLocalISODate(view.endDate);
  const minEndDate = startDateValue ? toLocalISODate(addDays(startDateValue, 1)) : "";
  const maxStartDate = endDateValue ? toLocalISODate(addDays(endDateValue, -1)) : "";

  const handleStartDateChange = (event: ChangeEvent<HTMLInputElement>) => {
    const nextStart = event.target.value;
    assistant.setStartDate(nextStart);
    if (!nextStart) return;
    const start = parseLocalISODate(nextStart);
    const end = parseLocalISODate(view.endDate);
    if (!start || !end) return;
    if (end.getTime() <= start.getTime()) {
      assistant.setEndDate(toLocalISODate(addDays(start, 1)));
    }
  };

  const handleEndDateChange = (event: ChangeEvent<HTMLInputElement>) => {
    const nextEnd = event.target.value;
    assistant.setEndDate(nextEnd);
    if (!nextEnd) return;
    const end = parseLocalISODate(nextEnd);
    const start = parseLocalISODate(view.startDate);
    if (!start || !end) return;
    if (end.getTime() <= start.getTime()) {
      assistant.setStartDate(toLocalISODate(addDays(end, -1)));
    }
  };

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
    <div className="fixed inset-0 z-[999] flex items-center justify-center bg-black/50 px-4">
      <div
        className="w-full max-w-[760px] max-h-[780px] h-[72vh] flex flex-col rounded-2xl bg-white dark:bg-[#111418] border border-gray-100 dark:border-gray-800 shadow-xl overflow-visible"
      >
        <div className="relative z-50 flex items-center justify-between px-6 py-4 border-b border-gray-100 dark:border-gray-800">
          <div className="flex items-center gap-4">
            <button
              className={`relative group size-9 rounded-full flex items-center justify-center ${
                view.mode === "add"
                  ? "bg-primary text-white"
                  : "bg-gray-100 dark:bg-gray-800 text-slate-500"
              }`}
              type="button"
              onClick={() => assistant.setMode("add")}
              aria-label="추가"
            >
              <Plus className="size-4" />
              <span className={tooltipClass}>
                일정 추가 모드
              </span>
            </button>
            <button
              className={`relative group size-9 rounded-full flex items-center justify-center ${
                view.mode === "delete"
                  ? "bg-red-500 text-white"
                  : "bg-gray-100 dark:bg-gray-800 text-slate-500"
              }`}
              type="button"
              onClick={() => assistant.setMode("delete")}
              aria-label="삭제"
            >
              <Trash2 className="size-4" />
              <span className={tooltipClass}>
                일정 삭제 모드
              </span>
            </button>
            <div className="h-6 w-px bg-gray-200 dark:bg-gray-700 mx-1" />
            <button
              className="relative group size-9 rounded-full flex items-center justify-center bg-gray-100 dark:bg-gray-800 text-slate-500 hover:text-primary"
              type="button"
              onClick={assistant.resetConversation}
              aria-label="대화 초기화"
            >
              <RotateCcw className="size-4" />
              <span className={tooltipClass}>
                초기화
              </span>
            </button>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[13px] font-semibold text-gray-600">추론</span>
            <div
              className="relative flex items-center rounded-full bg-gray-100 dark:bg-gray-800 p-0.5 segmented-toggle"
              style={
                {
                  "--seg-count": "2",
                  "--seg-index": view.reasoningEffort === "medium" ? "1" : "0",
                  "--seg-inset": "0.125rem",
                  "--seg-pad": "0.25rem",
                } as CSSProperties
              }
            >
              <span className="segmented-indicator">
                <span
                  key={view.reasoningEffort}
                  className="view-indicator-pulse block h-full w-full rounded-full bg-white dark:bg-gray-700 shadow-sm"
                />
              </span>
              {[
                { value: "low", icon: Rabbit, tooltip: "토끼 모드: 스피드 위주, 지능은 살짝 내려둠" },
                { value: "medium", icon: Turtle, tooltip: "거북이 모드: 느린 대신 두뇌 풀가동" },
              ].map(({ value, icon: Icon, tooltip }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => assistant.setReasoningEffort(value)}
                  aria-label={tooltip}
                  className={`relative z-10 group px-3 py-1 text-[11px] font-semibold rounded-full transition-colors ${
                    view.reasoningEffort === value
                      ? "text-slate-900 dark:text-white"
                      : "text-slate-500 hover:text-slate-900 dark:hover:text-white"
                  }`}
                >
                  <Icon className="size-4" />
                  <span className={tooltipClass}>
                    {tooltip}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
        <div className={`px-6 py-5 flex-1 min-h-0 flex flex-col gap-4 ${showEmptyState ? "justify-center" : ""}`}>
          {showConversation ? (
            <div className="flex-1 min-h-0 overflow-y-auto space-y-3 rounded-2xl border border-gray-100 dark:border-gray-800 bg-gray-50 dark:bg-[#0f1318] p-4">
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
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-xs font-semibold text-slate-500">
                    <span className="inline-flex size-6 items-center justify-center rounded-full bg-blue-100 text-primary">
                      <Sparkles className="size-3.5" />
                    </span>
                    <span>AI가 제안한 일정</span>
                  </div>
                  {view.error && <p className="text-xs text-red-500">{view.error}</p>}
                  {view.mode === "add" && addItems.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold text-slate-500">다음 일정이 감지되었습니다.</p>
                      {addItems.map((item, index) => {
                        const isSelected = view.selectedAddItems[index] ?? true;
                        return (
                        <div
                          key={`add-item-${index}`}
                            className={`relative flex items-center justify-between gap-4 overflow-hidden rounded-xl border p-3 pl-5 pr-5 transition-colors ${
                            isSelected
                              ? "border-blue-200 bg-blue-50/70 dark:border-blue-500/40 dark:bg-[#111418]"
                              : "border-gray-100 bg-slate-50/70 text-slate-400 dark:border-gray-700/50 dark:bg-[#1a2632]/70"
                          } cursor-pointer`}
                          onClick={() =>
                            assistant.setSelectedAddItems((prev) => ({
                              ...prev,
                              [index]: !isSelected,
                            }))
                          }
                        >
                          <div className="flex-1 space-y-2">
                            <div className="flex items-center justify-between gap-2">
                              <p className="text-sm font-semibold text-slate-900 dark:text-white">
                                {item.title}
                              </p>
                            </div>
                            {(() => {
                              const isRecurring = item.type === "recurring";
                              const startDate = parseISODateTime(item.start);
                              const dateLabel = isRecurring
                                ? formatRecurrenceDateLabel(item)
                                : startDate
                                  ? formatShortDate(startDate)
                                  : "";
                              const timeLabel = isRecurring
                                ? formatRecurrenceTimeLabel(item) || formatTimeRange(item.start, item.end)
                                : formatTimeRange(item.start, item.end);
                              const recurrenceSummary = isRecurring ? formatRecurrencePattern(item) : "";
                              const infoPills = [
                                dateLabel ? { key: "date", icon: Calendar, text: dateLabel } : null,
                                timeLabel ? { key: "time", icon: Clock, text: timeLabel } : null,
                                item.location ? { key: "location", icon: MapPin, text: item.location } : null,
                                recurrenceSummary
                                  ? { key: "recurrence", icon: RefreshCcw, text: recurrenceSummary }
                                  : null,
                              ].filter(Boolean);
                              const basePillClass =
                                "inline-flex items-center gap-1 rounded-full bg-white/90 px-2 py-1 text-[11px] font-medium text-slate-600 dark:bg-white/10 dark:text-slate-300";
                              return (
                                <>
                                  <div className="flex flex-wrap items-center gap-2">
                                    {infoPills.map((pill) => {
                                      if (!pill) return null;
                                      const Icon = pill.icon;
                                      return (
                                        <span
                                          key={pill.key}
                                          className={basePillClass}
                                        >
                                          <Icon className="size-3" />
                                          <span>{pill.text}</span>
                                        </span>
                                      );
                                    })}
                                  </div>
                                </>
                              );
                            })()}
                            {item.type !== "recurring" && item.samples && item.samples.length > 0 && (
                              <p className="text-[10px] text-slate-400 mt-1">
                                예시: {item.samples.slice(0, 3).join(", ")}
                              </p>
                            )}
                          </div>
                          <div className="relative z-10 flex shrink-0 items-center gap-2">
                            {onEditAddItem ? (
                              <button
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  onEditAddItem(item, index);
                                }}
                                className="flex size-7 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition-colors hover:border-slate-300 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:border-slate-700 dark:bg-[#111418] dark:text-slate-300 dark:hover:text-white dark:focus-visible:ring-blue-300 dark:focus-visible:ring-offset-[#111418]"
                                aria-label="일정 편집"
                              >
                                <Pencil className="size-4" />
                              </button>
                            ) : null}
                            <button
                              type="button"
                              role="checkbox"
                              aria-checked={isSelected}
                              onClick={(event) => {
                                event.stopPropagation();
                                assistant.setSelectedAddItems((prev) => ({
                                  ...prev,
                                  [index]: !isSelected,
                                }));
                              }}
                              className={`relative flex size-7 items-center justify-center rounded-full border transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2 focus-visible:ring-offset-white dark:focus-visible:ring-blue-300 dark:focus-visible:ring-offset-[#111418] ${
                                isSelected
                                  ? "border-blue-500 bg-blue-500 text-white dark:border-blue-400 dark:bg-blue-400"
                                  : "border-slate-300 bg-white text-slate-500 dark:border-slate-300 dark:bg-white dark:text-slate-500"
                              }`}
                            >
                              <Check className="size-4" />
                            </button>
                          </div>
                          {!isSelected ? (
                            <span
                              aria-hidden
                              className="pointer-events-none absolute inset-0 bg-white/60 dark:bg-white/10"
                            />
                          ) : null}
                        </div>
                        );
                      })}
                    </div>
                  )}
                  {view.mode === "delete" && deleteGroups.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-semibold text-slate-500">삭제 후보를 확인해주세요.</p>
                      {deleteGroups.map((group) => (
                        <label
                          key={group.group_key}
                          className="flex items-start justify-between gap-3 rounded-xl border border-gray-100 dark:border-gray-700/50 bg-slate-50 dark:bg-[#1a2632] p-3"
                        >
                          <div className="flex-1">
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
                          <input
                            className="mt-1 size-4 shrink-0 appearance-none rounded-full border border-slate-300 bg-white shadow-sm transition-colors checked:border-blue-500 checked:bg-blue-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-1 focus-visible:ring-offset-white dark:border-slate-600 dark:bg-[#111418] dark:checked:border-blue-400 dark:checked:bg-blue-400 dark:focus-visible:ring-blue-300 dark:focus-visible:ring-offset-[#111418]"
                            type="checkbox"
                            checked={view.selectedDeleteGroups[group.group_key] ?? true}
                            onChange={(event) =>
                              assistant.setSelectedDeleteGroups((prev) => ({
                                ...prev,
                                [group.group_key]: event.target.checked,
                              }))
                            }
                          />
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {assistant.progressLabel && (
                <div className="flex items-center gap-2 text-[13px] font-medium text-gray-500">
                  <span className="inline-flex size-6 items-center justify-center rounded-full bg-blue-100 text-primary">
                    <Sparkles className="size-3.5" />
                  </span>
                  {isThinking ? (
                    <span className="ai-thinking-text">
                      생각중
                      <span className="ai-thinking-dots">
                        <span className="dot dot-1">.</span>
                        <span className="dot dot-2">.</span>
                        <span className="dot dot-3">.</span>
                      </span>
                    </span>
                  ) : (
                    assistant.progressLabel
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="w-full text-center text-[18px] font-semibold leading-[1.6] text-gray-700">
              {emptyMessage}
            </div>
          )}
          <div
            className={`relative flex w-full overflow-hidden border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] shadow-sm ${
              isMultiline ? "flex-col rounded-3xl" : "flex-row items-center rounded-[28px]"
            }`}
          >
            <div
              className={`flex shrink-0 items-center ${
                isMultiline ? "order-2 w-full justify-between px-3 pb-3" : "order-1 pl-2"
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
                className={`max-h-[200px] w-full resize-none border-none bg-transparent text-[15px] font-normal leading-[1.6] text-gray-900 dark:text-slate-100 placeholder:text-gray-400 placeholder:text-[14px] placeholder:font-normal outline-none focus:outline-none focus:ring-0 ${
                  isMultiline ? "p-4 pb-0 overflow-y-auto" : "pt-3.5 pb-2.5 px-3 overflow-hidden"
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
              <div className="order-3 pr-2">
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
            <span className="text-[12px] font-medium text-gray-500">
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
            <div className="flex flex-col gap-3">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                삭제 범위
              </span>
              <div className="grid gap-3 px-4 sm:grid-cols-2">
                <label className="flex flex-col gap-1 text-xs font-semibold text-slate-500">
                  시작 날짜
                  <input
                    type="date"
                    value={view.startDate}
                    max={maxStartDate || undefined}
                    onChange={handleStartDateChange}
                    className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] px-3 py-2 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </label>
                <label className="flex flex-col gap-1 text-xs font-semibold text-slate-500">
                  종료 날짜
                  <input
                    type="date"
                    value={view.endDate}
                    min={minEndDate || undefined}
                    onChange={handleEndDateChange}
                    className="rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-[#111418] px-3 py-2 text-sm text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-primary/40"
                  />
                </label>
              </div>
            </div>
          )}
        </div>
        <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-gray-100 dark:border-gray-800">
          <button
            className="px-4 py-2 rounded-full border border-gray-200 dark:border-gray-700 text-[14px] font-semibold text-gray-700 hover:text-gray-900 dark:hover:text-white"
            type="button"
            onClick={assistant.close}
          >
            취소
          </button>
          <button
            className={`px-4 py-2 rounded-full text-[14px] font-semibold transition-colors ${
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
        <style jsx>{`
          @keyframes ai-thinking-dot-2 {
            0%,
            32% {
              opacity: 0;
            }
            33%,
            100% {
              opacity: 1;
            }
          }
          @keyframes ai-thinking-dot-3 {
            0%,
            65% {
              opacity: 0;
            }
            66%,
            100% {
              opacity: 1;
            }
          }
          .ai-thinking-text {
            display: inline-flex;
            align-items: baseline;
            gap: 2px;
          }
          .ai-thinking-dots {
            display: inline-flex;
            align-items: baseline;
          }
          .ai-thinking-dots .dot {
            display: inline-block;
          }
          .ai-thinking-dots .dot-2 {
            animation: ai-thinking-dot-2 1.2s steps(1, end) infinite;
          }
          .ai-thinking-dots .dot-3 {
            animation: ai-thinking-dot-3 1.2s steps(1, end) infinite;
          }
        `}</style>
      </div>
    </div>
  );
}
