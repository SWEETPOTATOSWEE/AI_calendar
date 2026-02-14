"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Calendar, FileText, X } from "lucide-react";
import type { GoogleTask, TaskPayload } from "../lib/types";
import { DatePopover } from "./DatePopover";

type TaskModalProps = {
  open: boolean;
  onClose: () => void;
  onSubmit: (payload: TaskPayload, initialTask?: GoogleTask | null) => Promise<void>;
  initialTask?: GoogleTask | null;
  variant?: "modal" | "drawer";
  showCloseButton?: boolean;
};

export default function TaskModal({
  open,
  onClose,
  onSubmit,
  initialTask,
  variant = "modal",
  showCloseButton,
}: TaskModalProps) {
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [due, setDue] = useState("");
  const [loading, setLoading] = useState(false);
  const isDrawer = variant === "drawer";
  const shouldShowClose = showCloseButton ?? !isDrawer;
  const [descriptionMultiline, setDescriptionMultiline] = useState(false);
  const descriptionRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (open) {
      if (initialTask) {
        setTitle(initialTask.title || "");
        setNotes(initialTask.notes || "");
        setDue(initialTask.due || "");
      } else {
        setTitle("");
        setNotes("");
        setDue("");
      }
    }
  }, [open, initialTask]);

  const resizeDescription = () => {
    if (!descriptionRef.current) return;
    const lineHeight = parseFloat(getComputedStyle(descriptionRef.current).lineHeight || "0") || 22;
    const maxHeight = lineHeight * 12;
    descriptionRef.current.style.height = "auto";
    const nextHeight = Math.min(descriptionRef.current.scrollHeight, maxHeight);
    descriptionRef.current.style.height = `${nextHeight}px`;
    descriptionRef.current.style.overflowY =
      descriptionRef.current.scrollHeight > maxHeight ? "auto" : "hidden";
    const isLong = descriptionRef.current.scrollHeight > lineHeight * 1.6;
    const isShort = descriptionRef.current.scrollHeight < lineHeight * 1.2;
    setDescriptionMultiline((prev) => (prev ? !isShort : isLong));
  };

  useLayoutEffect(() => {
    resizeDescription();
  }, [notes]);

  const handleSubmit = async () => {
    if (!title.trim()) {
      alert("제목을 입력해주세요.");
      return;
    }

    setLoading(true);
    try {
      await onSubmit(
        {
          title: title.trim(),
          notes: notes.trim() || null,
          due: due || null,
        },
        initialTask ?? null
      );
      onClose();
    } catch (error) {
      console.error("Task submit error:", error);
      alert("할 일 저장에 실패했습니다.");
    } finally {
      setLoading(false);
    }
  };

  if (!open) return null;

  return (
    <div
      className={
        isDrawer
          ? "flex h-full flex-col"
          : "fixed inset-0 z-[999] flex items-center justify-center bg-black/40 md:px-4"
      }
    >
      <div
        className={
          isDrawer
            ? "flex h-full flex-col bg-bg-surface"
            : "w-full h-full md:h-auto md:max-w-2xl md:rounded-2xl bg-bg-surface border border-border-subtle shadow-xl flex flex-col"
        }
      >
        <div
          className={
            !shouldShowClose
              ? "hidden"
              : isDrawer
                ? "flex items-center justify-between px-3 py-3 border-b border-border-subtle"
                : "flex items-center justify-between px-6 py-4 border-b border-border-subtle shrink-0"
          }
        >
          <div />
          {shouldShowClose && (
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-bg-subtle transition-colors text-text-secondary"
              aria-label="닫기"
            >
              <X className="size-5" />
            </button>
          )}
        </div>

        <div
          className={
            isDrawer
              ? "flex-1 px-3 py-3 space-y-4 overflow-y-auto"
              : "flex-1 md:flex-none px-6 py-5 space-y-4 overflow-y-auto md:max-h-[70vh]"
          }
        >
          <div className="rounded-lg border border-border-subtle bg-bg-canvas">
            <div className="flex min-h-12 items-center px-4">
              <label htmlFor="task-title" className="sr-only">
                제목
              </label>
              <input
                id="task-title"
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                className="h-10 w-full -translate-y-[1px] appearance-none border-none bg-transparent py-0 text-[15px] leading-none font-medium text-text-primary placeholder:text-[15px] placeholder:font-normal placeholder:text-text-disabled focus:outline-none focus:ring-0"
                placeholder="제목"
                autoComplete="off"
                autoFocus
              />
            </div>
          </div>

          <div className="rounded-lg border border-border-subtle bg-bg-canvas">
            <div
              className={`flex min-h-12 gap-2 px-4 py-2 ${
                descriptionMultiline ? "items-start" : "items-center"
              }`}
            >
              <FileText className={`size-4 text-text-disabled ${descriptionMultiline ? "mt-1" : ""}`} />
              <label className="sr-only">설명</label>
              <textarea
                ref={descriptionRef}
                rows={1}
                className="w-full resize-none border-none bg-transparent text-[15px] font-medium text-text-primary placeholder:text-[15px] placeholder:font-normal placeholder:text-text-disabled focus:outline-none focus:ring-0"
                placeholder="설명"
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
              />
            </div>
          </div>

          <div className="rounded-lg border border-border-subtle bg-bg-canvas">
            <div className="flex h-12 items-center px-4 py-2">
              <span className="w-7 shrink-0 text-[14px] font-medium text-text-primary">마감</span>
              <label className="sr-only">마감 날짜</label>
              <div className="flex items-center gap-2 ml-auto">
                <DatePopover
                  label="마감 날짜"
                  icon={<Calendar className="w-4 h-4" />}
                  value={due ? due.split("T")[0] : ""}
                  onChange={(value) => {
                    if (value) {
                      setDue(`${value}T00:00:00Z`);
                    } else {
                      setDue("");
                    }
                  }}
                  placeholder="날짜 선택"
                />
              </div>
            </div>
          </div>
        </div>

        <div
          className={
            isDrawer
              ? "flex items-center justify-between px-3 py-3 border-t border-border-subtle"
              : "flex items-center justify-between px-6 py-4 border-t border-border-subtle shrink-0"
          }
        >
          <span />
          <div className="flex gap-2">
            <button
              className="px-4 py-2 rounded-lg border border-border-subtle text-[14px] font-semibold text-text-primary"
              onClick={onClose}
              type="button"
              disabled={loading}
            >
              취소
            </button>
            <button
              className="px-4 py-2 rounded-lg bg-bg-brand text-[14px] font-semibold text-white disabled:opacity-50"
              onClick={handleSubmit}
              type="button"
              disabled={loading || !title.trim()}
            >
              {loading ? "저장 중..." : initialTask ? "수정" : "추가"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
