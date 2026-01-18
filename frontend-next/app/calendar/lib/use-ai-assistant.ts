"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  applyNlpAdd,
  classifyNlp,
  deleteEventsByIds,
  deleteGoogleEventById,
  interruptNlp,
  previewNlp,
  previewNlpDelete,
  previewNlpStream,
  resetNlpContext,
} from "./api";
import type { CalendarEvent, EventRecurrence } from "./types";

// ... (existing types)

const extractContentFromPartialJson = (json: string): string | undefined => {
  const contentStartMatch = json.match(/"content"\s*:\s*"/);
  if (!contentStartMatch) return undefined;

  const startIndex = (contentStartMatch.index || 0) + contentStartMatch[0].length;
  let contentValue = "";
  let escaped = false;

  for (let i = startIndex; i < json.length; i++) {
    const char = json[i];
    if (escaped) {
      if (char === "n") contentValue += "\n";
      else if (char === "r") contentValue += "\r";
      else if (char === "t") contentValue += "\t";
      else if (char === "\"") contentValue += "\"";
      else if (char === "\\") contentValue += "\\";
      else contentValue += char;
      escaped = false;
    } else if (char === "\\") {
      escaped = true;
    } else if (char === "\"") {
      break;
    } else {
      contentValue += char;
    }
  }
  return contentValue;
};

// ... (rest of the code)

type AiMode = "add" | "delete";
type AiModel = "nano" | "mini";
type NlpClassification = "add" | "delete" | "complex" | "garbage";

export type AddPreviewItem = {
  type: "single" | "recurring";
  title: string;
  start?: string;
  end?: string | null;
  start_date?: string;
  end_date?: string | null;
  weekdays?: number[];
  recurrence?: EventRecurrence | null;
  location?: string | null;
  description?: string | null;
  attendees?: string[] | null;
  reminders?: number[] | null;
  visibility?: "public" | "private" | "default" | null;
  transparency?: "opaque" | "transparent" | null;
  meeting_url?: string | null;
  timezone?: string | null;
  color_id?: string | null;
  all_day?: boolean;
  time?: string | null;
  duration_minutes?: number | null;
  count?: number | null;
  samples?: string[];
  occurrences?: Array<{ start?: string; end?: string | null }>;
  requires_end_confirmation?: boolean;
};

type AddPreviewResponse = {
  need_more_information?: boolean;
  content?: string;
  items?: AddPreviewItem[];
  context_used?: boolean;
  permission_required?: boolean;
};

type DeletePreviewGroup = {
  group_key: string;
  title: string;
  time?: string | null;
  location?: string | null;
  ids: Array<number | string>;
  count?: number;
  samples?: string[];
};

type DeletePreviewResponse = {
  groups?: DeletePreviewGroup[];
  permission_required?: boolean;
};

type Attachment = {
  id: string;
  name: string;
  dataUrl: string;
};

const MAX_ATTACHMENTS = 5;
const MAX_FILE_SIZE = 2.5 * 1024 * 1024;
const MAX_CONVERSATION_MESSAGES = 8;
const MAX_CONVERSATION_CHARS = 900;

type ConversationMessage = {
  role: "user" | "assistant";
  text: string;
  attachments?: Attachment[];
  includeInPrompt?: boolean;
};

const normalizeClassification = (value: unknown): NlpClassification => {
  if (value === "add" || value === "delete" || value === "complex" || value === "garbage") {
    return value;
  }
  if (typeof value === "string") {
    const lowered = value.trim().toLowerCase();
    if (lowered === "add" || lowered === "delete" || lowered === "complex" || lowered === "garbage") {
      return lowered as NlpClassification;
    }
  }
  return "garbage";
};

const pad2 = (value: number) => String(value).padStart(2, "0");
const toLocalISODate = (date: Date) =>
  `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
const addDays = (date: Date, days: number) => {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
};
const addMonths = (date: Date, months: number) => {
  const next = new Date(date);
  next.setMonth(next.getMonth() + months);
  return next;
};

const buildConversationText = (messages: ConversationMessage[]) =>
  messages
    .filter((msg) => msg.includeInPrompt !== false)
    .map((msg) => `${msg.role === "assistant" ? "assistant" : "사용자"}: ${msg.text}`)
    .join("\n");

const trimConversation = (messages: ConversationMessage[]) => {
  let next = messages.filter((msg) => msg.text.trim().length > 0);
  if (next.length > MAX_CONVERSATION_MESSAGES) {
    next = next.slice(-MAX_CONVERSATION_MESSAGES);
  }
  let text = buildConversationText(next);
  while (text.length > MAX_CONVERSATION_CHARS && next.length > 1) {
    next = next.slice(1);
    text = buildConversationText(next);
  }
  return next;
};

type AiAssistantOptions = {
  onApplied?: () => void;
  onAddApplied?: (events: CalendarEvent[]) => void;
  onDeleteApplied?: (ids: Array<number | string>) => void;
};

export const useAiAssistant = (options?: AiAssistantOptions) => {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<AiMode>("add");
  const [textByMode, setTextByMode] = useState<Record<AiMode, string>>({ add: "", delete: "" });
  const [reasoningEffort, setReasoningEffort] = useState("low");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [attachmentsByMode, setAttachmentsByMode] = useState<Record<AiMode, Attachment[]>>({
    add: [],
    delete: [],
  });
  const [conversationByMode, setConversationByMode] = useState<Record<AiMode, ConversationMessage[]>>({
    add: [],
    delete: [],
  });
  const text = textByMode[mode] ?? "";
  const attachments = attachmentsByMode[mode] ?? [];
  const conversation = conversationByMode[mode] ?? [];
  const model: AiModel = "mini";
  const [loadingByMode, setLoadingByMode] = useState<Record<AiMode, boolean>>({
    add: false,
    delete: false,
  });
  const [errorByMode, setErrorByMode] = useState<Record<AiMode, string | null>>({
    add: null,
    delete: null,
  });
  const [progressByMode, setProgressByMode] = useState<Record<AiMode, null | "thinking" | "context">>({
    add: null,
    delete: null,
  });
  const [permissionRequiredByMode, setPermissionRequiredByMode] = useState<Record<AiMode, boolean>>({
    add: false,
    delete: false,
  });
  const loading = loadingByMode[mode] ?? false;
  const error = errorByMode[mode] ?? null;
  const progress = progressByMode[mode] ?? null;
  const permissionRequired = permissionRequiredByMode[mode] ?? false;
  const [addPreview, setAddPreview] = useState<AddPreviewResponse | null>(null);
  const [deletePreview, setDeletePreview] = useState<DeletePreviewResponse | null>(null);
  const [selectedAddItems, setSelectedAddItems] = useState<Record<number, boolean>>({});
  const [selectedDeleteGroups, setSelectedDeleteGroups] = useState<Record<string, boolean>>({});
  const requestIdRef = useRef<string | null>(null);
  const pendingUserTextRef = useRef<string | null>(null);
  const pendingModeRef = useRef<AiMode | null>(null);
  const lastRequestParamsRef = useRef<{
    payloadText: string;
    attachmentSnapshot: { id: string; name: string; dataUrl: string }[];
  } | null>(null);

  const makeRequestId = () =>
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

  const ensureDefaultDeleteRange = useCallback(() => {
    const today = new Date();
    setStartDate(toLocalISODate(today));
    setEndDate(toLocalISODate(addMonths(today, 3)));
  }, []);

  const openWithText = useCallback((value: string) => {
    const trimmed = value.trim();
    if (trimmed) {
      setTextByMode((prev) => ({ ...prev, [mode]: trimmed }));
    }
    setOpen(true);
  }, [mode]);

  const close = useCallback(() => {
    setOpen(false);
  }, []);

  useEffect(() => {
    if (mode !== "delete") return;
    if (startDate && endDate) return;
    ensureDefaultDeleteRange();
  }, [mode, startDate, endDate, ensureDefaultDeleteRange]);

  const appendConversationForMode = useCallback(
    (
      targetMode: AiMode,
      role: ConversationMessage["role"],
      value: string,
      options?: { includeInPrompt?: boolean }
    ) => {
      const trimmed = value.trim();
      if (!trimmed) return;
      setConversationByMode((prev) => ({
        ...prev,
        [targetMode]: trimConversation([
          ...(prev[targetMode] ?? []),
          { role, text: trimmed, includeInPrompt: options?.includeInPrompt },
        ]),
      }));
    },
    []
  );

  const resetConversation = useCallback(() => {
    setConversationByMode((prev) => ({ ...prev, [mode]: [] }));
    setTextByMode((prev) => ({ ...prev, [mode]: "" }));
    setAttachmentsByMode((prev) => ({ ...prev, [mode]: [] }));
    if (mode === "add") {
      setAddPreview(null);
      setSelectedAddItems({});
    } else {
      setDeletePreview(null);
      setSelectedDeleteGroups({});
    }
    setErrorByMode((prev) => ({ ...prev, [mode]: null }));
    setProgressByMode((prev) => ({ ...prev, [mode]: null }));
    setLoadingByMode((prev) => ({ ...prev, [mode]: false }));
    resetNlpContext().catch(() => {});
  }, [mode]);

  const handleAttach = useCallback(async (files: FileList | null) => {
    if (!files) return;
    const fileArray = Array.from(files);
    const remaining = Math.max(0, MAX_ATTACHMENTS - attachments.length);
    const slice = fileArray.slice(0, remaining);
    const next: Attachment[] = [];

    for (const file of slice) {
      if (file.size > MAX_FILE_SIZE) {
        setErrorByMode((prev) => ({ ...prev, [mode]: "이미지가 너무 큽니다. 2.5MB 이하로 올려주세요." }));
        continue;
      }
      const dataUrl = await new Promise<string>((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
      });
      next.push({
        id: `${file.name}-${file.lastModified}`,
        name: file.name,
        dataUrl,
      });
    }

    setAttachmentsByMode((prev) => ({ ...prev, [mode]: [...(prev[mode] ?? []), ...next] }));
  }, [attachments.length, mode]);

  const removeAttachment = useCallback((id: string) => {
    setAttachmentsByMode((prev) => ({
      ...prev,
      [mode]: (prev[mode] ?? []).filter((item) => item.id !== id),
    }));
  }, [mode]);

  const preview = useCallback(async (contextConfirmed?: boolean) => {
    const trimmedText = text.trim();
    if (!trimmedText && attachments.length === 0 && !contextConfirmed) {
      setErrorByMode((prev) => ({ ...prev, [mode]: "문장을 입력해주세요." }));
      return;
    }
    const requestId = makeRequestId();
    let requestMode: AiMode = mode;
    requestIdRef.current = requestId;
    pendingModeRef.current = requestMode;
    setLoadingByMode((prev) => ({ ...prev, [requestMode]: true }));
    setProgressByMode((prev) => ({ ...prev, [requestMode]: "thinking" }));
    setErrorByMode((prev) => ({ ...prev, [requestMode]: null }));
    setPermissionRequiredByMode((prev) => ({ ...prev, [requestMode]: false }));

    if (requestMode === "add") {
      setAddPreview(null);
    } else {
      setDeletePreview(null);
    }
    const attachmentSnapshot = attachments.map((item) => ({ ...item }));
    const userMessage = trimmedText || (attachmentSnapshot.length > 0 ? "이미지 첨부" : "");
    const pendingMessage = !contextConfirmed && userMessage
      ? {
          role: "user" as const,
          text: userMessage,
          includeInPrompt: true,
          attachments: attachmentSnapshot.length > 0 ? attachmentSnapshot : undefined,
        }
      : null;
    let nextConversation = conversation;
    if (pendingMessage) {
      nextConversation = trimConversation([...conversation, pendingMessage]);
      setConversationByMode((prev) => ({ ...prev, [requestMode]: nextConversation }));
      pendingUserTextRef.current = userMessage;
    }

    if (!contextConfirmed) {
      setTextByMode((prev) => ({ ...prev, [requestMode]: "" }));
      setAttachmentsByMode((prev) => ({ ...prev, [requestMode]: [] }));
    }

    const payloadText = contextConfirmed && lastRequestParamsRef.current
      ? lastRequestParamsRef.current.payloadText
      : (buildConversationText(nextConversation) || trimmedText);

    const attachmentsToUse = contextConfirmed && lastRequestParamsRef.current
      ? lastRequestParamsRef.current.attachmentSnapshot
      : attachmentSnapshot;

    lastRequestParamsRef.current = { payloadText, attachmentSnapshot: attachmentsToUse };

    const resolveDeleteRange = () => {
      if (startDate && endDate) return { start: startDate, end: endDate };
      const today = new Date();
      const fallbackStart = toLocalISODate(today);
      const fallbackEnd = toLocalISODate(addMonths(today, 3));
      setStartDate(fallbackStart);
      setEndDate(fallbackEnd);
      return { start: fallbackStart, end: fallbackEnd };
    };
    try {
      let classification: NlpClassification = "add";
      if (!contextConfirmed) {
        const classifyResponse = await classifyNlp(payloadText, attachmentsToUse.length > 0, requestId);
        if (requestIdRef.current !== requestId) return;
        classification = normalizeClassification(classifyResponse?.type);
        const excludePendingMessage = () => {
          if (!userMessage) return;
          setConversationByMode((prev) => {
            const current = prev[requestMode] ?? [];
            const next = [...current];
            for (let i = next.length - 1; i >= 0; i -= 1) {
              const msg = next[i];
              if (msg.role === "user" && msg.text === userMessage) {
                next[i] = { ...msg, includeInPrompt: false };
                break;
              }
            }
            return { ...prev, [requestMode]: trimConversation(next) };
          });
        };

        if (classification === "complex") {
          setAddPreview(null);
          setDeletePreview(null);
          setSelectedAddItems({});
          setSelectedDeleteGroups({});
          excludePendingMessage();
          setErrorByMode((prev) => ({ ...prev, [requestMode]: "일정 추가와 일정 삭제는 동시에 할 수 없습니다." }));
          return;
        }
        if (classification === "garbage") {
          setAddPreview(null);
          setDeletePreview(null);
          setSelectedAddItems({});
          setSelectedDeleteGroups({});
          excludePendingMessage();
          setErrorByMode((prev) => ({ ...prev, [requestMode]: "일정 추가, 삭제와 관련된 것만 작성해주세요." }));
          return;
        }
        requestMode = classification === "delete" ? "delete" : "add";
        if (requestMode !== mode) {
          setMode(requestMode);
          pendingModeRef.current = requestMode;
          if (pendingMessage) {
            setConversationByMode((prev) => {
              const fromList = prev[mode] ?? [];
              const toList = prev[requestMode] ?? [];
              const nextFrom = [...fromList];
              for (let i = nextFrom.length - 1; i >= 0; i -= 1) {
                const msg = nextFrom[i];
                if (msg.role === "user" && msg.text === userMessage) {
                  nextFrom.splice(i, 1);
                  break;
                }
              }
              return {
                ...prev,
                [mode]: nextFrom,
                [requestMode]: trimConversation([...toList, pendingMessage]),
              };
            });
          }
          setLoadingByMode((prev) => ({ ...prev, [mode]: false, [requestMode]: true }));
          setProgressByMode((prev) => ({ ...prev, [mode]: null, [requestMode]: "thinking" }));
          setErrorByMode((prev) => ({ ...prev, [requestMode]: null }));
        }
      }

      if (requestMode === "add") {
        let fullJsonRaw = "";
        let currentAssistantText = "";
        let messageAppended = false;
        let permissionRequestedLocally = false;

        await previewNlpStream(
          payloadText,
          attachmentsToUse.map((item) => item.dataUrl),
          reasoningEffort,
          model,
          requestId,
          contextConfirmed,
          (event) => {
            if (requestIdRef.current !== requestId) return;

            if (event.type === "status") {
              if (event.context_used) {
                setProgressByMode((prev) => ({ ...prev, [requestMode]: "context" }));
              }
            } else if (event.type === "permission_required") {
              permissionRequestedLocally = true;
              setPermissionRequiredByMode((prev) => ({ ...prev, [requestMode]: true }));
            } else if (event.type === "reset_buffer") {
              fullJsonRaw = "";
            } else if (event.type === "chunk" || event.type === "full") {
              setProgressByMode((prev) => ({ ...prev, [requestMode]: null }));
              if (event.type === "chunk") {
                fullJsonRaw += event.delta;
              } else {
                fullJsonRaw = JSON.stringify(event.data);
              }

              const content = extractContentFromPartialJson(fullJsonRaw);
              if (content !== undefined && content.trim() !== "" && content !== currentAssistantText) {
                currentAssistantText = content;
                setConversationByMode((prev) => {
                  const current = prev[requestMode] ?? [];
                  const lastMsg = current[current.length - 1];
                  if (lastMsg && lastMsg.role === "assistant" && messageAppended) {
                    const next = [...current];
                    next[next.length - 1] = { ...lastMsg, text: content };
                    return { ...prev, [requestMode]: next };
                  } else {
                    messageAppended = true;
                    return {
                      ...prev,
                      [requestMode]: [...current, { role: "assistant", text: content, includeInPrompt: true }],
                    };
                  }
                });
              }
            } else if (event.type === "error") {
              throw new Error(event.detail);
            }
          }
        );

        if (requestIdRef.current !== requestId) return;

        let finalData: AddPreviewResponse = {};
        try {
          finalData = JSON.parse(fullJsonRaw);
        } catch (e) {
          console.warn("Complete JSON parse failed", e);
        }

        if (finalData.permission_required || permissionRequestedLocally) {
          setPermissionRequiredByMode((prev) => ({ ...prev, [requestMode]: true }));
          return;
        }
        setPermissionRequiredByMode((prev) => ({ ...prev, [requestMode]: false }));

        setAddPreview(finalData);
        const items = finalData.items || [];
        const selection: Record<number, boolean> = {};
        items.forEach((_, idx) => {
          selection[idx] = true;
        });
        setSelectedAddItems(selection);
      } else {
        const range = resolveDeleteRange();
        if (!range.start || !range.end) {
          setErrorByMode((prev) => ({ ...prev, [requestMode]: "삭제 범위를 선택해주세요." }));
          setLoadingByMode((prev) => ({ ...prev, [requestMode]: false }));
          setProgressByMode((prev) => ({ ...prev, [requestMode]: null }));
          return;
        }
        const response = await previewNlpDelete(
          payloadText,
          range.start,
          range.end,
          reasoningEffort,
          model,
          requestId,
          contextConfirmed
        );
        if (requestIdRef.current !== requestId) return;
        const data = response as DeletePreviewResponse;
        if (data.permission_required) {
          setPermissionRequiredByMode((prev) => ({ ...prev, [requestMode]: true }));
          return;
        }
        setPermissionRequiredByMode((prev) => ({ ...prev, [requestMode]: false }));

        setDeletePreview(data);
        const groups = data.groups || [];
        const selection: Record<string, boolean> = {};
        groups.forEach((group) => {
          selection[group.group_key] = true;
        });
        setSelectedDeleteGroups(selection);
        if (groups.length > 0) {
          appendConversationForMode(
            requestMode,
            "assistant",
            "삭제 후보를 찾았습니다. 확인 후 적용하세요.",
            {
              includeInPrompt: false,
            }
          );
        }
      }
    } catch (err) {
      if (requestIdRef.current !== requestId) return;
      setAddPreview(null);
      setDeletePreview(null);
      setSelectedAddItems({});
      setSelectedDeleteGroups({});
      const message = err instanceof Error ? err.message : "AI 요청에 실패했습니다.";
      setErrorByMode((prev) => ({ ...prev, [requestMode]: message }));
    } finally {
      if (requestIdRef.current === requestId) {
        requestIdRef.current = null;
        pendingUserTextRef.current = null;
        pendingModeRef.current = null;
        setLoadingByMode((prev) => ({ ...prev, [requestMode]: false }));
        setProgressByMode((prev) => ({ ...prev, [requestMode]: null }));
      }
      if (!contextConfirmed) {
        setTextByMode((prev) => ({ ...prev, [requestMode]: "" }));
      }
    }
  }, [
    mode,
    text,
    attachments,
    reasoningEffort,
    model,
    startDate,
    endDate,
    conversation,
    appendConversationForMode,
  ]);

  const confirmPermission = useCallback(() => {
    preview(true);
  }, [preview]);

  const denyPermission = useCallback(() => {
    setPermissionRequiredByMode((prev) => ({ ...prev, [mode]: false }));
    setErrorByMode((prev) => ({ ...prev, [mode]: "일정 정보를 읽지 않으면 정확한 결과를 제공하기 어렵습니다." }));
  }, [mode]);

  const updateAddPreviewItem = useCallback((index: number, patch: Partial<AddPreviewItem>) => {
    setAddPreview((prev) => {
      if (!prev || !Array.isArray(prev.items)) return prev;
      if (!prev.items[index]) return prev;
      const nextItems = [...prev.items];
      nextItems[index] = { ...nextItems[index], ...patch };
      return { ...prev, items: nextItems };
    });
  }, []);

  const interrupt = useCallback(async () => {
    const activeRequestId = requestIdRef.current;
    if (!activeRequestId) return;
    requestIdRef.current = null;
    const pendingText = pendingUserTextRef.current;
    const targetMode = pendingModeRef.current ?? mode;
    setLoadingByMode((prev) => ({ ...prev, [targetMode]: false }));
    setProgressByMode((prev) => ({ ...prev, [targetMode]: null }));
    setConversationByMode((prev) => {
      const next = [...(prev[targetMode] ?? [])];
      if (pendingText) {
        for (let i = next.length - 1; i >= 0; i -= 1) {
          const msg = next[i];
          if (msg.role === "user" && msg.includeInPrompt !== false && msg.text === pendingText) {
            next[i] = { ...msg, includeInPrompt: false };
            break;
          }
        }
      }
      return { ...prev, [targetMode]: trimConversation(next) };
    });
    pendingUserTextRef.current = null;
    pendingModeRef.current = null;
    try {
      await interruptNlp(activeRequestId);
    } catch (err) {
      const message = err instanceof Error ? err.message : "중단 요청에 실패했습니다.";
      setErrorByMode((prev) => ({ ...prev, [targetMode]: message }));
    }
  }, [mode]);

  const apply = useCallback(async () => {
    setLoadingByMode((prev) => ({ ...prev, [mode]: true }));
    setErrorByMode((prev) => ({ ...prev, [mode]: null }));
    try {
      if (mode === "add") {
        const items = (addPreview?.items || []).filter((_, idx) => selectedAddItems[idx]);
        if (!items.length) {
          setErrorByMode((prev) => ({ ...prev, [mode]: "추가할 항목을 선택해주세요." }));
          return;
        }
        const created = await applyNlpAdd(items as unknown as Record<string, unknown>[]);
        options?.onAddApplied?.(created);
      } else {
        const groups = deletePreview?.groups || [];
        const ids = groups
          .filter((group) => selectedDeleteGroups[group.group_key])
          .flatMap((group) => group.ids || [])
          .filter((id) => (typeof id === "number" ? Number.isFinite(id) : Boolean(id)));
        if (!ids.length) {
          setErrorByMode((prev) => ({ ...prev, [mode]: "삭제할 항목을 선택해주세요." }));
          return;
        }
        const numericIds = ids.filter((id) => typeof id === "number") as number[];
        const stringIds = ids.filter((id) => typeof id === "string") as string[];
        if (numericIds.length) {
          await deleteEventsByIds(numericIds);
        }
        for (const id of stringIds) {
          await deleteGoogleEventById(id);
        }
        options?.onDeleteApplied?.(ids);
      }
      setOpen(false);
      if (mode === "add") {
        setAddPreview(null);
      } else {
        setDeletePreview(null);
      }
      setConversationByMode((prev) => ({ ...prev, [mode]: [] }));
      setTextByMode((prev) => ({ ...prev, [mode]: "" }));
      setAttachmentsByMode((prev) => ({ ...prev, [mode]: [] }));
      resetNlpContext().catch(() => {});
      options?.onApplied?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : "적용에 실패했습니다.";
      setErrorByMode((prev) => ({ ...prev, [mode]: message }));
    } finally {
      setLoadingByMode((prev) => ({ ...prev, [mode]: false }));
    }
  }, [mode, addPreview, deletePreview, selectedAddItems, selectedDeleteGroups, options]);

  const attachmentList = useMemo(() => attachments, [attachments]);
  const activeAddPreview = mode === "add" ? addPreview : null;
  const activeDeletePreview = mode === "delete" ? deletePreview : null;
  const progressLabel = useMemo(() => {
    if (progress === "thinking") return "생각 중";
    if (progress === "context") return "일정을 살펴보는 중";
    return null;
  }, [progress]);

  return {
    open,
    mode,
    text,
    reasoningEffort,
    startDate,
    endDate,
    attachments: attachmentList,
    conversation,
    model,
    loading,
    progressLabel,
    error,
    permissionRequired,
    confirmPermission,
    denyPermission,
    addPreview: activeAddPreview,
    deletePreview: activeDeletePreview,
    selectedAddItems,
    selectedDeleteGroups,
    setMode,
    setText: (value: string) => setTextByMode((prev) => ({ ...prev, [mode]: value })),
    setReasoningEffort,
    setStartDate,
    setEndDate,
    setOpen,
    openWithText,
    close,
    preview,
    apply,
    resetConversation,
    interrupt,
    handleAttach,
    removeAttachment,
    setSelectedAddItems,
    setSelectedDeleteGroups,
    updateAddPreviewItem,
  };
};
