"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchAgentDebugStatus,
  runAgentStream,
  type AgentStreamDeltaEvent,
  type AgentStreamStatusEvent,
  type AgentRunResponse,
} from "./api";
import type { CalendarEvent, EventRecurrence } from "./types";

type AiMode = "agent";
type AiModel = "nano" | "mini";

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
  items?: AddPreviewItem[];
};

type DeletePreviewGroup = {
  group_key: string;
  title: string;
  time?: string | null;
  location?: string | null;
  ids: Array<number | string>;
  count?: number;
};

type DeletePreviewResponse = {
  groups?: DeletePreviewGroup[];
};

type Attachment = {
  id: string;
  name: string;
  dataUrl: string;
};

type ConversationMessage = {
  role: "user" | "assistant";
  text: string;
  attachments?: Attachment[];
  includeInPrompt?: boolean;
  streaming?: boolean;
};

type DebugLlmItem = {
  node: string;
  model?: string | null;
  reasoning_effort?: string | null;
  thinking_level?: string | null;
  input?: string | null;
  output: string;
};

type DebugTimelineEntry = {
  node: string;
  status: "running" | "done" | "failed";
  durationMs: number | null;
};

type DebugPanelState = {
  enabled: boolean;
  llmOutputs: DebugLlmItem[];
  timeline: DebugTimelineEntry[];
  totalMs: number | null;
};

const EMPTY_DEBUG_STATE: DebugPanelState = {
  enabled: false,
  llmOutputs: [],
  timeline: [],
  totalMs: null,
};

const MAX_ATTACHMENTS = 5;
const MAX_FILE_SIZE = 2.5 * 1024 * 1024;
const MAX_CONVERSATION_MESSAGES = 24;
const MAX_CONVERSATION_CHARS = 2700;

const buildConversationText = (messages: ConversationMessage[]) =>
  messages
    .filter((msg) => msg.includeInPrompt !== false)
    .map((msg) => `${msg.role === "assistant" ? "assistant" : "user"}: ${msg.text}`)
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
  onApplied?: (response?: AgentRunResponse) => void;
  onAddApplied?: (events: CalendarEvent[]) => void;
  onDeleteApplied?: (ids: Array<number | string>) => void;
};

const asRecord = (value: unknown): Record<string, unknown> | null => {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
};

const asString = (value: unknown): string | null => {
  return typeof value === "string" && value.trim() ? value.trim() : null;
};

/**
 * Try to parse a raw LLM output string into pretty-printed JSON.
 * Handles:
 *   - Pure JSON strings
 *   - JSON wrapped in markdown code fences (```json ... ```)
 *   - Mixed text + JSON (extracts and formats JSON blocks inline)
 * Falls back to the original string if nothing looks like JSON.
 */
const normalizeOutputToJson = (raw: string): string => {
  if (!raw || !raw.trim()) return raw;

  const trimmed = raw.trim();

  // 1) Try parsing the entire string as JSON directly
  try {
    const parsed = JSON.parse(trimmed);
    return JSON.stringify(parsed, null, 2);
  } catch {
    // not pure JSON ??continue
  }

  // 2) Strip markdown code fences and try again
  const fenceRegex = /^```(?:json)?\s*\n?([\s\S]*?)\n?```$/;
  const fenceMatch = trimmed.match(fenceRegex);
  if (fenceMatch) {
    try {
      const parsed = JSON.parse(fenceMatch[1].trim());
      return JSON.stringify(parsed, null, 2);
    } catch {
      // fenced content isn't valid JSON
    }
  }

  // 3) Find and format inline JSON blocks (```json ... ```) within larger text
  const inlineFenceRegex = /```(?:json)?\s*\n?([\s\S]*?)\n?```/g;
  let result = trimmed;
  let didReplace = false;
  result = result.replace(inlineFenceRegex, (_match, content: string) => {
    try {
      const parsed = JSON.parse(content.trim());
      didReplace = true;
      return JSON.stringify(parsed, null, 2);
    } catch {
      return _match;
    }
  });
  if (didReplace) return result;

  // 4) Try to find a top-level JSON object or array in the string
  const jsonStartIdx = trimmed.search(/[{\[]/);
  if (jsonStartIdx >= 0) {
    const candidate = trimmed.slice(jsonStartIdx);
    try {
      const parsed = JSON.parse(candidate);
      const prefix = trimmed.slice(0, jsonStartIdx).trim();
      const formatted = JSON.stringify(parsed, null, 2);
      return prefix ? `${prefix}\n${formatted}` : formatted;
    } catch {
      // not valid JSON from that point
    }
  }

  // 5) Fallback: return original
  return raw;
};

const buildFailureLogs = (response: AgentRunResponse) => {
  const logs: string[] = [];
  const trace = response.trace;
  if (!trace) return logs;
  if (!trace.debug?.enabled) return logs;

  const branch = asString(trace.branch);
  if (branch) logs.push(`- **branch**: ${branch}`);

  const currentNode = asString(trace.debug?.current_node ?? null);
  if (currentNode) logs.push(`- **current_node**: ${currentNode}`);

  const nodeOutputs = asRecord(trace.node_outputs);
  const executorOutput = nodeOutputs ? asRecord(nodeOutputs.executor) : null;
  const stoppedAtStepId = asString(executorOutput?.stopped_at_step_id);
  if (stoppedAtStepId) logs.push(`- **stopped_at_step_id**: ${stoppedAtStepId}`);

  // Show step results from executor output
  const stepResults = executorOutput?.step_results;
  if (Array.isArray(stepResults) && stepResults.length > 0) {
    for (const sr of stepResults) {
      const rec = asRecord(sr);
      if (!rec) continue;
      const sid = asString(rec.step_id) ?? "?";
      const int = asString(rec.intent) ?? "?";
      const ok = !!rec.ok;
      const err = asString(rec.error);
      if (!ok && err) {
        logs.push(`- **${sid}** (${int}): \`${err}\``);
      }
    }
  }

  // Extract node output details per stage
  if (nodeOutputs) {
    // Intent router output
    const routerOut = asRecord(nodeOutputs.intent_router);
    if (routerOut) {
      const model = asString(routerOut.model);
      const effort = asString(routerOut.reasoning_effort);
      const thinking = asString(routerOut.thinking_level);
      let label = `- **intent_router model**: ${model}`;
      if (effort) label += ` [${effort}]`;
      if (thinking) label += ` [T:${thinking}]`;
      if (model) logs.push(label);
    }

    // Slot extractor output
    const slotExtractorOut = asRecord(nodeOutputs.slot_extractor);
    if (slotExtractorOut) {
      const model = asString(slotExtractorOut.model);
      const effort = asString(slotExtractorOut.reasoning_effort);
      const thinking = asString(slotExtractorOut.thinking_level);
      let label = `- **slot_extractor model**: ${model}`;
      if (effort) label += ` [${effort}]`;
      if (thinking) label += ` [T:${thinking}]`;
      if (model) logs.push(label);
    }

    // Pre-validation issues
    const preOut = asRecord(nodeOutputs.slot_validator_pre);
    if (preOut) {
      const issuesCount = typeof preOut.issues_count === "number" ? preOut.issues_count : 0;
      if (issuesCount > 0) {
        logs.push(`- **slot_validator_pre**: ${issuesCount} issue(s)`);
        const issues = Array.isArray(preOut.issues) ? preOut.issues : [];
        for (const iss of issues.slice(0, 8)) {
          const rec = asRecord(iss);
          if (!rec) continue;
          const detail = asString(rec.detail) ?? "";
          const slot = asString(rec.slot) ?? "";
          const code = asString(rec.code) ?? "";
          logs.push(`  - \`${code}\` ${slot ? `[${slot}]` : ""}: ${detail}`);
        }
      }
      const missingSlots = Array.isArray(preOut.missing_slots) ? preOut.missing_slots : [];
      if (missingSlots.length > 0 && issuesCount === 0) {
        logs.push(`- **missing_slots (pre)**: ${missingSlots.length}`);
        for (const ms of missingSlots.slice(0, 8)) {
          const rec = asRecord(ms);
          if (!rec) continue;
          logs.push(`  - ${asString(rec.step_id) ?? "?"}: ${asString(rec.detail) ?? ""}`);
        }
      }
    }

    // Context validation issues
    const ctxOut = asRecord(nodeOutputs.slot_validator);
    if (ctxOut) {
      const issuesCount = typeof ctxOut.issues_count === "number" ? ctxOut.issues_count : 0;
      if (issuesCount > 0) {
        logs.push(`- **slot_validator**: ${issuesCount} issue(s)`);
        const issues = Array.isArray(ctxOut.issues) ? ctxOut.issues : [];
        for (const iss of issues.slice(0, 8)) {
          const rec = asRecord(iss);
          if (!rec) continue;
          const detail = asString(rec.detail) ?? "";
          const slot = asString(rec.slot) ?? "";
          const code = asString(rec.code) ?? "";
          logs.push(`  - \`${code}\` ${slot ? `[${slot}]` : ""}: ${detail}`);
        }
      }
    }

    // Question agent output
    const qaOut = asRecord(nodeOutputs.question_agent);
    if (qaOut) {
      const question = asString(qaOut.question);
      if (question) logs.push(`- **question_agent**: ${question}`);
    }

    // Title clarify output
    const titleOut = asRecord(nodeOutputs.title_clarify);
    if (titleOut && titleOut.triggered) {
      const missing = Array.isArray(titleOut.missing_titles) ? titleOut.missing_titles : [];
      if (missing.length > 0) {
        logs.push(`- **title_clarify**: ${missing.length} title(s) missing`);
        for (const mt of missing.slice(0, 8)) {
          const rec = asRecord(mt);
          if (!rec) continue;
          logs.push(`  - ${asString(rec.step_id) ?? "?"}: ${asString(rec.detail) ?? ""}`);
        }
      }
    }
  }

  const timeline = Array.isArray(trace.node_timeline) ? trace.node_timeline : [];
  const failedNodes = timeline
    .map((item) => {
      const parsed = asRecord(item);
      const node = asString(parsed?.node);
      const status = asString(parsed?.status)?.toLowerCase();
      if (!node || !status) return null;
      if (status !== "failed" && status !== "error") return null;
      return `\`${node}\``;
    })
    .filter((item): item is string => Boolean(item));
  if (failedNodes.length > 0) {
    logs.push(`- **failed_nodes**: ${failedNodes.join(", ")}`);
  }

  return logs;
};

const formatAgentReply = (response: AgentRunResponse) => {
  const status = response.status || "unknown";
  if (status === "needs_clarification") {
    return response.question || "I need one more detail to continue.";
  }
  const responseText = asString(response.response_text);
  if (responseText) {
    return responseText;
  }

  const lines: string[] = [];
  if (status === "completed") lines.push("Done.");
  if (status === "planned") lines.push("Plan generated (dry run).");
  if (status === "failed") lines.push("**Execution failed.**");

  const results = Array.isArray(response.results) ? response.results : [];
  const failedResults = results.filter((item) => !item.ok);

  if (results.length > 0) {
    lines.push("");
    lines.push("**Steps:**");
    for (const item of results) {
      if (item.ok) {
        const data = item.data as Record<string, unknown> | undefined;
        const eventId = data ? asString(data.event_id) : null;
        const count = typeof data?.count === "number" ? data.count : null;
        const extra = [
          eventId ? `id=${eventId}` : null,
          count && count > 1 ? `${count} items` : null,
        ].filter(Boolean).join(", ");
        lines.push(`- ??**${item.intent}** (${item.step_id})${extra ? `: ${extra}` : ""}`);
      } else {
        lines.push(`- ??**${item.intent}** (${item.step_id}): \`${item.error || "failed"}\``);
      }
    }
  } else if (status === "completed") {
    lines.push("- No step result returned.");
  }

  // Show plan summary for failed/planned status
  const plan = Array.isArray(response.plan) ? response.plan : [];
  if (plan.length > 0 && (status === "failed" || status === "planned")) {
    lines.push("");
    lines.push("**Plan:**");
    for (const step of plan) {
      const rec = asRecord(step);
      if (!rec) continue;
      const sid = asString(rec.step_id) ?? "?";
      const intent = asString(rec.intent) ?? "?";
      const args = asRecord(rec.args);
      const items = args ? (Array.isArray(args.items) ? args.items : []) : [];
      const title = asString(args?.title);
      const argsDetails: string[] = [];
      if (title) argsDetails.push(`title="${title}"`);
      if (items.length > 0) {
        for (const it of items) {
          const itRec = asRecord(it);
          if (!itRec) continue;
          const itTitle = asString(itRec.title) ?? "";
          const itType = asString(itRec.type) ?? "";
          const itStart = asString(itRec.start) ?? asString(itRec.start_date) ?? "";
          argsDetails.push(`[${itType}] ${itTitle} ${itStart}`);
        }
      }
      const argsStr = argsDetails.length > 0 ? ` ??${argsDetails.join(", ")}` : "";
      lines.push(`- ${sid}: \`${intent}\`${argsStr}`);
    }
  }

  // Show validation issues from response
  const issues = Array.isArray(response.issues) ? response.issues : [];
  if (issues.length > 0) {
    lines.push("");
    lines.push("**Validation Issues:**");
    for (const iss of issues) {
      const rec = asRecord(iss);
      if (!rec) continue;
      const sid = asString(rec.step_id) ?? "?";
      const code = asString(rec.code) ?? "?";
      const slot = asString(rec.slot);
      const detail = asString(rec.detail) ?? "";
      lines.push(`- ${sid}: \`${code}\`${slot ? ` [${slot}]` : ""} ??${detail}`);
    }
  }

  if (failedResults.length > 0 || status === "failed") {
    const failureLogs = buildFailureLogs(response);
    if (failureLogs.length > 0) {
      lines.push("");
      lines.push("**Failure Details:**");
      lines.push(...failureLogs);
    }
  }

  // Show confidence
  if (typeof response.confidence === "number" && status !== "completed") {
    lines.push("");
    lines.push(`*confidence: ${response.confidence}*`);
  }

  return lines.join("\n").trim() || "Request processed.";
};

const buildLlmInputQueues = (
  nodeOutputsRaw: unknown,
): Record<string, string[]> => {
  const queues: Record<string, string[]> = {};
  const push = (node: string, payload: Record<string, unknown>) => {
    if (!node) return;
    const normalized = normalizeOutputToJson(JSON.stringify(payload, null, 2));
    if (!queues[node]) queues[node] = [];
    queues[node].push(normalized);
  };

  const nodeOutputs = asRecord(nodeOutputsRaw);
  if (!nodeOutputs) return queues;

  const intentRouter = asRecord(nodeOutputs.intent_router);
  const intentRouterDebug = asRecord(intentRouter?.debug);
  if (intentRouterDebug) {
    push("intent_router", {
      payload: intentRouterDebug.payload ?? null,
      system_prompt: intentRouterDebug.system_prompt ?? null,
      developer_prompt: intentRouterDebug.developer_prompt ?? null,
    });
  }

  const questionAgent = asRecord(nodeOutputs.question_agent);
  const questionDebug = asRecord(questionAgent?.debug);
  if (questionDebug) {
    push("question_agent", {
      payload: questionDebug.payload ?? null,
      system_prompt: questionDebug.system_prompt ?? null,
      developer_prompt: questionDebug.developer_prompt ?? null,
    });
  }

  const responseAgent = asRecord(nodeOutputs.response_agent);
  const responseDebug = asRecord(responseAgent?.debug);
  if (responseDebug) {
    const attempts = Array.isArray(responseDebug.attempts) ? responseDebug.attempts : [];
    if (attempts.length > 0) {
      for (const rawAttempt of attempts) {
        const attempt = asRecord(rawAttempt);
        if (!attempt) continue;
        push("response_agent", {
          payload: attempt.payload ?? null,
          system_prompt: attempt.system_prompt ?? null,
          developer_prompt: attempt.developer_prompt ?? null,
        });
      }
    } else {
      push("response_agent", {
        payload: responseDebug.payload ?? null,
        system_prompt: responseDebug.system_prompt ?? null,
        developer_prompt: responseDebug.developer_prompt ?? null,
      });
    }
  }

  const slotExtractor = asRecord(nodeOutputs.slot_extractor);
  const extractions = Array.isArray(slotExtractor?.extractions) ? slotExtractor.extractions : [];
  for (const rawExtraction of extractions) {
    const extraction = asRecord(rawExtraction);
    if (!extraction) continue;
    const node = asString(extraction.node) ?? "";
    if (!node.startsWith("slot_extractor")) continue;
    push(node, {
      payload: extraction.payload ?? null,
      system_prompt: extraction.system_prompt ?? null,
      developer_prompt: extraction.developer_prompt ?? null,
    });
  }
  return queues;
};

const parseLlmOutputs = (rawList: unknown[], nodeOutputsRaw: unknown): DebugLlmItem[] => {
  const outputs: DebugLlmItem[] = [];
  const inputQueues = buildLlmInputQueues(nodeOutputsRaw);
  for (const raw of rawList) {
    const item = asRecord(raw);
    const node = typeof item?.node === "string" ? item.node : "";
    if (!node) continue;
    const output = typeof item?.output === "string" ? item.output : "";
    const model = typeof item?.model === "string" ? item.model : null;
    const reasoning_effort = typeof item?.reasoning_effort === "string" ? item.reasoning_effort : null;
    const thinking_level = typeof item?.thinking_level === "string" ? item.thinking_level : null;
    const queue = inputQueues[node];
    const input = Array.isArray(queue) && queue.length > 0 ? queue.shift() ?? null : null;
    outputs.push({ 
      node, 
      model, 
      reasoning_effort, 
      thinking_level, 
      input,
      output: normalizeOutputToJson(output) 
    });
  }
  return outputs;
};

const buildNodeTimeline = (rawTimeline: unknown[]): { entries: DebugTimelineEntry[]; totalMs: number | null } => {
  const running = new Map<string, number>(); // node -> running timestamp ms
  const seen = new Map<string, DebugTimelineEntry>(); // node -> latest entry (deduped, keeps order)

  let firstAt: number | null = null;
  let lastAt: number | null = null;

  for (const item of rawTimeline) {
    const rec = asRecord(item);
    if (!rec) continue;
    const node = asString(rec.node);
    const status = asString(rec.status)?.toLowerCase();
    const atStr = asString(rec.at);
    if (!node || !status) continue;
    const at = atStr ? new Date(atStr).getTime() : NaN;
    if (isNaN(at)) continue;

    if (firstAt === null || at < firstAt) firstAt = at;
    if (lastAt === null || at > lastAt) lastAt = at;

    if (status === "running") {
      running.set(node, at);
      if (!seen.has(node)) {
        seen.set(node, { node, status: "running", durationMs: null });
      }
    } else if (status === "done" || status === "failed") {
      const startAt = running.get(node);
      const durationMs = startAt != null ? at - startAt : null;
      seen.set(node, {
        node,
        status: status as "done" | "failed",
        durationMs,
      });
    }
  }

  const totalMs = firstAt != null && lastAt != null ? lastAt - firstAt : null;
  return { entries: Array.from(seen.values()), totalMs };
};

const parseDebugPanelState = (response: AgentRunResponse | null): DebugPanelState => {
  const trace = response?.trace;
  const debugObj = trace?.debug;
  const enabled = Boolean(debugObj?.enabled);
  if (!enabled) return { ...EMPTY_DEBUG_STATE };
  const llmRaw = Array.isArray(trace?.llm_outputs) ? trace.llm_outputs : [];
  const nodeOutputsRaw = trace?.node_outputs;
  const timelineRaw = Array.isArray(trace?.node_timeline) ? trace.node_timeline : [];
  const { entries, totalMs } = buildNodeTimeline(timelineRaw);
  return { enabled, llmOutputs: parseLlmOutputs(llmRaw, nodeOutputsRaw), timeline: entries, totalMs };
};

type SlotProgressItem = {
  key: string;
  label: string;
  status: "running" | "done" | "failed";
  finishedAt?: number;
};

type ProgressState = {
  thinking: boolean;
  context: "idle" | "running" | "done";
  slots: SlotProgressItem[];
  executorStatus: "idle" | "running" | "done" | "failed";
};

const EMPTY_PROGRESS_STATE: ProgressState = {
  thinking: false,
  context: "idle",
  slots: [],
  executorStatus: "idle",
};

const SLOT_INTENT_LABELS: Record<string, string> = {
  "calendar.create_event": "일정 생성",
  "calendar.update_event": "일정 업데이트",
  "calendar.cancel_event": "일정 삭제",
  "task.create_task": "할 일 생성",
  "task.update_task": "할 일 업데이트",
  "task.cancel_task": "할 일 삭제",
};

const slotLabelFromIntent = (intent: string | null) => {
  if (!intent) return null;
  return SLOT_INTENT_LABELS[intent] ?? null;
};

const applyStatusToProgress = (
  previous: ProgressState,
  event: AgentStreamStatusEvent,
): ProgressState => {
  const node = asString(event.node);
  const status = asString(event.status)?.toLowerCase();
  const detail = asRecord(event.detail);
  if (!node || !status) return previous;

  const next: ProgressState = {
    thinking: previous.thinking,
    context: previous.context,
    slots: [...previous.slots],
    executorStatus: previous.executorStatus,
  };

  if (node === "context_provider") {
    next.thinking = false;
    if (status === "running") next.context = "running";
    if (status === "done") next.context = "done";
    return next;
  }

  if (node === "slot_extractor") {
    next.thinking = false;
    const intent = asString(detail?.intent);
    const stepId = asString(detail?.step_id);
    const label = slotLabelFromIntent(intent);
    if (!label) return next;
    const key = stepId || intent || `slot-${next.slots.length + 1}`;
    const index = next.slots.findIndex((item) => item.key === key);
    if (status === "running") {
      const item: SlotProgressItem = { key, label, status: "running" };
      if (index >= 0) next.slots[index] = item;
      else next.slots.push(item);
    } else if (status === "done") {
      const prevStatus = index >= 0 ? next.slots[index].status : null;
      const prevFinishedAt = index >= 0 ? next.slots[index].finishedAt : undefined;
      const item: SlotProgressItem = {
        key,
        label,
        status: prevStatus === "failed" ? "failed" : "done",
        finishedAt:
          prevStatus === "failed"
            ? prevFinishedAt
            : prevFinishedAt ?? Date.now(),
      };
      if (index >= 0) next.slots[index] = item;
      else next.slots.push(item);
    } else if (status === "failed") {
      const prevFinishedAt = index >= 0 ? next.slots[index].finishedAt : undefined;
      const item: SlotProgressItem = {
        key,
        label,
        status: "failed",
        finishedAt: prevFinishedAt ?? Date.now(),
      };
      if (index >= 0) next.slots[index] = item;
      else next.slots.push(item);
    }
    return next;
  }

  if (node === "slot_validator" && status === "done") {
    const missingStepIdsRaw = Array.isArray(detail?.missing_step_ids)
      ? detail?.missing_step_ids
      : [];
    const missingStepIds = new Set(
      missingStepIdsRaw
        .map((value) => (typeof value === "string" ? value.trim() : ""))
        .filter((value) => value.length > 0),
    );
    if (missingStepIds.size === 0) return next;
    next.slots = next.slots.map((item) =>
      missingStepIds.has(item.key)
        ? { ...item, status: "failed", finishedAt: item.finishedAt ?? Date.now() }
        : item,
    );
    return next;
  }

  if (node === "executor") {
    next.thinking = false;
    if (status === "running") next.executorStatus = "running";
    if (status === "done") next.executorStatus = "done";
    if (status === "failed") next.executorStatus = "failed";
    return next;
  }

  if (status === "running") next.thinking = true;
  if (status === "done" || status === "failed") next.thinking = false;
  return next;
};

export const useAiAssistant = (options?: AiAssistantOptions) => {
  const [open, setOpen] = useState(false);
  const mode: AiMode = "agent";
  const [text, setText] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [conversation, setConversation] = useState<ConversationMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<ProgressState>({ ...EMPTY_PROGRESS_STATE });
  const [debug, setDebug] = useState<DebugPanelState>({ ...EMPTY_DEBUG_STATE });
  const [debugModeHint, setDebugModeHint] = useState<boolean>(false);
  const [addPreview, setAddPreview] = useState<AddPreviewResponse | null>(null);
  const [deletePreview, setDeletePreview] = useState<DeletePreviewResponse | null>(null);
  const [selectedAddItems, setSelectedAddItems] = useState<Record<number, boolean>>({});
  const [selectedDeleteGroups, setSelectedDeleteGroups] = useState<Record<string, boolean>>({});
  const model: AiModel = "nano";
  const progressLabels = useMemo(() => {
    const inProgressLines: string[] = [];
    const completedLines: string[] = [];
    if (progress.thinking) {
      inProgressLines.push("생각 중");
    }
    if (progress.context === "running") {
      inProgressLines.push("캘린더를 읽는 중");
    } else if (progress.context === "done") {
      completedLines.push("캘린더 읽음");
    }
    const runningSlots = progress.slots.filter((item) => item.status === "running");
    const finishedSlots = progress.slots
      .filter((item) => item.status !== "running")
      .sort((a, b) => (a.finishedAt ?? 0) - (b.finishedAt ?? 0));

    for (const item of runningSlots) {
      inProgressLines.push(`${item.label} 중`);
    }
    for (const item of finishedSlots) {
      completedLines.push(
        item.status === "failed"
          ? `${item.label} 실패`
          : `${item.label} 완료`,
      );
    }
    return [...inProgressLines, ...completedLines];
  }, [progress]);

  const abortControllerRef = useRef<AbortController | null>(null);
  const progressClearedOnResponseDeltaRef = useRef(false);

  useEffect(() => {
    let active = true;
    fetchAgentDebugStatus()
      .then((data) => {
        if (!active) return;
        setDebugModeHint(Boolean(data?.enabled));
      })
      .catch(() => {
        if (!active) return;
      });
    return () => {
      active = false;
    };
  }, []);

  const openWithText = useCallback(
    (value: string) => {
      const trimmed = value.trim();
      if (trimmed) {
        setText(trimmed);
      }
      setOpen(true);
    },
    [],
  );

  const close = useCallback(() => {
    setOpen(false);
  }, []);

  const resetConversation = useCallback(() => {
    setConversation([]);
    setText("");
    setAttachments([]);
    setAddPreview(null);
    setDeletePreview(null);
    setSelectedAddItems({});
    setSelectedDeleteGroups({});
    setError(null);
    setProgress({ ...EMPTY_PROGRESS_STATE });
    setDebug({ ...EMPTY_DEBUG_STATE });
  }, []);

  const handleAttach = useCallback(
    async (files: FileList | null) => {
      if (!files) return;
      const fileArray = Array.from(files);
      const remaining = Math.max(0, MAX_ATTACHMENTS - attachments.length);
      const slice = fileArray.slice(0, remaining);
      const next: Attachment[] = [];

      for (const file of slice) {
        if (file.size > MAX_FILE_SIZE) {
          setError("Image is too large. Use files <= 2.5MB.");
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
    },
    [attachments.length],
  );

  const removeAttachment = useCallback(
    (id: string) => {
      setAttachments((prev) => prev.filter((item) => item.id !== id));
    },
    [],
  );

  const appendConversation = useCallback(
    (
      role: ConversationMessage["role"],
      value: string,
      options?: { includeInPrompt?: boolean; attachments?: Attachment[] },
    ) => {
      const trimmed = value.trim();
      if (!trimmed) return;
      setConversation((prev) =>
        trimConversation([
          ...prev,
          {
            role,
            text: trimmed,
            includeInPrompt: options?.includeInPrompt,
            attachments: options?.attachments,
          },
        ]),
      );
    },
    [],
  );

  const appendStreamingAssistantDelta = useCallback((delta: string) => {
    const piece = typeof delta === "string" ? delta : "";
    if (!piece) return;
    setConversation((prev) => {
      const next = [...prev];
      for (let i = next.length - 1; i >= 0; i -= 1) {
        const item = next[i];
        if (item.role === "assistant" && item.streaming) {
          next[i] = { ...item, text: `${item.text}${piece}` };
          return next;
        }
      }
      return trimConversation([
        ...next,
        {
          role: "assistant",
          text: piece,
          includeInPrompt: true,
          streaming: true,
        },
      ]);
    });
  }, []);

  const finalizeStreamingAssistant = useCallback(
    (text: string, includeInPrompt: boolean) => {
      const finalText = typeof text === "string" ? text.trim() : "";
      setConversation((prev) => {
        const next = [...prev];
        for (let i = next.length - 1; i >= 0; i -= 1) {
          const item = next[i];
          if (item.role !== "assistant" || !item.streaming) continue;
          if (!finalText) {
            if (!item.text.trim()) {
              next.splice(i, 1);
              return next;
            }
            next[i] = { ...item, streaming: false, includeInPrompt };
            return trimConversation(next);
          }
          next[i] = {
            ...item,
            text: finalText,
            includeInPrompt,
            streaming: false,
          };
          return trimConversation(next);
        }
        if (!finalText) return next;
        return trimConversation([
          ...next,
          {
            role: "assistant",
            text: finalText,
            includeInPrompt,
            streaming: false,
          },
        ]);
      });
    },
    [],
  );

  const preview = useCallback(
    async () => {
      const trimmedText = text.trim();
      const attachmentSnapshot = attachments.map((item) => ({ ...item }));
      const debugWasEnabled = debug.enabled;
      const shouldShowLoadingDebug = debugWasEnabled || debugModeHint;

      if (!trimmedText && attachmentSnapshot.length === 0) {
        setError("Please enter a message.");
        return;
      }

      const userMessage = trimmedText || `Sent ${attachmentSnapshot.length} image(s).`;
      appendConversation("user", userMessage, {
        includeInPrompt: true,
        attachments: attachmentSnapshot.length > 0 ? attachmentSnapshot : undefined,
      });

      setText("");
      setAttachments([]);
      setLoading(true);
      setProgress({ ...EMPTY_PROGRESS_STATE, thinking: true });
      setError(null);
      setAddPreview(null);
      setDeletePreview(null);
      setSelectedAddItems({});
      setSelectedDeleteGroups({});
      if (shouldShowLoadingDebug) {
        setDebug({ enabled: true, llmOutputs: [], timeline: [], totalMs: null });
      } else {
        setDebug({ ...EMPTY_DEBUG_STATE });
      }

      const controller = new AbortController();
      abortControllerRef.current = controller;
      progressClearedOnResponseDeltaRef.current = false;

      try {
        const currentConversation = [
          ...conversation,
          {
            role: "user" as const,
            text: userMessage,
            includeInPrompt: true,
          },
        ];
        const payloadText = buildConversationText(trimConversation(currentConversation));
        const response = await runAgentStream(payloadText, {
          signal: controller.signal,
          handlers: {
            onDelta: (event: AgentStreamDeltaEvent) => {
              if (event.node !== "question_agent" && event.node !== "response_agent") return;
              if (
                event.node === "response_agent" &&
                !progressClearedOnResponseDeltaRef.current
              ) {
                progressClearedOnResponseDeltaRef.current = true;
                setProgress({ ...EMPTY_PROGRESS_STATE });
              }
              appendStreamingAssistantDelta(event.delta || "");
            },
            onStatus: (event: AgentStreamStatusEvent) => {
              setProgress((prev) => applyStatusToProgress(prev, event));
            },
          },
        });
        const parsedDebug = parseDebugPanelState(response);
        setDebug(parsedDebug);

        const reply = formatAgentReply(response);
        finalizeStreamingAssistant(reply, true);

        if (response.status === "completed") {
          options?.onApplied?.(response);
        }
      } catch (err) {
        const isAbortError = err instanceof Error && err.name === "AbortError";
        if (isAbortError) {
          finalizeStreamingAssistant("Request cancelled.", true);
        } else {
          const message = err instanceof Error ? err.message : "Agent request failed.";
          setError(message);
          finalizeStreamingAssistant(message, true);
        }
      } finally {
        if (abortControllerRef.current === controller) {
          abortControllerRef.current = null;
        }
        setLoading(false);
      }
    },
    [
      text,
      attachments,
      conversation,
      appendConversation,
      appendStreamingAssistantDelta,
      finalizeStreamingAssistant,
      options,
      debug.enabled,
      debugModeHint,
    ],
  );

  const confirmPermission = useCallback(() => {
    preview();
  }, [preview]);

  const denyPermission = useCallback(() => {
    setError("Permission was denied.");
  }, []);

  const interrupt = useCallback(async () => {
    abortControllerRef.current?.abort();
    abortControllerRef.current = null;
    setLoading(false);
    setProgress({ ...EMPTY_PROGRESS_STATE });
  }, []);

  const apply = useCallback(async () => {
    setError("Manual apply is not needed. Actions are executed directly.");
  }, []);

  const updateAddPreviewItem = useCallback((index: number, patch: Partial<AddPreviewItem>) => {
    setAddPreview((prev) => {
      if (!prev || !Array.isArray(prev.items)) return prev;
      if (!prev.items[index]) return prev;
      const nextItems = [...prev.items];
      nextItems[index] = { ...nextItems[index], ...patch };
      return { ...prev, items: nextItems };
    });
  }, []);

  return {
    open,
    mode,
    text,
    startDate,
    endDate,
    attachments,
    conversation,
    debug,
    model,
    loading,
    progressLabels,
    error,
    permissionRequired: false,
    confirmPermission,
    denyPermission,
    addPreview,
    deletePreview,
    selectedAddItems,
    selectedDeleteGroups,
    setText,
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
