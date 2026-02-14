"use client";

import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type CSSProperties,
  type PointerEvent,
} from "react";
import {
  ArrowUp,
  Calendar,
  Check,
  Image as ImageIcon,
  MapPin,
  Pencil,
  Plus,
  RotateCcw,
  Sparkles,
  Square,
  X,
} from "lucide-react";
import { formatShortDate, formatTime, formatTimeRange, parseISODateTime } from "../lib/date";
import ReactMarkdown, { type Components } from "react-markdown";
import {
  formatRecurrenceDateLabel,
  formatRecurrencePattern,
  formatRecurrenceSummary,
  formatRecurrenceTimeLabel,
} from "../lib/recurrence-summary";
import { useAiAssistant, type AddPreviewItem } from "../lib/use-ai-assistant";

export type AiAssistantModalProps = {
  assistant: ReturnType<typeof useAiAssistant>;
  onEditAddItem?: (item: AddPreviewItem, index: number) => void;
  variant?: "modal" | "drawer";
  showHeaderControls?: boolean;
};

const safeArray = <T,>(value?: T[] | null) => (Array.isArray(value) ? value : []);

const PLACEHOLDER_TEXT = "일정/할 일 또는 질문을 입력하세요";

const markdownComponents: Components = {
  h1: ({ children }) => <h1 className="text-[20px] leading-7 font-semibold text-text-primary">{children}</h1>,
  h2: ({ children }) => <h2 className="text-[18px] leading-7 font-semibold text-text-primary">{children}</h2>,
  h3: ({ children }) => <h3 className="text-[16px] leading-6 font-semibold text-text-primary">{children}</h3>,
  h4: ({ children }) => <h4 className="text-[14px] leading-6 font-semibold text-text-primary">{children}</h4>,
  p: ({ children }) => <p className="whitespace-pre-line leading-6 text-text-primary">{children}</p>,
  ul: ({ children }) => <ul className="list-disc space-y-1 pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal space-y-1 pl-5">{children}</ol>,
  li: ({ children }) => <li className="leading-6 text-text-primary">{children}</li>,
  blockquote: ({ children }) => (
    <blockquote className="border-l-2 border-border-subtle pl-3 text-text-primary">{children}</blockquote>
  ),
  pre: ({ children }) => (
    <pre className="overflow-auto rounded-md bg-subtle p-3 text-[12px] text-text-primary">{children}</pre>
  ),
  code: ({ children, className }) => {
    const isBlockCode = (className ?? "").includes("language-") || String(children).includes("\n");
    if (isBlockCode) {
      return <code className={className}>{children}</code>;
    }
    return <code className="rounded bg-subtle px-1 py-0.5 text-[12px] text-text-primary">{children}</code>;
  },
  hr: () => <hr className="border-0 border-t border-border-subtle" />,
};

type ConversationUiMessage = {
  role: "user" | "assistant";
  text: string;
  attachments?: Array<{ id: string; name: string; dataUrl: string }>;
};

type ConversationMessageRowProps = {
  message: ConversationUiMessage;
  index: number;
  onOpenImage: (src: string, alt: string) => void;
};

function ConversationMessageRow({ message, index, onOpenImage }: ConversationMessageRowProps) {
  const attachments = safeArray(message.attachments);
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`flex flex-col ${isUser ? "items-end max-w-[85%]" : "items-start w-full"}`}>
        {attachments.length > 0 && (
          <div className={`inline-grid ${attachments.length > 1 ? "grid-cols-2" : "grid-cols-1"} gap-2 mb-2`}>
            {attachments.map((item) => (
              <button
                key={item.id}
                className="h-24 w-24 overflow-hidden rounded-lg border border-border-subtle cursor-zoom-in"
                onClick={() => onOpenImage(item.dataUrl, item.name)}
              >
                <img src={item.dataUrl} alt={item.name} className="h-full w-full object-cover" />
              </button>
            ))}
          </div>
        )}
        {isUser ? (
          <div className="rounded-xl bg-subtle px-4 py-2.5 text-sm text-text-primary">
            <p className="whitespace-pre-wrap break-words">{message.text}</p>
          </div>
        ) : message.text.trim() ? (
          <div className="py-2 text-sm text-text-primary">
            <div className="space-y-2">
              <ReactMarkdown components={markdownComponents}>{message.text}</ReactMarkdown>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Syntax-highlight a JSON string for display.
 * Keys ??blue, strings ??green, numbers ??orange, booleans/null ??purple.
 */
function highlightJson(json: string): string {
  return json
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(
      /("(?:\\.|[^"\\])*")\s*:/g,
      '<span style="color:#6ab0f3">$1</span>:',
    )
    .replace(
      /:\s*("(?:\\.|[^"\\])*")/g,
      ': <span style="color:#98c379">$1</span>',
    )
    .replace(
      /:\s*(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
      ': <span style="color:#d19a66">$1</span>',
    )
    .replace(
      /:\s*(true|false|null)/g,
      ': <span style="color:#c678dd">$1</span>',
    );
}

/** Check if a string looks like valid JSON */
const isJsonLike = (text: string): boolean => {
  const t = text.trim();
  if ((!t.startsWith("{") && !t.startsWith("[")) || (!t.endsWith("}") && !t.endsWith("]"))) return false;
  try { JSON.parse(t); return true; } catch { return false; }
};

type LlmOutputBubbleProps = {
  item: { 
    node: string; 
    model?: string | null; 
    reasoning_effort?: string | null;
    thinking_level?: string | null;
    input?: string | null;
    output: string 
  };
  index: number;
};

function LlmOutputBubble({ item, index }: LlmOutputBubbleProps) {
  const effortLabel = item.reasoning_effort ? ` [${item.reasoning_effort}]` : "";
  const thinkingLabel = item.thinking_level ? ` [T:${item.thinking_level}]` : "";
  const modelLabel = item.model ? ` · ${item.model}${effortLabel}${thinkingLabel}` : "";
  const outputText = item.output || "(empty output)";
  const jsonFormatted = isJsonLike(outputText);
  const inputText = (item.input || "").trim();
  const inputJsonFormatted = inputText ? isJsonLike(inputText) : false;

  return (
    <div className="flex justify-start">
      <div className="w-full items-start">
        <div className="mb-0.5 flex items-center gap-1.5">
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-600 dark:text-amber-400">
            LLM #{index + 1}
          </span>
          <span className="text-[10px] text-text-secondary truncate">
            {item.node}{modelLabel}
          </span>
        </div>
        <div className="mb-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-300">Output</div>
        <pre
          className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-amber-500/20 bg-amber-50/50 p-2.5 text-[11px] leading-5 text-text-primary dark:bg-amber-950/20"
          {...(jsonFormatted
            ? { dangerouslySetInnerHTML: { __html: highlightJson(outputText) } }
            : { children: outputText }
          )}
        />
        {inputText && (
          <>
            <div className="mt-1.5 mb-0.5 text-[10px] font-semibold text-sky-700 dark:text-sky-300">Input</div>
            <pre
              className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-sky-500/20 bg-sky-50/50 p-2.5 text-[11px] leading-5 text-text-primary dark:bg-sky-950/20"
              {...(inputJsonFormatted
                ? { dangerouslySetInnerHTML: { __html: highlightJson(inputText) } }
                : { children: inputText }
              )}
            />
          </>
        )}
      </div>
    </div>
  );
}

type PermissionRequestCardProps = {
  onDeny: () => void;
  onConfirm: () => void;
};

function PermissionRequestCard({ onDeny, onConfirm }: PermissionRequestCardProps) {
  return (
    <div className="space-y-4 rounded-xl border border-border-subtle bg-subtle/50 p-4">
      <div className="flex gap-3 text-text-primary">
        <Calendar className="size-4 mt-0.5" />
        <div className="flex-1 space-y-1">
          <p className="text-sm font-semibold">일정 정보를 읽어올까요?</p>
          <p className="text-sm text-text-secondary leading-relaxed">맞춤형 제안을 위해 캘린더 정보를 읽어올까요? 거부하시겠습니까?</p>
        </div>
      </div>
      <div className="flex gap-2 justify-end">
        <button
          onClick={onDeny}
          className="px-4 py-2 rounded-lg text-sm font-medium text-text-secondary hover:bg-subtle transition-colors"
        >
          거부
        </button>
        <button
          onClick={onConfirm}
          className="px-4 py-2 rounded-lg text-sm font-medium bg-token-primary text-white hover:opacity-90 transition-colors"
        >
          허용
        </button>
      </div>
    </div>
  );
}

const buildSnapshot = (assistant: ReturnType<typeof useAiAssistant>) => ({
  text: assistant.text,
  model: assistant.model,
  attachments: assistant.attachments,
  conversation: assistant.conversation,
  loading: assistant.loading,
  error: assistant.error,
  addPreview: assistant.addPreview,
  deletePreview: assistant.deletePreview,
  selectedAddItems: assistant.selectedAddItems,
  selectedDeleteGroups: assistant.selectedDeleteGroups,
  debug: assistant.debug,
  progressLabels: assistant.progressLabels,
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
  }, [assistant.open, assistant.model, assistant.attachments, assistant.conversation, assistant.loading, assistant.error, assistant.addPreview, assistant.deletePreview, assistant.selectedAddItems, assistant.selectedDeleteGroups, assistant.debug, assistant.progressLabels]);

  const view = snapshot;
  const conversation = safeArray(view.conversation);
  const addItems = safeArray(view.addPreview?.items);
  const deleteGroups = safeArray(view.deletePreview?.groups);
  const selectedAddCount = addItems.filter((_, index) => view.selectedAddItems[index]).length;
  const selectedDeleteCount = deleteGroups.filter((group) => view.selectedDeleteGroups[group.group_key]).length;
  const selectedPreviewCount = selectedAddCount + selectedDeleteCount;
  const debugState = view.debug;
  const debugLlmOutputs = safeArray(debugState?.llmOutputs);
  const debugTimeline = safeArray(debugState?.timeline);
  const debugTotalMs = debugState?.totalMs ?? null;
  const showDebugOutputs = Boolean(debugState?.enabled) && debugLlmOutputs.length > 0;
  const showDebugTimeline = Boolean(debugState?.enabled) && debugTimeline.length > 0;

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [imagePreview, setImagePreview] = useState<{ src: string; alt: string } | null>(null);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const dragStartRef = useRef({ x: 0, y: 0 });
  const dragOriginRef = useRef({ x: 0, y: 0 });
  const scrollRef = useRef<HTMLDivElement>(null);
  const isComposingRef = useRef(false);

  const hasPreview = addItems.length > 0 || deleteGroups.length > 0;
  const showConversation = conversation.length > 0 || hasPreview || (Array.isArray(view.progressLabels) && view.progressLabels.length > 0) || view.error || showDebugOutputs || showDebugTimeline;

  useEffect(() => {
    if (showConversation) {
      scrollRef.current?.scrollTo({
        top: scrollRef.current.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [conversation, hasPreview, view.progressLabels, view.error, showConversation, showDebugOutputs, debugLlmOutputs.length, showDebugTimeline]);

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
                  {conversation.map((msg, index) => (
                    <ConversationMessageRow
                      key={`${msg.role}-${index}`}
                      message={msg as ConversationUiMessage}
                      index={index}
                      onOpenImage={(src, alt) => setImagePreview({ src, alt })}
                    />
                  ))}

                  {assistant.permissionRequired && (
                    <PermissionRequestCard
                      onDeny={assistant.denyPermission}
                      onConfirm={assistant.confirmPermission}
                    />
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
                          <button onClick={assistant.apply} className={`px-5 py-2 rounded-full text-sm font-semibold transition-opacity bg-token-primary text-white ${selectedPreviewCount === 0 ? "opacity-30 pointer-events-none" : "hover:opacity-90"}`}>
                            {`${selectedPreviewCount}건 적용`}
                          </button>
                        </div>
                      )}
                    </div>
                  )}

                  {Array.isArray(view.progressLabels) && view.progressLabels.length > 0 && (
                    <div className="py-1">
                      <div className="space-y-1 text-sm">
                        {view.progressLabels.map((line, idx) => {
                          const isInProgress = line.trim().endsWith("중");
                          return (
                            isInProgress ? (
                              <div key={`progress-${idx}`} className="text-mask-wrap">
                                <span className="text-mask-base">{line}</span>
                                <span aria-hidden className="text-sweep-overlay">{line}</span>
                              </div>
                            ) : (
                              <div key={`progress-${idx}`} className="text-text-secondary">
                                {line}
                              </div>
                            )
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {showDebugTimeline && (
                    <div className="rounded-lg border border-sky-500/20 bg-sky-50/50 dark:bg-sky-950/20 p-2.5">
                      <div className="flex items-center justify-between mb-1.5">
                        <span className="inline-flex items-center gap-1 rounded-full bg-sky-500/10 px-2 py-0.5 text-[10px] font-semibold text-sky-600 dark:text-sky-400">
                          Pipeline
                        </span>
                        {debugTotalMs !== null && (
                          <span className="text-[10px] font-medium text-text-secondary">
                            total {debugTotalMs >= 1000 ? `${(debugTotalMs / 1000).toFixed(1)}s` : `${debugTotalMs}ms`}
                          </span>
                        )}
                      </div>
                      <div className="space-y-0.5">
                        {debugTimeline.map((entry, idx) => {
                          const icon = entry.status === "done" ? "OK" : entry.status === "running" ? ".." : "!!";
                          const statusColor =
                            entry.status === "done"
                              ? "text-green-600 dark:text-green-400"
                              : entry.status === "failed"
                              ? "text-red-500 dark:text-red-400"
                              : "text-amber-500 dark:text-amber-400";
                          const dur = entry.durationMs;
                          const durLabel = dur !== null
                            ? dur >= 1000
                              ? `${(dur / 1000).toFixed(1)}s`
                              : `${dur}ms`
                            : null;
                          return (
                            <div key={`tl-${idx}`} className="flex items-center gap-1.5 text-[11px] leading-5 font-mono">
                              <span className={statusColor}>{icon}</span>
                              <span className="text-text-primary">{entry.node}</span>
                              {durLabel && (
                                <span className="text-text-secondary ml-auto tabular-nums">{durLabel}</span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {showDebugOutputs && debugLlmOutputs.map((item, idx) => (
                    <LlmOutputBubble key={`llm-out-${idx}`} item={item} index={idx} />
                  ))}
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
                  placeholder={PLACEHOLDER_TEXT}
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
        .text-mask-wrap {
          position: relative;
          display: block;
          width: fit-content;
        }
        .text-mask-base {
          color: oklch(var(--text-disabled));
        }
        .text-sweep-overlay {
          position: absolute;
          inset: 0;
          pointer-events: none;
          color: oklch(97% 0.01 250);
          -webkit-mask-image: radial-gradient(
            circle at center,
            rgba(0, 0, 0, 1) 0%,
            rgba(0, 0, 0, 0.98) 20%,
            rgba(0, 0, 0, 0) 42%
          );
          mask-image: radial-gradient(
            circle at center,
            rgba(0, 0, 0, 1) 0%,
            rgba(0, 0, 0, 0.98) 20%,
            rgba(0, 0, 0, 0) 42%
          );
          -webkit-mask-size: 132% 136%;
          mask-size: 132% 136%;
          -webkit-mask-repeat: no-repeat;
          mask-repeat: no-repeat;
          -webkit-mask-position-y: 50%;
          mask-position-y: 50%;
          animation: textMaskSweep 1.4s cubic-bezier(0.45, 0, 0.55, 1) infinite;
        }
        @keyframes textMaskSweep {
          0% {
            -webkit-mask-position: 300% 50%;
            mask-position: 300% 50%;
          }
          100% {
            -webkit-mask-position: -300% 50%;
            mask-position: -300% 50%;
          }
        }
        @media (prefers-reduced-motion: reduce) {
          .text-sweep-overlay {
            animation: none;
          }
        }
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
