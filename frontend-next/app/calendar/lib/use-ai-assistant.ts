"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  applyNlpAdd,
  deleteEventsByIds,
  interruptNlp,
  previewNlp,
  previewNlpDelete,
  resetNlpContext,
} from "./api";
import type { CalendarEvent } from "./types";

type AiMode = "add" | "delete";
type AiModel = "nano" | "mini";

type AddPreviewItem = {
  type: "single" | "recurring";
  title: string;
  start?: string;
  end?: string | null;
  location?: string | null;
  all_day?: boolean;
  time?: string | null;
  count?: number | null;
  samples?: string[];
  occurrences?: Array<{ start?: string; end?: string | null }>;
};

type AddPreviewResponse = {
  need_more_information?: boolean;
  content?: string;
  items?: AddPreviewItem[];
  context_used?: boolean;
};

type DeletePreviewGroup = {
  group_key: string;
  title: string;
  time?: string | null;
  location?: string | null;
  ids: number[];
  count?: number;
  samples?: string[];
};

type DeletePreviewResponse = {
  groups?: DeletePreviewGroup[];
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
  includeInPrompt?: boolean;
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
  onDeleteApplied?: (ids: number[]) => void;
};

export const useAiAssistant = (options?: AiAssistantOptions) => {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<AiMode>("add");
  const [text, setText] = useState("");
  const [reasoningEffort, setReasoningEffort] = useState("low");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [conversation, setConversation] = useState<ConversationMessage[]>([]);
  const [model, setModel] = useState<AiModel>("mini");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<null | "thinking" | "context">(null);
  const [addPreview, setAddPreview] = useState<AddPreviewResponse | null>(null);
  const [deletePreview, setDeletePreview] = useState<DeletePreviewResponse | null>(null);
  const [selectedAddItems, setSelectedAddItems] = useState<Record<number, boolean>>({});
  const [selectedDeleteGroups, setSelectedDeleteGroups] = useState<Record<string, boolean>>({});
  const requestIdRef = useRef<string | null>(null);
  const pendingUserTextRef = useRef<string | null>(null);

  const makeRequestId = () =>
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;

  const ensureDefaultDeleteRange = useCallback(() => {
    const today = new Date();
    setStartDate(toLocalISODate(today));
    setEndDate(toLocalISODate(addMonths(today, 3)));
  }, []);

  const openWithText = useCallback((value: string) => {
    setText(value.trim());
    setOpen(true);
    setAddPreview(null);
    setDeletePreview(null);
    setError(null);
    setProgress(null);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setError(null);
    setAddPreview(null);
    setDeletePreview(null);
    setProgress(null);
  }, []);

  useEffect(() => {
    if (mode !== "delete") return;
    if (startDate && endDate) return;
    ensureDefaultDeleteRange();
  }, [mode, startDate, endDate, ensureDefaultDeleteRange]);

  const resetConversation = useCallback(() => {
    setConversation([]);
    setAddPreview(null);
    setDeletePreview(null);
    setSelectedAddItems({});
    setSelectedDeleteGroups({});
    setError(null);
    setProgress(null);
    setLoading(false);
    resetNlpContext().catch(() => {});
  }, []);

  const appendConversation = useCallback(
    (role: ConversationMessage["role"], value: string, options?: { includeInPrompt?: boolean }) => {
      const trimmed = value.trim();
      if (!trimmed) return;
      setConversation((prev) =>
        trimConversation([...prev, { role, text: trimmed, includeInPrompt: options?.includeInPrompt }])
      );
    },
    []
  );

  const handleAttach = useCallback(async (files: FileList | null) => {
    if (!files) return;
    const fileArray = Array.from(files);
    const remaining = Math.max(0, MAX_ATTACHMENTS - attachments.length);
    const slice = fileArray.slice(0, remaining);
    const next: Attachment[] = [];

    for (const file of slice) {
      if (file.size > MAX_FILE_SIZE) {
        setError("이미지가 너무 큽니다. 2.5MB 이하로 올려주세요.");
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

    setAttachments((prev) => [...prev, ...next]);
  }, [attachments.length]);

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const preview = useCallback(async () => {
    const trimmedText = text.trim();
    if (!trimmedText && attachments.length === 0) {
      setError("문장을 입력해주세요.");
      return;
    }
    const requestId = makeRequestId();
    requestIdRef.current = requestId;
    setLoading(true);
    setProgress("thinking");
    setError(null);
    setAddPreview(null);
    setDeletePreview(null);
    const userMessage = trimmedText || (attachments.length > 0 ? "이미지 첨부" : "");
    let nextConversation = conversation;
    if (userMessage) {
      nextConversation = trimConversation([
        ...conversation,
        { role: "user", text: userMessage, includeInPrompt: true },
      ]);
      setConversation(nextConversation);
      pendingUserTextRef.current = userMessage;
    }
    setText("");
    const payloadText = buildConversationText(nextConversation) || trimmedText;
    try {
      if (mode === "add") {
        const response = await previewNlp(
          payloadText,
          attachments.map((item) => item.dataUrl),
          reasoningEffort,
          model,
          requestId
        );
        if (requestIdRef.current !== requestId) return;
        const data = response as AddPreviewResponse;
        if (data.context_used) {
          setProgress("context");
          await new Promise((resolve) => setTimeout(resolve, 2000));
          if (requestIdRef.current !== requestId) return;
          setProgress("thinking");
          await new Promise((resolve) => setTimeout(resolve, 350));
          if (requestIdRef.current !== requestId) return;
        }
        setAddPreview(data);
        const items = data.items || [];
        const selection: Record<number, boolean> = {};
        items.forEach((_, idx) => {
          selection[idx] = true;
        });
        setSelectedAddItems(selection);
        if (data.need_more_information) {
          appendConversation("assistant", data.content || "추가로 확인할 정보가 필요합니다.", {
            includeInPrompt: true,
          });
        }
      } else {
        if (!startDate || !endDate) {
          setError("삭제 범위를 선택해주세요.");
          setLoading(false);
          setProgress(null);
          return;
        }
        const response = await previewNlpDelete(
          payloadText,
          startDate,
          endDate,
          reasoningEffort,
          model,
          requestId
        );
        if (requestIdRef.current !== requestId) return;
        const data = response as DeletePreviewResponse;
        setDeletePreview(data);
        const groups = data.groups || [];
        const selection: Record<string, boolean> = {};
        groups.forEach((group) => {
          selection[group.group_key] = true;
        });
        setSelectedDeleteGroups(selection);
        if (groups.length > 0) {
          appendConversation("assistant", "삭제 후보를 찾았습니다. 확인 후 적용하세요.", {
            includeInPrompt: false,
          });
        }
      }
    } catch (err) {
      if (requestIdRef.current !== requestId) return;
      const message = err instanceof Error ? err.message : "AI 요청에 실패했습니다.";
      setError(message);
    } finally {
      if (requestIdRef.current === requestId) {
        requestIdRef.current = null;
        pendingUserTextRef.current = null;
        setLoading(false);
        setProgress(null);
      }
      setText("");
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
    appendConversation,
  ]);

  const interrupt = useCallback(async () => {
    const activeRequestId = requestIdRef.current;
    if (!activeRequestId) return;
    requestIdRef.current = null;
    setLoading(false);
    setProgress(null);
    setError("요청이 중단되었습니다.");
    const pendingText = pendingUserTextRef.current;
    setConversation((prev) => {
      const next = [...prev];
      if (pendingText) {
        for (let i = next.length - 1; i >= 0; i -= 1) {
          const msg = next[i];
          if (msg.role === "user" && msg.includeInPrompt !== false && msg.text === pendingText) {
            next[i] = { ...msg, includeInPrompt: false };
            break;
          }
        }
      }
      next.push({ role: "assistant", text: "요청을 중단했습니다.", includeInPrompt: false });
      return trimConversation(next);
    });
    pendingUserTextRef.current = null;
    try {
      await interruptNlp(activeRequestId);
    } catch (err) {
      const message = err instanceof Error ? err.message : "중단 요청에 실패했습니다.";
      setError(message);
    }
  }, [appendConversation]);

  const apply = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      if (mode === "add") {
        const items = (addPreview?.items || []).filter((_, idx) => selectedAddItems[idx]);
        if (!items.length) {
          setError("추가할 항목을 선택해주세요.");
          return;
        }
        const created = await applyNlpAdd(items as unknown as Record<string, unknown>[]);
        options?.onAddApplied?.(created);
      } else {
        const groups = deletePreview?.groups || [];
        const ids = groups
          .filter((group) => selectedDeleteGroups[group.group_key])
          .flatMap((group) => group.ids || [])
          .filter((id) => Number.isFinite(id));
        if (!ids.length) {
          setError("삭제할 항목을 선택해주세요.");
          return;
        }
        await deleteEventsByIds(ids as number[]);
        options?.onDeleteApplied?.(ids as number[]);
      }
      setOpen(false);
      setAddPreview(null);
      setDeletePreview(null);
      setConversation([]);
      resetNlpContext().catch(() => {});
      options?.onApplied?.();
    } catch (err) {
      const message = err instanceof Error ? err.message : "적용에 실패했습니다.";
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [mode, addPreview, deletePreview, selectedAddItems, selectedDeleteGroups, options]);

  const attachmentList = useMemo(() => attachments, [attachments]);
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
    addPreview,
    deletePreview,
    selectedAddItems,
    selectedDeleteGroups,
    setMode,
    setText,
    setReasoningEffort,
    setStartDate,
    setEndDate,
    setModel,
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
  };
};
