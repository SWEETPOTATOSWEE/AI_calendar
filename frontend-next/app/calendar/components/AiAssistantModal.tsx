"use client";

import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type CSSProperties,
  type PointerEvent,
  type ReactNode,
} from "react";
import {
  ArrowUp,
  Calendar,
  Check,
  Clock,
  Image as ImageIcon,
  RefreshCcw,
  MapPin,
  Pencil,
  Plus,
  Rabbit,
  RotateCcw,
  Sparkles,
  Square,
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
import { DatePopover } from "./EventModal";

export type AiAssistantModalProps = {
  assistant: ReturnType<typeof useAiAssistant>;
  onEditAddItem?: (item: AddPreviewItem, index: number) => void;
  variant?: "modal" | "drawer";
  showHeaderControls?: boolean;
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
const PLACEHOLDER_TEXT = {
  add: "추가/삭제할 일정을 입력하세요.",
  delete: "추가/삭제할 일정을 입력하세요.",
} as const;
const INPUT_ACTIONS = [{ type: "image", label: "이미지 추가" }] as const;

type MarkdownBlock =
  | { type: "heading"; level: number; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "quote"; text: string }
  | { type: "code"; text: string }
  | { type: "hr" };

const parseInlineMarkdown = (text: string) => {
  const parts = text.split(
    /(\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|~~[^~]+~~|`[^`]+`)/g
  );
  return parts.map((part, index) => {
    if (
      (part.startsWith("**") && part.endsWith("**")) ||
      (part.startsWith("__") && part.endsWith("__"))
    ) {
      return <strong key={`strong-${index}`}>{part.slice(2, -2)}</strong>;
    }
    if (
      (part.startsWith("*") && part.endsWith("*")) ||
      (part.startsWith("_") && part.endsWith("_"))
    ) {
      return <em key={`em-${index}`}>{part.slice(1, -1)}</em>;
    }
    if (part.startsWith("~~") && part.endsWith("~~")) {
      return <del key={`del-${index}`}>{part.slice(2, -2)}</del>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={`code-${index}`} className="rounded bg-slate-100 px-1 py-0.5 text-[12px]">
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={`text-${index}`}>{part}</span>;
  });
};

const parseMarkdownBlocks = (text: string): MarkdownBlock[] => {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];
  let code: string[] | null = null;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    blocks.push({ type: "paragraph", text: paragraph.join("\n") });
    paragraph = [];
  };

  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.trim().startsWith("```")) {
      if (code) {
        blocks.push({ type: "code", text: code.join("\n") });
        code = null;
      } else {
        flushParagraph();
        code = [];
      }
      index += 1;
      continue;
    }
    if (code) {
      code.push(line);
      index += 1;
      continue;
    }

    const headingMatch = line.match(/^(#{1,4})\s+(.*)$/);
    if (headingMatch) {
      flushParagraph();
      blocks.push({ type: "heading", level: headingMatch[1].length, text: headingMatch[2] });
      index += 1;
      continue;
    }

    const hrMatch = line.match(/^\s*(\*{3,}|-{3,}|_{3,})\s*$/);
    if (hrMatch) {
      flushParagraph();
      blocks.push({ type: "hr" });
      index += 1;
      continue;
    }

    const ulMatch = line.match(/^\s*[-*+]\s+(.*)$/);
    if (ulMatch) {
      flushParagraph();
      const items: string[] = [];
      while (index < lines.length) {
        const listLine = lines[index];
        const match = listLine.match(/^\s*[-*+]\s+(.*)$/);
        if (!match) break;
        items.push(match[1]);
        index += 1;
      }
      blocks.push({ type: "list", ordered: false, items });
      continue;
    }

    const olMatch = line.match(/^\s*\d+\.\s+(.*)$/);
    if (olMatch) {
      flushParagraph();
      const items: string[] = [];
      while (index < lines.length) {
        const listLine = lines[index];
        const match = listLine.match(/^\s*\d+\.\s+(.*)$/);
        if (!match) break;
        items.push(match[1]);
        index += 1;
      }
      blocks.push({ type: "list", ordered: true, items });
      continue;
    }

    const quoteMatch = line.match(/^\s*>\s?(.*)$/);
    if (quoteMatch) {
      flushParagraph();
      const quoteLines: string[] = [];
      while (index < lines.length) {
        const quoteLine = lines[index];
        const match = quoteLine.match(/^\s*>\s?(.*)$/);
        if (!match) break;
        quoteLines.push(match[1]);
        index += 1;
      }
      blocks.push({ type: "quote", text: quoteLines.join("\n") });
      continue;
    }

    if (line.trim() === "") {
      flushParagraph();
      index += 1;
      continue;
    }

    paragraph.push(line);
    index += 1;
  }
  flushParagraph();
  return blocks;
};

const renderMarkdown = (text: string, keyPrefix: string): ReactNode[] => {
  const blocks = parseMarkdownBlocks(text);
  return blocks.map((block, blockIndex) => {
    const key = `${keyPrefix}-${blockIndex}`;
    switch (block.type) {
      case "heading": {
        const headingClass =
          block.level === 1
            ? "text-[15px] font-semibold text-slate-900"
            : block.level === 2
              ? "text-[14px] font-semibold text-slate-900"
              : block.level === 3
                ? "text-[13px] font-semibold text-slate-900"
                : "text-[12px] font-semibold text-slate-900";
        return (
          <div key={key} className={headingClass}>
            {parseInlineMarkdown(block.text)}
          </div>
        );
      }
      case "list": {
        const listClass = block.ordered ? "list-decimal" : "list-disc";
        return (
          <ul key={key} className={`${listClass} space-y-1 pl-5`}>
            {block.items.map((item, itemIndex) => (
              <li key={`${key}-item-${itemIndex}`} className="leading-6 text-slate-700">
                {parseInlineMarkdown(item)}
              </li>
            ))}
          </ul>
        );
      }
      case "quote":
        return (
          <blockquote key={key} className="border-l-2 border-slate-200 pl-3 text-slate-600">
            {parseInlineMarkdown(block.text)}
          </blockquote>
        );
      case "code":
        return (
          <pre key={key} className="overflow-auto rounded-md bg-slate-900/90 p-3 text-[12px] text-slate-100">
            <code>{block.text}</code>
          </pre>
        );
      case "hr":
        return <hr key={key} className="border-0 border-t border-slate-200" />;
      case "paragraph":
      default:
        return (
          <p key={key} className="whitespace-pre-line leading-6 text-slate-700">
            {parseInlineMarkdown(block.text)}
          </p>
        );
    }
  });
};

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

export default function AiAssistantModal({
  assistant,
  onEditAddItem,
  variant = "modal",
  showHeaderControls = true,
}: AiAssistantModalProps) {
  const { visible, closing } = useAnimatedOpen(assistant.open);
  const isDrawer = variant === "drawer";
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
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [rangeOpen, setRangeOpen] = useState(false);
  const [imagePreview, setImagePreview] = useState<{ src: string; alt: string } | null>(null);
  const [focused, setFocused] = useState(false);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragStartRef = useRef({ x: 0, y: 0 });
  const dragOriginRef = useRef({ x: 0, y: 0 });
  const rangeButtonRef = useRef<HTMLButtonElement | null>(null);
  const rangePopoverRef = useRef<HTMLDivElement | null>(null);
  const isThinking = assistant.progressLabel === "생각 중";
  const hasAddPreview = addItems.length > 0;
  const hasDeletePreview = deleteGroups.length > 0;
  const hasPreview = hasAddPreview || hasDeletePreview;
  const hasPanels = Boolean(hasPreview || assistant.progressLabel || view.error);
  const showConversation = conversation.length > 0 || hasPanels;
  const handleStartDateChange = (nextStart: string) => {
    assistant.setStartDate(nextStart);
    if (!nextStart) return;
    const start = parseLocalISODate(nextStart);
    const end = parseLocalISODate(view.endDate);
    if (!start || !end) return;
    if (end.getTime() <= start.getTime()) {
      assistant.setEndDate(toLocalISODate(addDays(start, 1)));
    }
  };

  const handleEndDateChange = (nextEnd: string) => {
    assistant.setEndDate(nextEnd);
    if (!nextEnd) return;
    const end = parseLocalISODate(nextEnd);
    const start = parseLocalISODate(view.startDate);
    if (!start || !end) return;
    if (end.getTime() <= start.getTime()) {
      assistant.setStartDate(toLocalISODate(addDays(end, -1)));
    }
  };
  const handleImagePreview = (src: string, alt: string) => {
    setImagePreview({ src, alt });
  };

  useEffect(() => {
    if (view.mode === "delete") return;
    setRangeOpen(false);
  }, [view.mode]);

  useEffect(() => {
    if (!rangeOpen) return;
    const handleClick = (event: MouseEvent) => {
      const target = event.target as Node;
      if (rangeButtonRef.current?.contains(target)) return;
      if (rangePopoverRef.current?.contains(target)) return;
      setRangeOpen(false);
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [rangeOpen]);

  useEffect(() => {
    if (!imagePreview) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setImagePreview(null);
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [imagePreview]);

  useEffect(() => {
    if (!inputRef.current) return;
    inputRef.current.style.height = "auto";
    inputRef.current.style.height = `${inputRef.current.scrollHeight}px`;
  }, [view.text]);

  useEffect(() => {
    if (!dragging) return;
    const handlePointerMove = (event: PointerEvent) => {
      const dx = event.clientX - dragStartRef.current.x;
      const dy = event.clientY - dragStartRef.current.y;
      setDragOffset({
        x: dragOriginRef.current.x + dx,
        y: dragOriginRef.current.y + dy,
      });
    };
    const handlePointerUp = () => {
      setDragging(false);
    };
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };
  }, [dragging]);

  if (!visible) return null;

  const canSend = view.text.trim().length > 0 || view.attachments.length > 0;
  const handleSend = () => {
    if (!canSend || assistant.loading) return;
    assistant.preview();
  };
  const handleSendClick = () => {
    if (assistant.loading) {
      assistant.interrupt();
      return;
    }
    handleSend();
  };
  const handleAddAttachment = () => {
    fileInputRef.current?.click();
    setMenuOpen(false);
  };
  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    assistant.handleAttach(event.target.files);
    event.target.value = "";
    setMenuOpen(false);
  };
  const handleDragStart = (event: PointerEvent<HTMLDivElement>) => {
    if (isDrawer) return;
    if (event.button !== 0) return;
    event.preventDefault();
    dragStartRef.current = { x: event.clientX, y: event.clientY };
    dragOriginRef.current = dragOffset;
    setDragging(true);
  };

  return (
    <>
      <div
        className={
          isDrawer
            ? "flex h-full flex-col"
            : "fixed inset-0 z-[999] flex items-center justify-center px-4 pointer-events-none"
        }
      >
        <div
          className={
            isDrawer
              ? "flex h-full w-full flex-col bg-[#F9FAFB]"
            : "w-[46vh] max-w-[90vw] aspect-[9/16] flex flex-col rounded-[2.5rem] bg-white border border-gray-100 shadow-xl overflow-visible pointer-events-auto"
          }
          style={isDrawer ? undefined : { transform: `translate3d(${dragOffset.x}px, ${dragOffset.y}px, 0)` }}
        >
        {!isDrawer && (
          <div
            className="relative flex items-center justify-center pb-1 pt-3"
            onPointerDown={handleDragStart}
          >
            <div
              className="h-1.5 w-14 rounded-full bg-gray-200 cursor-grab active:cursor-grabbing select-none touch-none"
              aria-label="모달 이동"
              role="button"
            />
          </div>
        )}
        {showHeaderControls && (
          <div
            className={`relative z-50 flex items-center justify-between pb-0 ${
              isDrawer ? "px-3 pt-0 -mt-3" : "px-6 pt-4"
            }`}
          >
            <div className="flex items-center gap-4">
              <button
                className="relative group size-9 rounded-full flex items-center justify-center !bg-[#E5E7EB] text-slate-500 hover:text-primary"
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
          </div>
        )}
        <div
          className={`pb-0 pt-4 flex-1 min-h-0 flex flex-col gap-4 ${
            isDrawer ? "px-0" : "px-6"
          }`}
        >
          <div className="flex-1 min-h-0 bg-gray-50 flex flex-col overflow-visible">
            <div
              className={`flex-1 min-h-0 overflow-y-auto space-y-3 p-4 scrollbar-hidden ${
                showConversation ? "" : "flex items-center justify-center"
              }`}
            >
              {showConversation ? (
                <>
                  {conversation.map((msg, index) => {
                    const attachmentCount = Array.isArray(msg.attachments) ? msg.attachments.length : 0;
                    const showAttachments = msg.role === "user" && attachmentCount > 0;
                    const hideText = showAttachments && msg.text.trim() === "이미지 첨부";
                    const imageOnly = showAttachments && hideText;
                    const attachmentColumns = attachmentCount > 1 ? "grid-cols-2" : "grid-cols-1";
                    return (
                      <div
                        key={`${msg.role}-${index}`}
                        className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                      >
                        {imageOnly ? (
                          <div className={`inline-grid ${attachmentColumns} gap-2`}>
                            {msg.attachments?.map((item) => (
                              <button
                                key={item.id}
                                type="button"
                                className="h-24 w-24 overflow-hidden rounded-lg border border-black/10 cursor-zoom-in"
                                onClick={() => handleImagePreview(item.dataUrl, item.name)}
                                aria-label={`${item.name} 확대`}
                              >
                                <img
                                  src={item.dataUrl}
                                  alt={item.name}
                                  className="h-full w-full object-cover"
                                />
                              </button>
                            ))}
                          </div>
                        ) : (
                          <div
                            className={`flex flex-col ${
                              msg.role === "user" ? "items-end" : "items-start"
                            }`}
                          >
                            {showAttachments && (
                              <div className={`inline-grid ${attachmentColumns} gap-2 mb-2`}>
                                {msg.attachments?.map((item) => (
                                  <button
                                    key={item.id}
                                    type="button"
                                    className="h-24 w-24 overflow-hidden rounded-lg border border-black/10 cursor-zoom-in"
                                    onClick={() => handleImagePreview(item.dataUrl, item.name)}
                                    aria-label={`${item.name} 확대`}
                                  >
                                    <img
                                      src={item.dataUrl}
                                      alt={item.name}
                                      className="h-full w-full object-cover"
                                    />
                                  </button>
                                ))}
                              </div>
                            )}
                            {msg.role === "user" ? (
                              <div className="max-w-[70%] rounded-lg bg-gray-200 px-4 py-3 text-sm text-slate-800">
                                <p className="whitespace-pre-line">{msg.text}</p>
                              </div>
                            ) : (
                              <div className="w-full py-3 text-sm text-slate-700">
                                <div className="space-y-2">
                                  {renderMarkdown(msg.text, `${msg.role}-${index}`)}
                                  {index === assistant.conversation.length - 1 && assistant.loading && msg.text.trim() === "" && (
                                    <div className="flex items-center gap-1">
                                      <div className="size-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.3s]" />
                                      <div className="size-1.5 animate-bounce rounded-full bg-slate-400 [animation-delay:-0.15s]" />
                                      <div className="size-1.5 animate-bounce rounded-full bg-slate-400" />
                                    </div>
                                  )}
                                </div>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {assistant.permissionRequired && (
                    <div className="space-y-4 rounded-xl border border-blue-100 bg-blue-50/50 p-4">
                      <div className="flex gap-3">
                        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-blue-100 text-blue-600">
                          <Calendar className="size-4" />
                        </div>
                        <div className="flex-1 space-y-1">
                          <p className="text-sm font-semibold text-slate-900">
                            일정 정보를 읽어야 합니다
                          </p>
                          <p className="text-sm text-slate-600 leading-relaxed">
                            사용자의 일정을 파악하여 정확한 제안을 드리기 위해 캘린더 정보를 읽어야 합니다. 허락하시겠습니까?
                          </p>
                        </div>
                      </div>
                      <div className="flex gap-2 justify-end">
                        <button
                          onClick={assistant.denyPermission}
                          className="px-4 py-2 rounded-lg text-sm font-medium text-slate-600 hover:bg-white/50 transition-colors"
                        >
                          거절
                        </button>
                        <button
                          onClick={assistant.confirmPermission}
                          className="px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 text-white hover:bg-blue-700 transition-colors shadow-sm"
                        >
                          허락
                        </button>
                      </div>
                    </div>
                  )}
                  {(hasPreview || view.error) && (
                    <div className="space-y-2">
                      {hasPreview && (
                        <div className="flex items-center gap-2 text-xs font-semibold text-slate-500">
                          <span className="inline-flex size-6 items-center justify-center rounded-full bg-blue-100 text-primary">
                            <Sparkles className="size-3.5" />
                          </span>
                          <span>AI가 제안한 일정</span>
                        </div>
                      )}
                      {view.error && !hasPreview && <p className="text-xs text-red-500">{view.error}</p>}
                      {view.mode === "add" && addItems.length > 0 && (
                        <div className="space-y-2">
                          <p className="text-xs font-semibold text-slate-500">다음 일정이 감지되었습니다.</p>
                          {addItems.map((item, index) => {
                            const isSelected = view.selectedAddItems[index] ?? true;
                            return (
                              <div
                                key={`add-item-${index}`}
                                className={`relative flex items-center justify-between gap-4 overflow-hidden rounded-lg border p-3 pl-5 pr-5 transition-colors ${
                                  isSelected
                                    ? "border-blue-200 bg-blue-50/70"
                                    : "border-gray-100 bg-slate-50/70 text-slate-400"
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
                                    <p className="text-sm font-semibold text-slate-900">
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
                                      "inline-flex items-center gap-1 rounded-full bg-white/90 px-2 py-1 text-[11px] font-medium text-slate-600";
                                    return (
                                      <div className="flex flex-wrap items-center gap-2">
                                        {infoPills.map((pill) => {
                                          if (!pill) return null;
                                          const Icon = pill.icon;
                                          return (
                                            <span key={pill.key} className={basePillClass}>
                                              <Icon className="size-3" />
                                              <span>{pill.text}</span>
                                            </span>
                                          );
                                        })}
                                      </div>
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
                                      className="flex size-7 items-center justify-center rounded-full border border-slate-200 bg-white text-slate-500 transition-colors hover:border-slate-300 hover:text-slate-700 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2 focus-visible:ring-offset-white"
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
                                    className={`relative flex size-7 items-center justify-center rounded-full border transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2 focus-visible:ring-offset-white ${
                                      isSelected
                                        ? "border-blue-500 bg-blue-500 text-white"
                                        : "border-slate-300 bg-white text-slate-500"
                                    }`}
                                  >
                                    <Check className="size-4" />
                                  </button>
                                </div>
                                {!isSelected ? (
                                  <span
                                    aria-hidden
                                    className="pointer-events-none absolute inset-0 bg-white/60"
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
                              className="flex items-start justify-between gap-3 rounded-lg border border-gray-100 bg-slate-50 p-3"
                            >
                              <div className="flex-1">
                                <p className="text-sm font-semibold text-slate-900">{group.title}</p>
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
                                className="mt-1 size-4 shrink-0 appearance-none rounded-full border border-slate-300 bg-white shadow-sm transition-colors checked:border-blue-500 checked:bg-blue-500 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-1 focus-visible:ring-offset-white"
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
                      {(hasAddPreview || hasDeletePreview) && (
                        <div className="flex justify-end pt-2">
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
                      )}
                    </div>
                  )}
                  {assistant.progressLabel && (
                    <div className="flex items-center py-1">
                      <div 
                        className="m4s" 
                        data-text={assistant.progressLabel}
                      >
                        {assistant.progressLabel}
                      </div>
                    </div>
                  )}
                </>
              ) : null}
            </div>
            <div className="p-4 pt-2">
              <div className="rounded-[22px] border border-[#EEF2F6] bg-white px-3 py-2 shadow-[0_1px_2px_rgba(15,23,42,0.06)]">
                <textarea
                  ref={inputRef}
                  rows={1}
                  value={view.text}
                  placeholder={PLACEHOLDER_TEXT[view.mode]}
                  onChange={(event) => assistant.setText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      handleSend();
                    }
                  }}
                  onFocus={() => setFocused(true)}
                  onBlur={() => setFocused(false)}
                  className="max-h-[8lh] w-full resize-none bg-white px-2.5 py-2 text-sm text-[#1c1b18] placeholder:text-[#b3aaa1] focus:outline-none"
                />

                {view.attachments.length > 0 && (
                  <div className="flex flex-wrap gap-2 px-2 pb-2">
                    {view.attachments.map((item) => (
                      <span
                        key={item.id}
                        className="inline-flex items-center gap-2 rounded-full border border-[#e6dfd6] bg-[#f7f4ef] px-3 py-1 text-xs text-[#6d655e]"
                      >
                        <ImageIcon className="size-3.5" />
                        {item.name}
                        <button
                          type="button"
                          className="rounded-full p-0.5 hover:bg-white"
                          onClick={() => assistant.removeAttachment(item.id)}
                          aria-label="첨부 제거"
                        >
                          <X className="size-3.5" />
                        </button>
                      </span>
                    ))}
                  </div>
                )}

                <div className="flex flex-wrap items-center justify-between gap-3 px-2 pb-2">
                  <div className="flex items-center gap-2">
                    <div className="relative">
                      <button
                        type="button"
                        className="flex size-8 items-center justify-center rounded-full border border-[#e6dfd6] bg-white text-[#1c1b18] shadow-sm transition hover:bg-[#f7f4ef]"
                        onClick={() => setMenuOpen((prev) => !prev)}
                        aria-label="첨부 메뉴"
                      >
                        <Plus className="size-4" />
                      </button>
                      <input
                        ref={fileInputRef}
                        className="hidden"
                        type="file"
                        accept="image/*"
                        multiple
                        onChange={handleFileChange}
                      />
                      {menuOpen && (
                        <div className="absolute left-0 top-full z-10 mt-2 w-44 rounded-2xl border border-[#e6dfd6] bg-white p-1 text-sm shadow-lg">
                          {INPUT_ACTIONS.map((action) => (
                            <button
                              key={action.type}
                              type="button"
                              className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm text-[#2a2622] transition hover:bg-[#f7f4ef]"
                              onClick={handleAddAttachment}
                            >
                              <ImageIcon className="size-4" />
                              {action.label}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    <div
                      className="relative flex items-center rounded-full bg-gray-100 p-0.5 segmented-toggle"
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
                          className="view-indicator-pulse block h-full w-full rounded-full bg-white shadow-sm"
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
                          className={`relative z-10 group flex size-8 items-center justify-center rounded-full text-[11px] font-semibold transition-colors ${
                            view.reasoningEffort === value
                              ? "text-slate-900"
                              : "text-slate-500 hover:text-slate-900"
                          }`}
                        >
                          <Icon className="size-4" />
                          <span className={tooltipClass}>
                            {tooltip}
                          </span>
                        </button>
                      ))}
                    </div>
                    {view.mode === "delete" && (
                      <div className="relative">
                        <button
                          ref={rangeButtonRef}
                          type="button"
                          className="flex size-8 items-center justify-center rounded-full border border-[#e6dfd6] bg-white text-[#1c1b18] shadow-sm transition hover:bg-[#f7f4ef]"
                          aria-haspopup="dialog"
                          aria-expanded={rangeOpen}
                          onClick={() => setRangeOpen((prev) => !prev)}
                          aria-label="삭제 범위 설정"
                        >
                          <Calendar className="size-4" />
                        </button>
                        {rangeOpen && (
                          <div
                            ref={rangePopoverRef}
                            className="absolute left-0 bottom-full z-20 mb-2 w-max rounded-xl border border-[#e6dfd6] bg-white p-3 shadow-lg"
                          >
                            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                              삭제 범위
                            </div>
                            <div className="mt-2 flex items-center gap-2">
                              <DatePopover
                                label="시작 날짜"
                                value={view.startDate}
                                onChange={handleStartDateChange}
                                placeholder="날짜 선택"
                                icon={<Calendar className="size-4" />}
                                disabled={false}
                              />
                              <span className="text-xs font-semibold text-slate-400">~</span>
                              <DatePopover
                                label="종료 날짜"
                                value={view.endDate}
                                onChange={handleEndDateChange}
                                placeholder="날짜 선택"
                                icon={<Calendar className="size-4" />}
                                disabled={false}
                              />
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  <button
                    type="button"
                    className={`inline-flex size-9 items-center justify-center rounded-full text-xs font-semibold transition ${
                      assistant.loading
                        ? "bg-[#1c1b18] text-white cursor-pointer"
                        : canSend
                        ? "bg-[#1c1b18] text-white hover:-translate-y-0.5 cursor-pointer"
                        : "bg-[#1c1b18]/40 text-white"
                    }`}
                    onClick={handleSendClick}
                    disabled={!assistant.loading && !canSend}
                    aria-label={assistant.loading ? "중단" : "보내기"}
                  >
                    {assistant.loading ? <Square className="size-3.5" /> : <ArrowUp className="size-3.5" />}
                  </button>
                </div>
              </div>
            </div>
          </div>
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
          .scrollbar-hidden {
            -ms-overflow-style: none;
            scrollbar-width: none;
          }
          .scrollbar-hidden::-webkit-scrollbar {
            display: none;
          }
          .m4s {
            font-weight: 500;
            font-size: 14px;
            line-height: 1.1;
            display: inline-block;
            background: linear-gradient(
              90deg, 
              rgba(0,0,0,0.55) 0%, 
              rgba(0,0,0,0.55) 40%, 
              rgba(0,0,0,0.9) 50%, 
              rgba(0,0,0,0.55) 60%, 
              rgba(0,0,0,0.55) 100%
            );
            background-size: 200% auto;
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            animation: m4s-shimmer 2.5s linear infinite;
          }
          @keyframes m4s-shimmer {
            from { background-position: 200% center; }
            to { background-position: 0% center; }
          }
          @media (prefers-reduced-motion: reduce) {
            .m4s { animation: none; }
          }
        `}</style>
        </div>
      </div>
      {imagePreview && (
        <div className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/60">
          <button
            type="button"
            className="absolute inset-0 cursor-zoom-out"
            onClick={() => setImagePreview(null)}
            aria-label="이미지 닫기"
          />
          <div className="relative z-10">
            <img
              src={imagePreview.src}
              alt={imagePreview.alt}
              className="max-h-[80vh] max-w-[90vw] object-contain shadow-2xl"
            />
            <button
              type="button"
              onClick={() => setImagePreview(null)}
              aria-label="이미지 닫기"
              className="absolute -right-3 -top-3 flex size-8 items-center justify-center rounded-full bg-white text-slate-600 shadow-lg transition hover:text-slate-900"
            >
              <X className="size-4" />
            </button>
          </div>
        </div>
      )}
    </>
  );
}
