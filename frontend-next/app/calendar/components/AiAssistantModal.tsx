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
  formatRecurrenceSummary,
  formatRecurrenceTimeLabel,
} from "../lib/recurrence-summary";
import { useAiAssistant, type AddPreviewItem } from "../lib/use-ai-assistant";
import { DatePopover } from "./DatePopover";

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
  const parts = text.split(/(\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|~~[^~]+~~|`[^`]+`)/g);
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
        <code key={`code-${index}`} className="rounded bg-subtle px-1 py-0.5 text-[12px] text-text-primary">
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
        return (
          <div key={key} className="text-[14px] font-semibold text-text-primary">
            {parseInlineMarkdown(block.text)}
          </div>
        );
      }
      case "list": {
        const listClass = block.ordered ? "list-decimal" : "list-disc";
        return (
          <ul key={key} className={`${listClass} space-y-1 pl-5`}>
            {block.items.map((item, itemIndex) => (
              <li key={`${key}-item-${itemIndex}`} className="leading-6 text-text-primary">
                {parseInlineMarkdown(item)}
              </li>
            ))}
          </ul>
        );
      }
      case "quote":
        return (
          <blockquote key={key} className="border-l-2 border-border-subtle pl-3 text-text-primary">
            {parseInlineMarkdown(block.text)}
          </blockquote>
        );
      case "code":
        return (
          <pre key={key} className="overflow-auto rounded-md bg-subtle p-3 text-[12px] text-text-primary">
            <code>{block.text}</code>
          </pre>
        );
      case "hr":
        return <hr key={key} className="border-0 border-t border-border-subtle" />;
      case "paragraph":
      default:
        return (
          <p key={key} className="whitespace-pre-line leading-6 text-text-primary">
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

export default function AiAssistantModal({
  assistant,
  onEditAddItem,
  variant = "modal",
  showHeaderControls = true,
}: AiAssistantModalProps) {
  const isDrawer = variant === "drawer";
  const tooltipClass =
    "pointer-events-none absolute left-1/2 top-full z-[9999] mt-2 w-max max-w-[360px] -translate-x-1/2 rounded-full border border-border-strong bg-subtle px-3 py-1 text-[12px] font-medium leading-[1.4] text-text-primary opacity-0 transition-opacity group-hover:opacity-100 group-disabled:opacity-100 group-disabled:text-text-disabled whitespace-nowrap text-center";

  const [snapshot, setSnapshot] = useState(() => buildSnapshot(assistant));
  useEffect(() => {
    if (!assistant.open) return;
    setSnapshot(buildSnapshot(assistant));
  }, [assistant.open, assistant.mode, assistant.reasoningEffort, assistant.model, assistant.startDate, assistant.endDate, assistant.attachments, assistant.conversation, assistant.loading, assistant.error, assistant.addPreview, assistant.deletePreview, assistant.selectedAddItems, assistant.selectedDeleteGroups]);

  const view = snapshot;
  const conversation = safeArray(view.conversation);
  const addItems = safeArray(view.addPreview?.items);
  const deleteGroups = safeArray(view.deletePreview?.groups);
  const selectedAddCount = addItems.filter((_, index) => view.selectedAddItems[index]).length;
  const selectedDeleteCount = deleteGroups.filter((group) => view.selectedDeleteGroups[group.group_key]).length;

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [rangeOpen, setRangeOpen] = useState(false);
  const [imagePreview, setImagePreview] = useState<{ src: string; alt: string } | null>(null);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragStartRef = useRef({ x: 0, y: 0 });
  const dragOriginRef = useRef({ x: 0, y: 0 });
  const rangeButtonRef = useRef<HTMLButtonElement | null>(null);
  const rangePopoverRef = useRef<HTMLDivElement | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const isComposingRef = useRef(false);

  const hasPreview = addItems.length > 0 || deleteGroups.length > 0;
  const showConversation = conversation.length > 0 || hasPreview || assistant.progressLabel || view.error;

  const handleStartDateChange = (nextStart: string) => {
    assistant.setStartDate(nextStart);
    const start = parseLocalISODate(nextStart);
    const end = parseLocalISODate(view.endDate);
    if (start && end && end.getTime() <= start.getTime()) {
      assistant.setEndDate(toLocalISODate(addDays(start, 1)));
    }
  };

  const handleEndDateChange = (nextEnd: string) => {
    assistant.setEndDate(nextEnd);
    const end = parseLocalISODate(nextEnd);
    const start = parseLocalISODate(view.startDate);
    if (start && end && end.getTime() <= start.getTime()) {
      assistant.setStartDate(toLocalISODate(addDays(end, -1)));
    }
  };

  useEffect(() => {
    if (showConversation) {
      scrollRef.current?.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [conversation, hasPreview, assistant.progressLabel, view.error, showConversation]);

  useEffect(() => {
    if (!rangeOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (!rangeButtonRef.current?.contains(e.target as Node) && !rangePopoverRef.current?.contains(e.target as Node)) {
        setRangeOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [rangeOpen]);

  useEffect(() => {
    if (!inputRef.current) return;
    inputRef.current.style.height = "auto";
    inputRef.current.style.height = `${inputRef.current.scrollHeight}px`;
  }, [assistant.text]);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: globalThis.PointerEvent) => {
      setDragOffset({
        x: dragOriginRef.current.x + (e.clientX - dragStartRef.current.x),
        y: dragOriginRef.current.y + (e.clientY - dragStartRef.current.y),
      });
    };
    const onUp = () => setDragging(false);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [dragging]);

  if (!assistant.open) return null;

  const canSend = assistant.text.trim().length > 0;
  const handleSend = () => {
    if (canSend && !assistant.loading) assistant.preview();
  };

  return (
    <>
      <div className={isDrawer ? "flex h-full flex-col bg-surface overflow-hidden" : "fixed inset-0 z-[999] flex items-center justify-center md:px-4 pointer-events-none"}>
        <div 
          className={isDrawer ? "flex h-full w-full flex-col overflow-hidden" : "w-full h-full md:w-[420px] md:max-w-[90vw] md:h-[760px] md:max-h-[90vh] flex flex-col md:rounded-[2.5rem] bg-canvas border border-border-subtle shadow-xl overflow-hidden pointer-events-auto"}
          style={isDrawer ? undefined : { transform: typeof window !== 'undefined' && window.innerWidth < 768 ? undefined : `translate3d(${dragOffset.x}px, ${dragOffset.y}px, 0)` }}
        >
          {!isDrawer && (
            <div className="relative hidden md:flex items-center justify-center pb-1 pt-3 flex-shrink-0" onPointerDown={(e) => {
              if (e.button !== 0) return;
              e.preventDefault();
              dragStartRef.current = { x: e.clientX, y: e.clientY };
              dragOriginRef.current = dragOffset;
              setDragging(true);
            }}>
              <div className="h-1.5 w-14 rounded-full bg-border-subtle cursor-grab active:cursor-grabbing" role="button" />
            </div>
          )}
          
          {showHeaderControls && (
            <div className={`flex-shrink-0 relative z-50 flex items-center justify-between bg-bg-surface ${isDrawer ? "px-3 pt-0" : "px-6 pt-4"}`}>
              <button
                className="size-9 rounded-full flex items-center justify-center bg-subtle text-text-secondary hover:text-token-primary transition-colors group relative"
                onClick={assistant.resetConversation}
              >
                <RotateCcw className="size-4" />
                <span className={tooltipClass}>초기화</span>
              </button>
              {!isDrawer && (
                <button
                  className="size-9 rounded-full flex items-center justify-center bg-subtle text-text-secondary hover:text-text-primary transition-colors md:hidden"
                  onClick={() => assistant.setOpen(false)}
                >
                  <X className="size-5" />
                </button>
              )}
            </div>
          )}

          <div className="flex-1 min-h-0 flex flex-col overflow-hidden bg-surface">
            <div 
              ref={scrollRef}
              className={`flex-1 min-h-0 overflow-y-auto space-y-3 p-4 ${showConversation ? "" : "flex items-center justify-center"}`}
            >
              {showConversation ? (
                <>
                  {conversation.map((msg, index) => {
                    const attachments = safeArray(msg.attachments);
                    const isUser = msg.role === "user";
                    return (
                      <div key={`${msg.role}-${index}`} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
                        <div className={`flex flex-col ${isUser ? "items-end max-w-[85%]" : "items-start w-full"}`}>
                          {attachments.length > 0 && (
                            <div className={`inline-grid ${attachments.length > 1 ? "grid-cols-2" : "grid-cols-1"} gap-2 mb-2`}>
                              {attachments.map((item) => (
                                <button key={item.id} className="h-24 w-24 overflow-hidden rounded-lg border border-border-subtle cursor-zoom-in" onClick={() => setImagePreview({ src: item.dataUrl, alt: item.name })}>
                                  <img src={item.dataUrl} alt={item.name} className="h-full w-full object-cover" />
                                </button>
                              ))}
                            </div>
                          )}
                          {isUser ? (
                            <div className="rounded-xl bg-subtle px-4 py-2.5 text-sm text-text-primary">
                              <p className="whitespace-pre-wrap break-words">{msg.text}</p>
                            </div>
                          ) : msg.text.trim() ? (
                            <div className="py-2 text-sm text-text-primary">
                              <div className="space-y-2">
                                {renderMarkdown(msg.text, `${msg.role}-${index}`)}
                              </div>
                            </div>
                          ) : null}
                        </div>
                      </div>
                    );
                  })}

                  {assistant.permissionRequired && (
                    <div className="space-y-4 rounded-xl border border-border-subtle bg-subtle/50 p-4">
                      <div className="flex gap-3 text-text-primary">
                        <Calendar className="size-4 mt-0.5" />
                        <div className="flex-1 space-y-1">
                          <p className="text-sm font-semibold">일정 정보를 읽어야 합니다</p>
                          <p className="text-sm text-text-secondary leading-relaxed">자세한 제안을 위해 캘린더 정보를 읽어야 합니다. 허락하시겠습니까?</p>
                        </div>
                      </div>
                      <div className="flex gap-2 justify-end">
                        <button onClick={assistant.denyPermission} className="px-4 py-2 rounded-lg text-sm font-medium text-text-secondary hover:bg-subtle transition-colors">거절</button>
                        <button onClick={assistant.confirmPermission} className="px-4 py-2 rounded-lg text-sm font-medium bg-token-primary text-white hover:opacity-90 transition-colors">허락</button>
                      </div>
                    </div>
                  )}

                  {(hasPreview || view.error) && (
                    <div className="space-y-2">
                      {view.error && <p className="text-xs text-token-error">{view.error}</p>}
                      {addItems.map((item, idx) => {
                        const isSelected = view.selectedAddItems[idx] ?? true;
                        const isRecurring = item.type === "recurring";
                        const dateLabel = isRecurring
                          ? formatRecurrenceSummary(item)
                          : item.start
                          ? `${formatShortDate(parseISODateTime(item.start) || new Date())} ${formatTime(item.start)}`
                          : "";

                        return (
                          <div key={idx} onClick={() => assistant.setSelectedAddItems(p => ({ ...p, [idx]: !isSelected }))}
                            className={`relative flex flex-col gap-2 rounded-lg border p-3 transition-colors cursor-pointer ${isSelected ? "border-token-primary bg-token-primary-low/10" : "border-border-subtle bg-subtle opacity-60"}`}>
                            <div className="flex justify-between items-start gap-2">
                              <p className="text-sm font-medium text-text-primary">{item.title}</p>
                              <div className="flex gap-1.5 shrink-0">
                                {onEditAddItem && <button onClick={(e) => { e.stopPropagation(); onEditAddItem(item, idx); }} className="size-7 flex items-center justify-center rounded-full border border-border-subtle bg-canvas text-text-secondary hover:text-text-primary transition-colors"><Pencil className="size-3.5" /></button>}
                                <div className={`size-7 flex items-center justify-center rounded-full border ${isSelected ? "bg-token-primary border-token-primary text-white" : "bg-canvas border-border-subtle text-text-disabled"}`}><Check className="size-4" /></div>
                              </div>
                            </div>
                            <div className="flex flex-wrap items-center gap-1.5">
                              {[
                                dateLabel && { icon: Calendar, text: dateLabel },
                                item.location && { icon: MapPin, text: item.location }
                              ].filter(Boolean).map((p: any, i) => (
                                <span key={i} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-canvas/50 text-[10px] font-medium leading-none text-text-secondary border border-border-subtle/30"><p.icon className="size-3" />{p.text}</span>
                              ))}
                            </div>
                          </div>
                        );
                      })}
                      {deleteGroups.map((group) => (
                        <label key={group.group_key} className="flex items-center justify-between gap-3 rounded-lg border border-border-subtle bg-subtle p-3 cursor-pointer">
                          <div className="flex-1 min-w-0">
                            <p className="text-sm font-medium text-text-primary truncate">{group.title}</p>
                            <p className="text-xs font-medium text-text-secondary">{group.count || 0}건 · {group.time}</p>
                          </div>
                          <input type="checkbox" className="size-5 shrink-0 accent-token-primary" checked={view.selectedDeleteGroups[group.group_key] ?? true} onChange={(e) => assistant.setSelectedDeleteGroups(p => ({ ...p, [group.group_key]: e.target.checked }))} />
                        </label>
                      ))}
                      {(hasPreview) && (
                        <div className="flex justify-end pt-1">
                          <button onClick={assistant.apply} className={`px-5 py-2 rounded-full text-sm font-semibold transition-opacity ${view.mode === "delete" ? "bg-token-error" : "bg-token-primary"} text-white ${(view.mode === "add" && selectedAddCount === 0) || (view.mode === "delete" && selectedDeleteCount === 0) ? "opacity-30 pointer-events-none" : "hover:opacity-90"}`}>
                            {view.mode === "delete" ? `${selectedDeleteCount}건 삭제` : `${selectedAddCount}건 추가`}
                          </button>
                        </div>
                      )}
                    </div>
                  )}

                  {assistant.progressLabel && (
                    <div className="flex items-center py-1">
                      <div className="text-sm text-text-primary opacity-70 italic">{assistant.progressLabel}...</div>
                    </div>
                  )}
                </>
              ) : (
                <div className="text-center space-y-4 opacity-40">
                  <Sparkles className="size-8 mx-auto text-text-disabled" />
                  <p className="text-sm text-text-secondary">어떤 일정을 도와드릴까요?</p>
                </div>
              )}
            </div>

            <div className="flex-shrink-0 p-4 pt-2 bg-surface education-input-area">
              <div className="relative rounded-[22px] border border-border-subtle bg-canvas px-1 shadow-sm focus-within:border-border-strong transition-colors">
                <textarea
                  ref={inputRef}
                  rows={1}
                  value={assistant.text}
                  placeholder={PLACEHOLDER_TEXT[assistant.mode]}
                  onChange={(e) => assistant.setText(e.target.value)}
                  onCompositionStart={() => {
                    isComposingRef.current = true;
                  }}
                  onCompositionEnd={() => {
                    isComposingRef.current = false;
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !isComposingRef.current && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      handleSend();
                    }
                  }}
                  className="max-h-[8lh] w-full resize-none bg-transparent px-4 py-3 text-sm text-text-primary placeholder:text-text-disabled focus:outline-none"
                />

                <div className="flex items-center justify-between px-3 pb-3 gap-2">
                  <div className="flex items-center gap-1.5">
                    <div className="relative">
                      <button className="size-8 flex items-center justify-center rounded-full border border-border-subtle bg-subtle text-text-secondary hover:text-text-primary transition-colors" onClick={() => setMenuOpen(!menuOpen)}>
                        <Plus className="size-4" />
                      </button>
                      {menuOpen && (
                        <div className="absolute left-0 bottom-full mb-2 w-40 rounded-2xl border border-border-subtle bg-canvas p-1 shadow-xl z-[100]">
                          <button className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-text-primary hover:bg-subtle transition-colors" onClick={() => (fileInputRef.current?.click(), setMenuOpen(false))}>
                            <ImageIcon className="size-4" />이미지 추가
                          </button>
                        </div>
                      )}
                      <input ref={fileInputRef} className="hidden" type="file" accept="image/*" multiple onChange={(e) => (assistant.handleAttach(e.target.files), e.target.value = "")} />
                    </div>

                    <div className="flex items-center bg-subtle rounded-full p-1 border border-border-subtle/50">
                      {[{ v: "low", i: Rabbit, t: "빠름" }, { v: "medium", i: Turtle, t: "지능" }].map(opt => (
                        <button key={opt.v} onClick={() => assistant.setReasoningEffort(opt.v as any)} className={`size-7 flex items-center justify-center rounded-full transition-all ${view.reasoningEffort === opt.v ? "bg-canvas text-text-primary shadow-sm" : "text-text-disabled hover:text-text-secondary"}`}>
                          <opt.i className="size-3.5" />
                        </button>
                      ))}
                    </div>

                    {view.mode === "delete" && (
                      <div className="relative">
                        <button ref={rangeButtonRef} className="size-8 flex items-center justify-center rounded-full border border-border-subtle bg-subtle text-text-secondary hover:text-text-primary transition-colors" onClick={() => setRangeOpen(!rangeOpen)}>
                          <Calendar className="size-4" />
                        </button>
                        {rangeOpen && (
                          <div ref={rangePopoverRef} className="absolute left-0 bottom-full mb-2 w-max rounded-2xl border border-border-subtle bg-canvas p-4 shadow-xl z-[100] space-y-3">
                            <p className="text-[11px] font-bold text-text-secondary uppercase tracking-wider">삭제 범위 설정</p>
                            <div className="flex items-center gap-2">
                              <DatePopover value={view.startDate} onChange={handleStartDateChange} />
                              <span className="text-text-disabled">~</span>
                              <DatePopover value={view.endDate} onChange={handleEndDateChange} />
                            </div>
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                  <button
                    onClick={() => assistant.loading ? assistant.interrupt() : handleSend()}
                    disabled={!assistant.loading && !canSend}
                    className={`size-9 flex items-center justify-center rounded-full transition-all ${assistant.loading ? "bg-text-primary text-bg-canvas" : canSend ? "bg-token-primary text-white hover:opacity-90 active:scale-95" : "bg-text-disabled/20 text-text-disabled opacity-50"}`}
                  >
                    {assistant.loading ? <Square className="size-3.5 fill-current" /> : <ArrowUp className="size-4" />}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <style jsx>{`
        .scrollbar-hidden { -ms-overflow-style: none; scrollbar-width: none; }
        .scrollbar-hidden::-webkit-scrollbar { display: none; }
        .m4s {
          background: linear-gradient(90deg, var(--text-primary) 0%, var(--text-secondary) 50%, var(--text-primary) 100%);
          background-size: 200% auto;
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          animation: shim 2s linear infinite;
        }
        @keyframes shim { to { background-position: 200% center; } }
      `}</style>

      {imagePreview && (
        <div className="fixed inset-0 z-[10000] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4" onClick={() => setImagePreview(null)}>
          <div className="relative max-w-full max-h-full" onClick={e => e.stopPropagation()}>
            <img src={imagePreview.src} alt={imagePreview.alt} className="max-h-[90vh] rounded-2xl shadow-2xl" />
            <button className="absolute -top-12 right-0 size-8 flex items-center justify-center bg-white/10 rounded-full text-white hover:bg-white/20 transition-colors" onClick={() => setImagePreview(null)}>
              <X className="size-5" />
            </button>
          </div>
        </div>
      )}
    </>
  );
}
