"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { runAgent, type AgentRunResponse } from "@/app/calendar/lib/api";

type NodeId =
  | "input_gate"
  | "normalizer"
  | "intent_router"
  | "early_clarify"
  | "context_provider"
  | "slot_validator"
  | "question_agent"
  | "executor"
  | "response";

type NodeKind = "entry" | "llm" | "context" | "validation" | "execution" | "exit";
type Anchor = "left" | "right" | "top" | "bottom";
type EdgeLane = "main" | "clarify" | "validation";
type RuntimeState = "idle" | "active" | "done" | "error";

type AgentNode = {
  id: NodeId;
  title: string;
  subtitle: string;
  kind: NodeKind;
  x: number;
  y: number;
  prompt: string;
  structuredOutput: string;
};

type Edge = {
  id: string;
  from: NodeId;
  to: NodeId;
  fromAnchor: Anchor;
  toAnchor: Anchor;
  step: string;
  lane: EdgeLane;
  label?: string;
};

type RuntimeGraph = {
  nodeStates: Record<NodeId, RuntimeState>;
  activeEdges: Set<string>;
  pathLabel: string;
};

const POLL_MS = 4000;

const GRAPH_W = 1700;
const GRAPH_H = 620;
const NODE_W = 240;
const NODE_H = 108;

const INTENT_ROUTER_PROMPT = `System Prompt
"""
You are the Intent Router for a calendar + tasks agent.
Return ONLY valid JSON that matches the provided output schema. Do not output markdown or extra text.
Do not fabricate event IDs, emails, calendar IDs, task IDs, or any API results.
Timezone is Asia/Seoul. Current date/time is {now_iso}.
Your task is to convert the user's request into one or more plan steps with a confidence score in [0,1].
Do not choose meta.clarify just because some slots are missing.
Choose meta.clarify only when the high-level user intention is unclear.
Always fill all required fields in the schema.
"""

Developer Prompt
"""
Inputs: user_text, now_iso, context
Allowed intents:
- calendar.create_event
- calendar.update_event
- calendar.cancel_event
- calendar.move_event
- meta.summarize
- task.create_task
- task.update_task
- task.cancel_task
- meta.clarify

Output must be PlannerOutput with one or more ordered steps.
Use only intent-appropriate args. Do not include routing hint fields.
"""`;

const QUESTION_PROMPT = `You are a clarification question generator.
Write exactly one concise {target_language} question.
Return plain text only (no JSON, no markdown list).
Ask only what is needed to continue execution.
If ambiguous reference exists, ask user to choose one candidate.
If slot is missing, ask for that slot only.`;

const NODES: AgentNode[] = [
  {
    id: "input_gate",
    title: "Input Gate",
    subtitle: "Check input_as_text",
    kind: "entry",
    x: 40,
    y: 130,
    prompt: "None (rule-based)",
    structuredOutput: `if input_as_text is empty -> status=needs_clarification`,
  },
  {
    id: "normalizer",
    title: "Normalizer",
    subtitle: "Detect language/timezone/now_iso",
    kind: "entry",
    x: 320,
    y: 130,
    prompt: "None (rule-based)",
    structuredOutput: `normalized_text, language_code, timezone_name, now_iso`,
  },
  {
    id: "intent_router",
    title: "Intent Router",
    subtitle: "gpt-5-mini + structured output",
    kind: "llm",
    x: 600,
    y: 130,
    prompt: INTENT_ROUTER_PROMPT,
    structuredOutput: `PlannerOutput (one or more steps)
{
  "plan": [
    { "step_id": "s1", "intent": "...", "args": { ... }, "depends_on": [], "on_fail": "stop" },
    { "step_id": "s2", "intent": "...", "args": { ... }, "depends_on": ["s1"], "on_fail": "stop" }
  ],
  "confidence": 0.0
}`,
  },
  {
    id: "early_clarify",
    title: "Early Clarify Gate",
    subtitle: "intent=meta.clarify",
    kind: "validation",
    x: 880,
    y: 28,
    prompt: "None (orchestrator branch)",
    structuredOutput: `early clarify issue + question generation path`,
  },
  {
    id: "context_provider",
    title: "Context Provider",
    subtitle: "Load Google Calendar/Tasks context",
    kind: "context",
    x: 880,
    y: 250,
    prompt: "None (API calls)",
    structuredOutput: `events, tasks, scope`,
  },
  {
    id: "slot_validator",
    title: "Slot Extractor + Validator",
    subtitle: "Normalize and validate plan",
    kind: "validation",
    x: 1160,
    y: 250,
    prompt: "None (rule-based)",
    structuredOutput: `validated_plan + issues[]`,
  },
  {
    id: "question_agent",
    title: "Question Agent",
    subtitle: "gpt-5-nano plain-text question",
    kind: "llm",
    x: 1160,
    y: 472,
    prompt: QUESTION_PROMPT,
    structuredOutput: `one plain-text question`,
  },
  {
    id: "executor",
    title: "Executor",
    subtitle: "Run calendar/task actions",
    kind: "execution",
    x: 1440,
    y: 250,
    prompt: "None (Google API execution)",
    structuredOutput: `results[] per step`,
  },
  {
    id: "response",
    title: "Final Response",
    subtitle: "Return API payload",
    kind: "exit",
    x: 1440,
    y: 472,
    prompt: "None",
    structuredOutput: `status, confidence, plan, issues, results`,
  },
];

const EDGES: Edge[] = [
  {
    id: "e1",
    from: "input_gate",
    to: "normalizer",
    fromAnchor: "right",
    toAnchor: "left",
    step: "1",
    lane: "main",
  },
  {
    id: "e2",
    from: "normalizer",
    to: "intent_router",
    fromAnchor: "right",
    toAnchor: "left",
    step: "2",
    lane: "main",
  },
  {
    id: "e3",
    from: "intent_router",
    to: "early_clarify",
    fromAnchor: "right",
    toAnchor: "left",
    step: "3A",
    lane: "clarify",
    label: "intent=meta.clarify",
  },
  {
    id: "e4",
    from: "intent_router",
    to: "context_provider",
    fromAnchor: "right",
    toAnchor: "left",
    step: "3B",
    lane: "main",
    label: "normal intent",
  },
  {
    id: "e5",
    from: "context_provider",
    to: "slot_validator",
    fromAnchor: "right",
    toAnchor: "left",
    step: "4",
    lane: "main",
  },
  {
    id: "e6",
    from: "slot_validator",
    to: "executor",
    fromAnchor: "right",
    toAnchor: "left",
    step: "5A",
    lane: "main",
    label: "issues=none",
  },
  {
    id: "e7",
    from: "slot_validator",
    to: "question_agent",
    fromAnchor: "bottom",
    toAnchor: "top",
    step: "5B",
    lane: "validation",
    label: "issues>0",
  },
  {
    id: "e8",
    from: "question_agent",
    to: "response",
    fromAnchor: "right",
    toAnchor: "left",
    step: "6Q",
    lane: "validation",
  },
  {
    id: "e9",
    from: "executor",
    to: "response",
    fromAnchor: "bottom",
    toAnchor: "top",
    step: "6A",
    lane: "main",
  },
  {
    id: "e10",
    from: "early_clarify",
    to: "question_agent",
    fromAnchor: "bottom",
    toAnchor: "left",
    step: "4A",
    lane: "clarify",
  },
  {
    id: "e11",
    from: "slot_validator",
    to: "response",
    fromAnchor: "bottom",
    toAnchor: "top",
    step: "5D",
    lane: "main",
    label: "dry_run planned",
  },
];

function nodeKindClass(kind: NodeKind): string {
  switch (kind) {
    case "llm":
      return "border-[#0e7490] bg-[#ecfeff] text-[#0f172a]";
    case "context":
      return "border-[#1d4ed8] bg-[#eff6ff] text-[#0f172a]";
    case "validation":
      return "border-[#b45309] bg-[#fffbeb] text-[#0f172a]";
    case "execution":
      return "border-[#065f46] bg-[#ecfdf5] text-[#0f172a]";
    case "exit":
      return "border-[#334155] bg-[#f8fafc] text-[#0f172a]";
    default:
      return "border-[#475569] bg-[#f8fafc] text-[#0f172a]";
  }
}

function nodeRuntimeClass(state: RuntimeState): string {
  if (state === "active") return "ring-2 ring-[#2563eb] shadow-[0_12px_30px_-20px_rgba(37,99,235,0.95)]";
  if (state === "done") return "ring-2 ring-[#16a34a]/60";
  if (state === "error") return "ring-2 ring-[#dc2626]";
  return "";
}

function edgeStyle(
  lane: EdgeLane,
  active: boolean
): { stroke: string; badgeFill: string; badgeStroke: string; dash?: string } {
  if (lane === "clarify") {
    return {
      stroke: active ? "#b45309" : "#d97706",
      badgeFill: active ? "#fef3c7" : "#fffbeb",
      badgeStroke: "#d97706",
      dash: "8 6",
    };
  }

  if (lane === "validation") {
    return {
      stroke: active ? "#0f766e" : "#0d9488",
      badgeFill: active ? "#ccfbf1" : "#ecfeff",
      badgeStroke: "#0d9488",
      dash: "8 6",
    };
  }

  return {
    stroke: active ? "#1d4ed8" : "#64748b",
    badgeFill: active ? "#dbeafe" : "#f8fafc",
    badgeStroke: active ? "#1d4ed8" : "#94a3b8",
  };
}

function anchorPoint(node: AgentNode, anchor: Anchor): { x: number; y: number } {
  if (anchor === "left") return { x: node.x, y: node.y + NODE_H / 2 };
  if (anchor === "right") return { x: node.x + NODE_W, y: node.y + NODE_H / 2 };
  if (anchor === "top") return { x: node.x + NODE_W / 2, y: node.y };
  return { x: node.x + NODE_W / 2, y: node.y + NODE_H };
}

function buildCurve(from: { x: number; y: number }, to: { x: number; y: number }): string {
  const dx = Math.max(Math.abs(to.x - from.x) * 0.45, 60);
  const c1x = from.x + (to.x >= from.x ? dx : -dx);
  const c2x = to.x - (to.x >= from.x ? dx : -dx);
  return `M ${from.x} ${from.y} C ${c1x} ${from.y}, ${c2x} ${to.y}, ${to.x} ${to.y}`;
}

function parsePrimaryIntent(response: AgentRunResponse | null): string | null {
  const first = Array.isArray(response?.plan) ? response?.plan?.[0] : null;
  if (!first || typeof first !== "object") return null;
  const intent = (first as Record<string, unknown>).intent;
  return typeof intent === "string" ? intent : null;
}

function parseIssuesCount(response: AgentRunResponse | null): number {
  if (!Array.isArray(response?.issues)) return 0;
  return response.issues.length;
}

function parseBranch(response: AgentRunResponse | null): string | null {
  const branch = response?.trace?.branch;
  return typeof branch === "string" ? branch : null;
}

function nodeOutputsFromTrace(response: AgentRunResponse | null): Record<string, unknown> {
  const nodeOutputs = response?.trace?.node_outputs;
  if (!nodeOutputs || typeof nodeOutputs !== "object") return {};
  return nodeOutputs as Record<string, unknown>;
}

function fallbackNodeOutput(
  nodeId: NodeId,
  response: AgentRunResponse | null,
  isRunning: boolean,
  runError: string | null,
  dryRun: boolean
): unknown {
  if (nodeId === "response") {
    return {
      status: response?.status ?? (runError ? "error" : isRunning ? "running" : "idle"),
      confidence: response?.confidence ?? null,
      issues_count: parseIssuesCount(response),
      results_count: Array.isArray(response?.results) ? response.results.length : 0,
      mode: dryRun ? "dry_run" : "execute",
      error: runError,
    };
  }
  if (nodeId === "intent_router") {
    return {
      confidence: response?.confidence ?? null,
      plan: response?.plan ?? [],
    };
  }
  if (nodeId === "question_agent") {
    return {
      question: response?.question ?? null,
      issues: response?.issues ?? [],
    };
  }
  if (nodeId === "executor") {
    return {
      status: response?.status ?? null,
      results: response?.results ?? [],
    };
  }
  if (nodeId === "slot_validator") {
    return {
      issues: response?.issues ?? [],
      plan: response?.plan ?? [],
    };
  }
  return {
    status: response?.status ?? null,
  };
}

function selectedNodeOutput(
  nodeId: NodeId,
  response: AgentRunResponse | null,
  isRunning: boolean,
  runError: string | null,
  dryRun: boolean
): unknown {
  const traceOutputs = nodeOutputsFromTrace(response);
  if (Object.prototype.hasOwnProperty.call(traceOutputs, nodeId)) {
    return traceOutputs[nodeId];
  }
  return fallbackNodeOutput(nodeId, response, isRunning, runError, dryRun);
}

function makeEmptyNodeStates(): Record<NodeId, RuntimeState> {
  return {
    input_gate: "idle",
    normalizer: "idle",
    intent_router: "idle",
    early_clarify: "idle",
    context_provider: "idle",
    slot_validator: "idle",
    question_agent: "idle",
    executor: "idle",
    response: "idle",
  };
}

function computeRuntimeGraph(
  response: AgentRunResponse | null,
  isRunning: boolean,
  runError: string | null,
  dryRun: boolean
): RuntimeGraph {
  const nodeStates = makeEmptyNodeStates();
  const activeEdges = new Set<string>();

  if (isRunning) {
    nodeStates.input_gate = "active";
    nodeStates.normalizer = "active";
    nodeStates.intent_router = "active";
    return {
      nodeStates,
      activeEdges,
      pathLabel: "실행 중: 입력 정규화/의도 라우팅",
    };
  }

  if (!response) {
    if (runError) nodeStates.response = "error";
    return {
      nodeStates,
      activeEdges,
      pathLabel: runError ? "실행 오류" : "대기 중",
    };
  }

  nodeStates.input_gate = "done";
  nodeStates.normalizer = "done";
  nodeStates.intent_router = "done";
  activeEdges.add("e1");
  activeEdges.add("e2");

  const primaryIntent = parsePrimaryIntent(response);
  const earlyClarify = primaryIntent === "meta.clarify";

  if (earlyClarify) {
    nodeStates.early_clarify = "done";
    nodeStates.question_agent = "done";
    nodeStates.response = runError ? "error" : "done";
    activeEdges.add("e3");
    activeEdges.add("e10");
    activeEdges.add("e8");
    return {
      nodeStates,
      activeEdges,
      pathLabel: "조기 질문 경로: intent/meta.clarify",
    };
  }

  nodeStates.context_provider = "done";
  nodeStates.slot_validator = "done";
  activeEdges.add("e4");
  activeEdges.add("e5");

  const issuesCount = parseIssuesCount(response);
  if (issuesCount > 0 || response.status === "needs_clarification") {
    nodeStates.question_agent = "done";
    nodeStates.response = runError ? "error" : "done";
    activeEdges.add("e7");
    activeEdges.add("e8");
    return {
      nodeStates,
      activeEdges,
      pathLabel: "검증 이슈 경로: 슬롯 보강 질문",
    };
  }

  if (response.status === "planned" || dryRun) {
    nodeStates.response = runError ? "error" : "done";
    activeEdges.add("e11");
    return {
      nodeStates,
      activeEdges,
      pathLabel: "dry_run 경로: 실행 없이 계획 반환",
    };
  }

  nodeStates.executor = response.status === "failed" ? "error" : "done";
  nodeStates.response = runError || response.status === "failed" ? "error" : "done";
  activeEdges.add("e6");
  activeEdges.add("e9");

  return {
    nodeStates,
    activeEdges,
    pathLabel: response.status === "failed" ? "실행 실패 경로" : "실행 완료 경로",
  };
}

function stringifyResponse(response: AgentRunResponse | null): string {
  if (!response) return "(no response yet)";
  try {
    return JSON.stringify(response, null, 2);
  } catch {
    return "(failed to stringify response)";
  }
}

function stringifyUnknown(value: unknown): string {
  if (value === undefined) return "(no node output yet)";
  if (value === null) return "null";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "(failed to stringify)";
  }
}

function normalizeError(error: unknown): string {
  if (error instanceof Error && typeof error.message === "string") return error.message;
  return "Unknown error";
}

export default function AgentPage() {
  const [selectedId, setSelectedId] = useState<NodeId>("intent_router");
  const [inputText, setInputText] = useState("내일 오후 3시에 1시간 회의 추가해줘");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [dryRun, setDryRun] = useState(true);
  const [isRunning, setIsRunning] = useState(false);
  const [lastResponse, setLastResponse] = useState<AgentRunResponse | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null);

  const inFlightRef = useRef(false);

  const nodeMap = useMemo(() => new Map(NODES.map((node) => [node.id, node])), []);
  const selectedNode = nodeMap.get(selectedId) ?? NODES[0];

  const primaryIntent = parsePrimaryIntent(lastResponse);
  const issuesCount = parseIssuesCount(lastResponse);
  const confidence = typeof lastResponse?.confidence === "number" ? lastResponse.confidence : null;
  const traceBranch = parseBranch(lastResponse);
  const liveNodeOutput = useMemo(
    () => selectedNodeOutput(selectedId, lastResponse, isRunning, lastError, dryRun),
    [selectedId, lastResponse, isRunning, lastError, dryRun]
  );

  const runtime = useMemo(
    () => computeRuntimeGraph(lastResponse, isRunning, lastError, dryRun),
    [lastResponse, isRunning, lastError, dryRun]
  );

  const runNow = useCallback(async () => {
    const text = inputText.trim();
    if (!text || inFlightRef.current) return;

    inFlightRef.current = true;
    setIsRunning(true);
    setLastError(null);

    try {
      const response = await runAgent(text, { dry_run: dryRun });
      setLastResponse(response);
      setLastUpdatedAt(Date.now());
    } catch (error) {
      setLastError(normalizeError(error));
      setLastUpdatedAt(Date.now());
    } finally {
      inFlightRef.current = false;
      setIsRunning(false);
    }
  }, [inputText, dryRun]);

  useEffect(() => {
    if (!autoRefresh) return undefined;

    void runNow();
    const id = window.setInterval(() => {
      void runNow();
    }, POLL_MS);

    return () => {
      window.clearInterval(id);
    };
  }, [autoRefresh, runNow]);

  useEffect(() => {
    if (!dryRun && autoRefresh) {
      setAutoRefresh(false);
    }
  }, [dryRun, autoRefresh]);

  return (
    <main className="min-h-screen bg-[#f7f8f6] text-[#0f172a]">
      <div
        aria-hidden="true"
        className="pointer-events-none fixed inset-0 -z-10 bg-[radial-gradient(circle_at_15%_15%,rgba(2,132,199,0.14),transparent_42%),radial-gradient(circle_at_85%_20%,rgba(217,119,6,0.12),transparent_40%),linear-gradient(180deg,#f8fafc_0%,#f7f8f6_60%,#f1f5f9_100%)]"
      />

      <section className="mx-auto w-full max-w-[1680px] px-4 pb-8 pt-7 sm:px-6 lg:px-8">
        <div className="mb-5 flex items-center justify-between gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-[#0e7490]">Agent Graph</p>
            <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight">Live Agent Flow Inspector</h1>
            <p className="mt-2 max-w-3xl text-sm text-[#475569]">
              자동 갱신으로 `/api/agent/run` 응답을 반영해서 노드/화살표 흐름을 실시간 업데이트합니다.
            </p>
          </div>
          <Link
            href="/calendar"
            className="shrink-0 rounded-full border border-[#cbd5e1] bg-white px-4 py-2 text-sm font-medium text-[#0f172a] transition hover:-translate-y-[1px]"
          >
            캘린더로 이동
          </Link>
        </div>

        <div className="mb-5 rounded-3xl border border-[#dbe5ee] bg-white/95 p-4 shadow-[0_20px_40px_-34px_rgba(2,6,23,0.38)] sm:p-5">
          <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
            <div>
              <label htmlFor="agent-live-input" className="text-xs font-semibold uppercase tracking-[0.12em] text-[#334155]">
                Live Input
              </label>
              <textarea
                id="agent-live-input"
                value={inputText}
                onChange={(event) => setInputText(event.target.value)}
                className="mt-2 h-24 w-full rounded-2xl border border-[#cbd5e1] bg-[#f8fafc] px-3 py-2 font-mono text-sm text-[#0f172a] outline-none focus:border-[#0ea5e9]"
                placeholder="예: 내일 오후 3시에 1시간 회의 추가해줘"
              />
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => void runNow()}
                  disabled={isRunning || !inputText.trim()}
                  className="rounded-full bg-[#0284c7] px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:bg-[#94a3b8]"
                >
                  {isRunning ? "실행 중..." : "지금 실행"}
                </button>
                <label className="inline-flex items-center gap-2 rounded-full border border-[#dbe5ee] bg-[#f8fafc] px-3 py-1.5 text-xs text-[#334155]">
                  <input
                    type="checkbox"
                    checked={autoRefresh}
                    onChange={(event) => setAutoRefresh(event.target.checked)}
                    disabled={!dryRun}
                  />
                  자동 갱신 ({POLL_MS / 1000}s, dry_run 전용)
                </label>
                <label className="inline-flex items-center gap-2 rounded-full border border-[#dbe5ee] bg-[#f8fafc] px-3 py-1.5 text-xs text-[#334155]">
                  <input
                    type="checkbox"
                    checked={dryRun}
                    onChange={(event) => setDryRun(event.target.checked)}
                  />
                  dry_run (끄면 실제 실행)
                </label>
              </div>
              {!dryRun ? (
                <p className="mt-2 text-xs font-semibold text-[#b45309]">
                  실제 실행 모드입니다. 자동 갱신은 비활성화되며, `지금 실행` 버튼으로만 호출됩니다.
                </p>
              ) : null}
            </div>

            <div className="rounded-2xl border border-[#dbe5ee] bg-[#f8fafc] p-3 text-sm text-[#334155]">
              <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#0f172a]">Live Snapshot</p>
              <p className="mt-2">status: <span className="font-semibold">{lastResponse?.status ?? (lastError ? "error" : "idle")}</span></p>
              <p className="mt-1">path: <span className="font-semibold">{runtime.pathLabel}</span></p>
              <p className="mt-1">primary_intent: <span className="font-semibold">{primaryIntent ?? "-"}</span></p>
              <p className="mt-1">confidence: <span className="font-semibold">{confidence?.toFixed(2) ?? "-"}</span></p>
              <p className="mt-1">issues: <span className="font-semibold">{issuesCount}</span></p>
              <p className="mt-1">mode: <span className="font-semibold">{dryRun ? "dry_run" : "execute"}</span></p>
              <p className="mt-1">branch: <span className="font-semibold">{traceBranch ?? "-"}</span></p>
              <p className="mt-1">last_update: <span className="font-semibold">{lastUpdatedAt ? new Date(lastUpdatedAt).toLocaleTimeString() : "-"}</span></p>
              {lastError ? <p className="mt-2 rounded-lg bg-[#fef2f2] px-2 py-1 text-xs text-[#b91c1c]">{lastError}</p> : null}
            </div>
          </div>
        </div>

        <div className="grid gap-5 lg:grid-cols-[1.25fr_0.85fr]">
          <div className="rounded-3xl border border-[#dbe5ee] bg-white/90 p-3 shadow-[0_20px_40px_-34px_rgba(2,6,23,0.4)] sm:p-4">
            <div className="overflow-x-auto">
              <div className="relative h-[620px] min-w-[1700px] rounded-2xl border border-[#e2e8f0] bg-[linear-gradient(0deg,rgba(255,255,255,0.9),rgba(255,255,255,0.9)),radial-gradient(circle_at_center,rgba(148,163,184,0.15)_1px,transparent_1px)] bg-[size:100%_100%,24px_24px]">
                <svg
                  className="absolute inset-0 h-full w-full"
                  viewBox={`0 0 ${GRAPH_W} ${GRAPH_H}`}
                  fill="none"
                  aria-hidden="true"
                >
                  <defs>
                    <marker id="flowArrow" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto">
                      <path d="M0 0L10 5L0 10z" fill="#64748b" />
                    </marker>
                  </defs>

                  {EDGES.map((edge) => {
                    const fromNode = nodeMap.get(edge.from);
                    const toNode = nodeMap.get(edge.to);
                    if (!fromNode || !toNode) return null;

                    const from = anchorPoint(fromNode, edge.fromAnchor);
                    const to = anchorPoint(toNode, edge.toAnchor);
                    const curve = buildCurve(from, to);
                    const midX = (from.x + to.x) / 2;
                    const midY = (from.y + to.y) / 2;

                    const selectedRelated = selectedId === edge.from || selectedId === edge.to;
                    const liveRelated = runtime.activeEdges.has(edge.id);
                    const emphasized = selectedRelated || liveRelated;
                    const style = edgeStyle(edge.lane, emphasized);

                    return (
                      <g key={edge.id}>
                        <path
                          d={curve}
                          stroke={style.stroke}
                          strokeWidth={emphasized ? 3 : 2.2}
                          fill="none"
                          strokeDasharray={style.dash}
                          markerEnd="url(#flowArrow)"
                          opacity={emphasized ? 1 : 0.78}
                        />

                        <g transform={`translate(${midX}, ${midY})`}>
                          <rect
                            x={-18}
                            y={-14}
                            width={36}
                            height={20}
                            rx={10}
                            fill={style.badgeFill}
                            stroke={style.badgeStroke}
                            strokeWidth={1.4}
                          />
                          <text
                            x={0}
                            y={0}
                            textAnchor="middle"
                            dominantBaseline="middle"
                            className="fill-[#0f172a] text-[10px] font-bold"
                          >
                            {edge.step}
                          </text>
                        </g>

                        {edge.label ? (
                          <text
                            x={midX}
                            y={midY - 22}
                            textAnchor="middle"
                            className="fill-[#334155] text-[11px] font-medium"
                          >
                            {edge.label}
                          </text>
                        ) : null}
                      </g>
                    );
                  })}
                </svg>

                {NODES.map((node) => {
                  const selected = node.id === selectedId;
                  const runtimeState = runtime.nodeStates[node.id];
                  return (
                    <button
                      key={node.id}
                      type="button"
                      onClick={() => setSelectedId(node.id)}
                      className={`${nodeKindClass(node.kind)} ${nodeRuntimeClass(runtimeState)} absolute rounded-2xl border p-4 text-left shadow-sm transition focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0ea5e9] ${
                        selected ? "ring-2 ring-[#0ea5e9]" : "hover:-translate-y-[1px]"
                      }`}
                      style={{ left: node.x, top: node.y, width: NODE_W, minHeight: NODE_H }}
                      aria-pressed={selected}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#334155]">{node.kind}</p>
                        <span className="rounded-full border border-[#cbd5e1] bg-white px-2 py-0.5 text-[10px] font-semibold uppercase text-[#334155]">
                          {runtimeState}
                        </span>
                      </div>
                      <h3 className="mt-2 font-display text-lg font-semibold leading-tight">{node.title}</h3>
                      <p className="mt-1 text-xs text-[#334155]">{node.subtitle}</p>
                    </button>
                  );
                })}
              </div>
            </div>
          </div>

          <aside className="rounded-3xl border border-[#dbe5ee] bg-white/95 p-5 shadow-[0_20px_40px_-34px_rgba(2,6,23,0.38)]">
            <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-[#0e7490]">Selected Node</p>
            <h2 className="mt-2 font-display text-2xl font-semibold">{selectedNode.title}</h2>
            <p className="mt-2 text-sm text-[#475569]">{selectedNode.subtitle}</p>

            <div className="mt-5 space-y-4">
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-[#334155]">Prompt</h3>
                <pre className="mt-2 max-h-[220px] overflow-auto rounded-2xl border border-[#dbe5ee] bg-[#f8fafc] p-3 font-mono text-[12px] leading-relaxed text-[#0f172a]">
                  {selectedNode.prompt}
                </pre>
              </section>

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-[#334155]">Structured Output</h3>
                <pre className="mt-2 max-h-[300px] overflow-auto rounded-2xl border border-[#dbe5ee] bg-[#f8fafc] p-3 font-mono text-[12px] leading-relaxed text-[#0f172a]">
                  {selectedNode.structuredOutput}
                </pre>
              </section>

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-[#334155]">Selected Node Live Output</h3>
                <pre className="mt-2 max-h-[220px] overflow-auto rounded-2xl border border-[#dbe5ee] bg-[#f8fafc] p-3 font-mono text-[11px] leading-relaxed text-[#0f172a]">
                  {stringifyUnknown(liveNodeOutput)}
                </pre>
              </section>

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-[0.12em] text-[#334155]">Last Agent Response</h3>
                <pre className="mt-2 max-h-[240px] overflow-auto rounded-2xl border border-[#dbe5ee] bg-[#f8fafc] p-3 font-mono text-[11px] leading-relaxed text-[#0f172a]">
                  {stringifyResponse(lastResponse)}
                </pre>
              </section>
            </div>

            <div className="mt-5 rounded-2xl border border-[#dbe5ee] bg-[#f8fafc] p-3 text-xs text-[#334155]">
              <p className="font-semibold text-[#0f172a]">Flow Guide</p>
              <p className="mt-2">main: 1 → 2 → 3B → 4 → 5A → 6A</p>
              <p className="mt-1">validation: 5B → 6Q</p>
              <p className="mt-1">early clarify: 3A → 4A → 6Q</p>
              <p className="mt-1">dry_run: 5D (slot_validator → response)</p>
            </div>
          </aside>
        </div>
      </section>
    </main>
  );
}
