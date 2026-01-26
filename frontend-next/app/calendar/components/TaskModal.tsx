"use client";

import { useEffect, useState } from "react";
import { X } from "lucide-react";
import { createTask, updateTask } from "../lib/api";
import type { GoogleTask } from "../lib/types";

type TaskModalProps = {
  open: boolean;
  onClose: () => void;
  onSubmit: (task: GoogleTask) => void;
  initialTask?: GoogleTask | null;
};

export default function TaskModal({ open, onClose, onSubmit, initialTask }: TaskModalProps) {
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [due, setDue] = useState("");
  const [loading, setLoading] = useState(false);

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

  const handleSubmit = async () => {
    if (!title.trim()) {
      alert("제목을 입력해주세요.");
      return;
    }

    setLoading(true);
    try {
      let result: GoogleTask;
      if (initialTask) {
        result = await updateTask(initialTask.id, {
          title: title.trim(),
          notes: notes.trim() || null,
          due: due || null,
        });
      } else {
        result = await createTask({
          title: title.trim(),
          notes: notes.trim() || null,
          due: due || null,
        });
      }
      onSubmit(result);
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
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="relative w-full max-w-md bg-bg-surface rounded-2xl shadow-2xl p-6 m-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-xl font-semibold text-text-primary">
            {initialTask ? "할 일 수정" : "할 일 추가"}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-bg-subtle transition-colors text-text-secondary"
            aria-label="닫기"
          >
            <X className="size-5" />
          </button>
        </div>

        <div className="flex flex-col gap-4">
          <div>
            <label htmlFor="task-title" className="block text-sm font-medium text-text-primary mb-1.5">
              제목 *
            </label>
            <input
              id="task-title"
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-border-subtle bg-bg-canvas text-text-primary placeholder:text-text-tertiary focus:outline-none focus:ring-2 focus:ring-primary"
              placeholder="할 일 제목"
              autoFocus
            />
          </div>

          <div>
            <label htmlFor="task-notes" className="block text-sm font-medium text-text-primary mb-1.5">
              설명
            </label>
            <textarea
              id="task-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border border-border-subtle bg-bg-canvas text-text-primary placeholder:text-text-tertiary focus:outline-none focus:ring-2 focus:ring-primary resize-none"
              placeholder="할 일 설명"
              rows={4}
            />
          </div>

          <div>
            <label htmlFor="task-due" className="block text-sm font-medium text-text-primary mb-1.5">
              마감일
            </label>
            <input
              id="task-due"
              type="date"
              value={due ? due.split("T")[0] : ""}
              onChange={(e) => {
                if (e.target.value) {
                  setDue(`${e.target.value}T00:00:00Z`);
                } else {
                  setDue("");
                }
              }}
              className="w-full px-3 py-2 rounded-lg border border-border-subtle bg-bg-canvas text-text-primary focus:outline-none focus:ring-2 focus:ring-primary"
            />
          </div>

          <div className="flex gap-2 mt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 px-4 py-2.5 rounded-lg border border-border-subtle bg-bg-canvas text-text-primary font-medium hover:bg-bg-subtle transition-colors"
              disabled={loading}
            >
              취소
            </button>
            <button
              type="button"
              onClick={handleSubmit}
              className="flex-1 px-4 py-2.5 rounded-lg bg-primary text-white font-medium hover:opacity-90 transition-opacity disabled:opacity-50"
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
