from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .llm_provider import run_structured_completion, get_agent_llm_settings
from .schemas import PlanStep, PlannerOutput, RouterPlannerOutput, StepArgs

INTENT_ROUTER_MODEL = os.getenv("AGENT_INTENT_ROUTER_MODEL", "gpt-5-mini").strip()
_SETTINGS = get_agent_llm_settings("INTENT_ROUTER")
_CLARIFY_CONFIDENCE_THRESHOLD = 0.7
print(f"[INTENT_ROUTER] Loaded model: {INTENT_ROUTER_MODEL}, provider: {os.getenv('AGENT_LLM_PROVIDER', 'auto')}", flush=True)

INTENT_ROUTER_SYSTEM_PROMPT_TEMPLATE = """Intent router for a calendar + tasks agent.
Return JSON only. No markdown.
Current: {now_iso}. Timezone: Asia/Seoul.
Choose the best intent even when details are missing.
Use meta.clarify only when the user's goal itself is unclear.
"""

INTENT_ROUTER_DEVELOPER_PROMPT = """Input: user_text, now_iso, context.

Allowed intents:
- calendar.create_event — ONE step even for multiple events
- calendar.update_event — ONE step even for multiple updates
- calendar.cancel_event
- meta.summarize
- task.create_task
- task.update_task
- task.cancel_task
- meta.clarify

Output: {{plan: [{{step_id, intent, hint?, extract_hint?, query_ranges?, depends_on, on_fail}}], confidence}}
step_id: use short IDs — "s1", "s2", "s3", etc.
hint / extract_hint: brief description of which part of user_text this step handles.
Use "hint" as the primary field; "extract_hint" is allowed for compatibility.
query_ranges: required for update_event, cancel_event, task.cancel_task, meta.summarize.
Optional for create_event when the user references existing events (e.g. "after my 3pm meeting", "between my classes", "move my dentist appointment and add a follow-up").
Array of {{start_date: "YYYY-MM-DD", end_date: "YYYY-MM-DD"}} covering the time period the user refers to.

Rules:
1) One step per intent type. Do NOT duplicate same intent for multiple items.
2) Use depends_on only when a step needs another step's result.
3) meta.clarify only when user's goal is truly unclear.
4) If it is unclear whether the user means a task intent or a calendar event intent, route to meta.clarify.
5) Missing details (time, location) do NOT warrant meta.clarify.
5.1) Task completion requests (e.g., "완료", "done", "check off") must route to task.update_task with status change.
6) confidence: clear>=0.85, likely=0.65~0.84, partial=0.40~0.64, unclear<0.40.
7) Always set hint when user_text has multiple requests, so each step knows which items to handle.
8) For meta.summarize, always set BOTH hint and query_ranges.
9) For task.cancel_task, always set query_ranges.

Examples:

User: "내일 오후 3시에 팀 미팅 잡아줘"
{"plan":[{"step_id":"s1","intent":"calendar.create_event","hint":"팀 미팅","depends_on":[],"on_fail":"stop"}],"confidence":0.92}

User: "내일 마지막 회의 끝나고 저녁 약속 잡아줘"
{"plan":[{"step_id":"s1","intent":"calendar.create_event","hint":"저녁 약속","query_ranges":[{"start_date":"2026-02-12","end_date":"2026-02-12"}],"depends_on":[],"on_fail":"stop"}],"confidence":0.88}

User: "이번 주 일정 알려줘"
{"plan":[{"step_id":"s1","intent":"meta.summarize","hint":"이번 주 캘린더와 할일 요약","query_ranges":[{"start_date":"2026-02-09","end_date":"2026-02-15"}],"depends_on":[],"on_fail":"stop"}],"confidence":0.9}

User: "이번 주 할일 요약해줘"
{"plan":[{"step_id":"s1","intent":"meta.summarize","hint":"이번 주 캘린더와 할일 요약","query_ranges":[{"start_date":"2026-02-09","end_date":"2026-02-15"}],"depends_on":[],"on_fail":"stop"}],"confidence":0.9}

User: "매주 화목 영어수업 잡고, 보고서 제출 할일 추가해"
{"plan":[{"step_id":"s1","intent":"calendar.create_event","hint":"매주 화목 영어수업","depends_on":[],"on_fail":"stop"},{"step_id":"s2","intent":"task.create_task","hint":"보고서 제출","depends_on":[],"on_fail":"stop"}],"confidence":0.9}

User: "내일 팀미팅 4시로 옮기고 토요일 일정 전부 삭제해"
{"plan":[{"step_id":"s1","intent":"calendar.update_event","hint":"팀미팅 4시로 변경","query_ranges":[{"start_date":"2026-02-12","end_date":"2026-02-12"}],"depends_on":[],"on_fail":"stop"},{"step_id":"s2","intent":"calendar.cancel_event","hint":"토요일 일정 전부 삭제","query_ranges":[{"start_date":"2026-02-14","end_date":"2026-02-14"}],"depends_on":[],"on_fail":"stop"}],"confidence":0.88}

User: "내일 운동 추가해줘"
{"plan":[{"step_id":"s1","intent":"meta.clarify","hint":"운동을 일정(event)으로 추가할지 할 일(task)로 추가할지 확인 필요","depends_on":[],"on_fail":"stop"}],"confidence":0.36}

User: "내일 약먹기 추가해줘"
{"plan":[{"step_id":"s1","intent":"meta.clarify","hint":"약먹기를 일정(event)으로 추가할지 할 일(task)로 추가할지 확인 필요","depends_on":[],"on_fail":"stop"}],"confidence":0.35}

User: "우유 사기랑 빨래하기 할 일 지워줘"
{"plan":[{"step_id":"s1","intent":"task.cancel_task","hint":"우유 사기, 빨래하기 삭제","depends_on":[],"on_fail":"stop"}],"confidence":0.9}
"""

def _default_clarify_plan(reason: str = "Unable to interpret the user request.") -> PlannerOutput:
  return PlannerOutput(
      plan=[
          PlanStep(
              step_id="s1",
              intent="meta.clarify",
              args=StepArgs(reason=reason),
              depends_on=[],
              on_fail="stop",
          )
      ],
      confidence=0.0,
  )


def _normalize_plan(raw: RouterPlannerOutput) -> PlannerOutput:
  """Convert lightweight RouterPlannerOutput into full PlannerOutput
  with empty StepArgs. Normalizes step IDs and dependency references."""
  if not raw.plan:
    return _default_clarify_plan("Planning output was empty.")

  raw_steps = raw.plan[:8]
  has_non_clarify = any(step.intent != "meta.clarify" for step in raw_steps)
  if has_non_clarify:
    raw_steps = [step for step in raw_steps if step.intent != "meta.clarify"]
  if not raw_steps:
    return _default_clarify_plan("Planning output was empty.")

  id_map: Dict[str, str] = {}
  for index, step in enumerate(raw_steps):
    new_step_id = f"s{index + 1}"
    old_step_id = str(step.step_id or "").strip() or new_step_id
    id_map[old_step_id] = new_step_id
  index_by_id = {f"s{i + 1}": i for i in range(len(raw_steps))}

  normalized_steps: List[PlanStep] = []
  required_query_range_intents = {
      "calendar.update_event",
      "calendar.cancel_event",
      "task.cancel_task",
      "meta.summarize",
  }
  for index, step in enumerate(raw_steps):
    new_step_id = f"s{index + 1}"

    raw_depends_on = step.depends_on if isinstance(step.depends_on, list) else []
    mapped_depends_on: List[str] = []
    for dep in raw_depends_on:
      if not isinstance(dep, str):
        continue
      mapped = id_map.get(dep)
      if not mapped or mapped == new_step_id or mapped in mapped_depends_on:
        continue
      if index_by_id.get(mapped, -1) >= index:
        continue
      mapped_depends_on.append(mapped)

    # Preserve query_ranges from router step
    qr = None
    if step.query_ranges:
      qr = [{"start_date": r.start_date, "end_date": r.end_date}
            for r in step.query_ranges]

    hint = (step.hint or step.extract_hint or "").strip() or None

    normalized_steps.append(
        PlanStep(
            step_id=new_step_id,
            intent=step.intent,
            extract_hint=hint,
            args=StepArgs(),
            query_ranges=qr,
            depends_on=mapped_depends_on,
            on_fail=step.on_fail if step.on_fail in ("stop", "continue") else "stop",
        ))

  for step in normalized_steps:
    if step.intent not in required_query_range_intents:
      continue
    if isinstance(step.query_ranges, list) and len(step.query_ranges) > 0:
      continue
    return _default_clarify_plan(
        f"{step.intent} requires query_ranges to identify the target period.")

  confidence = raw.confidence
  if confidence < 0.0:
    confidence = 0.0
  if confidence > 1.0:
    confidence = 1.0

  return PlannerOutput(plan=normalized_steps, confidence=confidence)


async def build_plan_from_text_with_debug(
    input_as_text: str,
    now_iso: str,
    timezone: str,
    preferences: Optional[Dict[str, Any]] = None
) -> Tuple[PlannerOutput, Dict[str, Any]]:
  text = (input_as_text or "").strip()
  if not text:
    default = _default_clarify_plan("input_as_text is empty.")
    return default, {
        "raw_output": "",
        "model": INTENT_ROUTER_MODEL,
        "normalized_plan": default.model_dump(exclude_none=True),
        "payload": {
            "user_text": "",
            "now_iso": now_iso,
            "context": {
                "timezone": timezone,
                "preferences": preferences or {},
            },
        },
    }

  payload = {
      "user_text": text,
      "now_iso": now_iso,
      "context": {
          "timezone": timezone,
          "preferences": preferences or {},
      },
  }

  system_prompt = INTENT_ROUTER_SYSTEM_PROMPT_TEMPLATE.format(now_iso=now_iso)
  parsed, raw_text, llm_meta = await run_structured_completion(
      model=INTENT_ROUTER_MODEL,
      system_prompt=system_prompt,
      developer_prompt=INTENT_ROUTER_DEVELOPER_PROMPT,
      user_payload=payload,
      response_model=RouterPlannerOutput,
      reasoning_effort=_SETTINGS["reasoning_effort"],
      gemini_thinking_level=_SETTINGS["gemini_thinking_level"],
      verbosity="low",
      max_completion_tokens=10000,
  )
  if parsed is None:
    reason = "Structured model response was empty."
    if llm_meta.get("llm_available") is False:
      unavailable_reason = str(llm_meta.get("unavailable_reason") or "").strip()
      if unavailable_reason == "google_genai_not_installed":
        reason = "Gemini SDK is not installed. Install google-genai."
      elif unavailable_reason == "gemini_api_key_missing":
        reason = "GEMINI_API_KEY is missing."
      else:
        reason = "LLM provider is unavailable."
    elif llm_meta.get("llm_output_empty_or_error"):
      llm_error = str(llm_meta.get("llm_error") or "").strip()
      if llm_error:
        reason = f"LLM call failed: {llm_error[:220]}"
    elif isinstance(raw_text, str) and raw_text.strip():
      reason = "Structured parse failed: model returned non-JSON output."
    default = _default_clarify_plan(reason)
    return default, {
        "raw_output": raw_text,
        "model": str(llm_meta.get("model") or INTENT_ROUTER_MODEL),
        "resolved_model": llm_meta.get("resolved_model"),
        "reasoning_effort": llm_meta.get("reasoning_effort"),
        "thinking_level": llm_meta.get("thinking_level"),
        "gemini_schema_mode": llm_meta.get("gemini_schema_mode"),
        "fallback_used": llm_meta.get("fallback_used"),
        "provider": llm_meta.get("provider"),
        "llm_available": llm_meta.get("llm_available"),
        "unavailable_reason": llm_meta.get("unavailable_reason"),
        "llm_error": llm_meta.get("llm_error"),
        "llm_output_empty_or_error": llm_meta.get("llm_output_empty_or_error", True),
        "normalized_plan": default.model_dump(exclude_none=True),
        "payload": payload,
        "system_prompt": system_prompt,
        "developer_prompt": INTENT_ROUTER_DEVELOPER_PROMPT,
    }

  normalized = _normalize_plan(parsed)
  if normalized.confidence <= _CLARIFY_CONFIDENCE_THRESHOLD:
    normalized = PlannerOutput(
        plan=[
            PlanStep(
                step_id="s1",
                intent="meta.clarify",
                args=StepArgs(
                    reason=(
                        f"Low router confidence ({normalized.confidence:.2f}) "
                        f"<= {_CLARIFY_CONFIDENCE_THRESHOLD:.2f}."
                    )),
                depends_on=[],
                on_fail="stop",
            )
        ],
        confidence=normalized.confidence,
    )
  return normalized, {
      "raw_output": raw_text,
      "model": str(llm_meta.get("model") or INTENT_ROUTER_MODEL),
      "resolved_model": llm_meta.get("resolved_model"),
      "reasoning_effort": llm_meta.get("reasoning_effort"),
      "thinking_level": llm_meta.get("thinking_level"),
      "gemini_schema_mode": llm_meta.get("gemini_schema_mode"),
      "fallback_used": llm_meta.get("fallback_used"),
      "provider": llm_meta.get("provider"),
      "llm_available": llm_meta.get("llm_available", True),
      "normalized_plan": normalized.model_dump(exclude_none=True),
      "forced_meta_clarify": normalized.confidence <= _CLARIFY_CONFIDENCE_THRESHOLD,
      "clarify_confidence_threshold": _CLARIFY_CONFIDENCE_THRESHOLD,
      "payload": payload,
      "system_prompt": system_prompt,
      "developer_prompt": INTENT_ROUTER_DEVELOPER_PROMPT,
  }


async def build_plan_from_text(input_as_text: str,
                               now_iso: str,
                               timezone: str,
                               preferences: Optional[Dict[str, Any]] = None
                               ) -> PlannerOutput:
  plan, _debug = await build_plan_from_text_with_debug(
      input_as_text=input_as_text,
      now_iso=now_iso,
      timezone=timezone,
      preferences=preferences,
  )
  return plan
