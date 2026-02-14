from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from .llm_provider import run_text_completion, get_agent_llm_settings

RESPONSE_AGENT_MODEL = os.getenv("AGENT_RESPONSE_MODEL", "gpt-5-nano").strip() or "gpt-5-nano"
_SETTINGS = get_agent_llm_settings("RESPONSE")
print(f"[RESPONSE_AGENT] Loaded model: {RESPONSE_AGENT_MODEL}, provider: {os.getenv('AGENT_LLM_PROVIDER', 'auto')}", flush=True)
RESPONSE_AGENT_GEMINI_THINKING = "MINIMAL"

RESPONSE_AGENT_SYSTEM_PROMPT = """You are the response agent for a calendar + tasks assistant.
Write user-facing Markdown only (no JSON).
Current: {now_iso}. Timezone: {timezone}.

Rules:
- Summarize real execution changes from the provided changes list.
- For meta.summarize, use summary_requests + context to provide a concise summary.
- If both changes and summarize requests exist, cover both in one response.
- Allowed Markdown only: headings, inline style, lists, horizontal rules.
- Do NOT use links, tables, blockquotes, code blocks, or raw HTML.
- **CRITICAL: Write in natural, conversational Korean like a friendly assistant talking to a user.**
- **DO NOT include technical details like event IDs, calendar IDs, or internal identifiers.**
- **DO NOT use structured formats like "일정: X, 변경 내용: Y, 수정된 항목 수: Z" - instead write naturally.**
- Focus on what matters to the user: event titles, dates, times, and what changed.
- Keep it concise and friendly.
- Never invent events/tasks/details that are not in payload.
- If no relevant item exists, clearly say nothing matched.

Example good responses:
- "휴가 일정을 2월 18일로 변경했어요."
- "2월 14일에 3개 일정이 있어요: 회의 (오전 10시), 점심약속 (낮 12시), 운동 (오후 7시)"
- "내일 할 일 2개를 완료 처리했어요."

Example bad responses (too technical):
- "일정: 휴가\\n변경 내용: 날짜를 2026-02-18 (전일)로 수정함\\n수정된 항목 수: 1\\n이벤트 ID: caltestuser0207@gmail.com::0dsdjogt7e57furjsh20cf8krs"
"""

RESPONSE_AGENT_DEVELOPER_PROMPT = """Input fields:
- user_text, intent, now_iso, timezone, language
- changes: execution changes for calendar/tasks
- summary_requests: list of meta.summarize requests with hint/query_ranges
- context: loaded events/tasks/scope

Goal:
Return the final assistant message shown to the user.

**Writing Style:**
- Write like a friendly human assistant, NOT like a technical system.
- Use conversational Korean with natural flow.
- Omit ALL technical identifiers (event IDs, calendar IDs, internal codes).
- Focus on user-relevant information only: titles, dates, times, counts.
- If update items include before/after, explain changes by comparing before vs after naturally.
- Avoid structured formats - write in natural sentences.

**Format:**
- Markdown only, using headings/style/lists/horizontal rules only.
- Keep it concise, concrete, and grounded in payload data only.
"""

RESPONSE_AGENT_RETRY_DEVELOPER_PROMPT = """Previous attempt returned empty output.
Return one Markdown response now.

Rules:
- No JSON
- Allowed Markdown only: headings, inline style, lists, horizontal rules
- Do NOT use links, tables, blockquotes, code blocks, or raw HTML
- Write in natural, conversational Korean
- NO technical IDs or structured formats
- At least one sentence
"""


def _compact_context(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
  if not isinstance(context, dict):
    return {"events": [], "tasks": [], "scope": None}

  events = context.get("events") if isinstance(context.get("events"), list) else []
  tasks = context.get("tasks") if isinstance(context.get("tasks"), list) else []
  scope = context.get("scope") if isinstance(context.get("scope"), dict) else None

  compact_events: List[Dict[str, Any]] = []
  for event in events[:80]:
    if not isinstance(event, dict):
      continue
    compact_events.append({
        "id": event.get("id"),
        "calendar_id": event.get("calendar_id"),
        "title": event.get("title"),
        "start": event.get("start"),
        "end": event.get("end"),
        "location": event.get("location"),
    })

  compact_tasks: List[Dict[str, Any]] = []
  for task in tasks[:80]:
    if not isinstance(task, dict):
      continue
    compact_tasks.append({
        "id": task.get("id"),
        "title": task.get("title"),
        "status": task.get("status"),
        "due": task.get("due"),
    })

  return {
      "events": compact_events,
      "tasks": compact_tasks,
      "scope": scope,
  }


def _extract_change_count(change: Dict[str, Any]) -> int:
  data = change.get("data") if isinstance(change.get("data"), dict) else {}
  candidates = (
      data.get("count"),
      data.get("updated_count"),
      data.get("deleted_count"),
  )
  for raw in candidates:
    if isinstance(raw, int) and raw > 0:
      return raw
  items = data.get("items")
  if isinstance(items, list) and items:
    return len(items)
  return 1


def _fallback_response_text(changes: List[Dict[str, Any]],
                            summary_requests: List[Dict[str, Any]],
                            context: Dict[str, Any]) -> str:
  parts: List[str] = []

  if changes:
    ok_changes = [item for item in changes if item.get("ok") is True]
    failed_changes = [item for item in changes if item.get("ok") is False]
    parts.append(
        f"Processed changes: {len(ok_changes)} succeeded, {len(failed_changes)} failed.")
    for change in ok_changes[:8]:
      intent = str(change.get("intent") or "")
      count = _extract_change_count(change)
      parts.append(f"- {intent} ({count})")
    for change in failed_changes[:4]:
      intent = str(change.get("intent") or "")
      error = str(change.get("error") or "failed")
      parts.append(f"- {intent} failed: {error}")

  if summary_requests:
    events = context.get("events") if isinstance(context.get("events"), list) else []
    tasks = context.get("tasks") if isinstance(context.get("tasks"), list) else []
    parts.append(f"Calendar summary: {len(events)} events in the requested range.")
    completed_count = sum(
        1 for task in tasks if str(task.get("status") or "") == "completed")
    open_count = max(0, len(tasks) - completed_count)
    parts.append(
        f"Task summary: {len(tasks)} total, {completed_count} completed, {open_count} open.")

  if parts:
    return "\n".join(parts).strip()
  return "Request processed."


def _build_primary_payload(user_text: str,
                           intent: str,
                           now_iso: str,
                           timezone_name: str,
                           language_code: str,
                           changes: List[Dict[str, Any]],
                           summary_requests: List[Dict[str, Any]],
                           compact_context: Dict[str, Any]) -> Dict[str, Any]:
  return {
      "user_text": user_text,
      "intent": intent,
      "now_iso": now_iso,
      "timezone": timezone_name,
      "language": language_code,
      "changes": changes[:24],
      "summary_requests": summary_requests[:8],
      "context": compact_context,
  }


def _build_retry_payload(primary_payload: Dict[str, Any]) -> Dict[str, Any]:
  context = primary_payload.get("context")
  events = context.get("events") if isinstance(context, dict) and isinstance(
      context.get("events"), list) else []
  tasks = context.get("tasks") if isinstance(context, dict) and isinstance(
      context.get("tasks"), list) else []
  changes = primary_payload.get("changes") if isinstance(
      primary_payload.get("changes"), list) else []
  summary_requests = primary_payload.get("summary_requests") if isinstance(
      primary_payload.get("summary_requests"), list) else []
  return {
      "intent": primary_payload.get("intent"),
      "language": primary_payload.get("language"),
      "changes_count": len(changes),
      "changes_preview": changes[:8],
      "summary_request_count": len(summary_requests),
      "summary_requests": summary_requests,
      "context_counts": {
          "events": len(events),
          "tasks": len(tasks),
      },
      "context_preview": {
          "events": events[:20],
          "tasks": tasks[:20],
      },
  }


async def _run_response_llm(*,
                            model: str,
                            system_prompt: str,
                            developer_prompt: str,
                            payload: Dict[str, Any],
                            on_stream_delta: Optional[Callable[[str], None]] = None
                            ) -> tuple[str, Dict[str, Any]]:
  return await run_text_completion(
      model=model,
      system_prompt=system_prompt,
      developer_prompt=developer_prompt,
      user_payload=payload,
      reasoning_effort=_SETTINGS["reasoning_effort"],
      verbosity="low",
      gemini_thinking_level=_SETTINGS["gemini_thinking_level"],
      max_completion_tokens=3000,
      on_stream_delta=on_stream_delta,
  )


def _build_attempt_debug(*,
                         try_index: int,
                         payload: Dict[str, Any],
                         system_prompt: str,
                         developer_prompt: str,
                         raw_output: str,
                         llm_meta: Dict[str, Any]) -> Dict[str, Any]:
  safe_meta = llm_meta if isinstance(llm_meta, dict) else {}
  return {
      "try": try_index,
      "payload": payload,
      "system_prompt": system_prompt,
      "developer_prompt": developer_prompt,
      "raw_output": raw_output,
      "model": str(safe_meta.get("model") or RESPONSE_AGENT_MODEL),
      "resolved_model": safe_meta.get("resolved_model"),
      "reasoning_effort": safe_meta.get("reasoning_effort"),
      "thinking_level": safe_meta.get("thinking_level"),
      "provider": safe_meta.get("provider"),
      "llm_available": safe_meta.get("llm_available"),
      "unavailable_reason": safe_meta.get("unavailable_reason"),
      "llm_error": safe_meta.get("llm_error"),
      "llm_output_empty_or_error": safe_meta.get("llm_output_empty_or_error"),
      "meta": safe_meta,
  }


async def build_response_text(
    *,
    user_text: str,
    intent: str,
    now_iso: str,
    timezone_name: str,
    language_code: str,
    changes: Optional[List[Dict[str, Any]]] = None,
    summary_requests: Optional[List[Dict[str, Any]]] = None,
    context: Optional[Dict[str, Any]] = None,
    debug_capture: Optional[Dict[str, Any]] = None,
    on_stream_delta: Optional[Callable[[str], None]] = None,
) -> str:
  safe_changes = changes if isinstance(changes, list) else []
  safe_summary_requests = summary_requests if isinstance(summary_requests, list) else []
  # Only provide context to the LLM when summarize intent is requested.
  if safe_summary_requests:
    compact_context = _compact_context(context)
  else:
    compact_context = {"events": [], "tasks": [], "scope": None}
  payload = _build_primary_payload(
      user_text=user_text,
      intent=intent,
      now_iso=now_iso,
      timezone_name=timezone_name,
      language_code=language_code,
      changes=safe_changes,
      summary_requests=safe_summary_requests,
      compact_context=compact_context,
  )

  system_prompt = RESPONSE_AGENT_SYSTEM_PROMPT.format(
      now_iso=now_iso, timezone=timezone_name)

  attempts: List[Dict[str, Any]] = []
  text, llm_meta = await _run_response_llm(
      model=RESPONSE_AGENT_MODEL,
      system_prompt=system_prompt,
      developer_prompt=RESPONSE_AGENT_DEVELOPER_PROMPT,
      payload=payload,
      on_stream_delta=on_stream_delta,
  )
  attempts.append(
      _build_attempt_debug(
          try_index=1,
          payload=payload,
          system_prompt=system_prompt,
          developer_prompt=RESPONSE_AGENT_DEVELOPER_PROMPT,
          raw_output=text,
          llm_meta=llm_meta,
      ))

  candidate = str(text or "").strip()
  if not candidate:
    retry_payload = _build_retry_payload(payload)
    retry_text, retry_meta = await _run_response_llm(
        model=RESPONSE_AGENT_MODEL,
        system_prompt=system_prompt,
        developer_prompt=RESPONSE_AGENT_RETRY_DEVELOPER_PROMPT,
        payload=retry_payload,
        on_stream_delta=on_stream_delta,
    )
    attempts.append(
        _build_attempt_debug(
            try_index=2,
            payload=retry_payload,
            system_prompt=system_prompt,
            developer_prompt=RESPONSE_AGENT_RETRY_DEVELOPER_PROMPT,
            raw_output=retry_text,
            llm_meta=retry_meta,
        ))
    candidate = str(retry_text or "").strip()

  fallback = ""
  if not candidate:
    fallback = _fallback_response_text(safe_changes, safe_summary_requests,
                                       compact_context)
    candidate = fallback

  if debug_capture is not None:
    last_attempt = attempts[-1] if attempts else {}
    first_attempt = attempts[0] if attempts else {}
    debug_capture.clear()
    debug_capture.update({
        "payload": payload,
        "model": str(last_attempt.get("model") or RESPONSE_AGENT_MODEL),
        "resolved_model": last_attempt.get("resolved_model"),
        "reasoning_effort": last_attempt.get("reasoning_effort"),
        "thinking_level": last_attempt.get("thinking_level"),
        "provider": str(last_attempt.get("provider")
                         or first_attempt.get("provider")
                         or ""),
        "system_prompt": system_prompt,
        "developer_prompt": RESPONSE_AGENT_DEVELOPER_PROMPT,
        "retry_developer_prompt": RESPONSE_AGENT_RETRY_DEVELOPER_PROMPT,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "raw_output": str(last_attempt.get("raw_output") or ""),
        "llm_available": last_attempt.get("llm_available"),
        "unavailable_reason": last_attempt.get("unavailable_reason"),
        "llm_error": last_attempt.get("llm_error"),
        "llm_output_empty_or_error": last_attempt.get("llm_output_empty_or_error"),
        "fallback_used": bool(fallback),
        "fallback_response": fallback or None,
    })

  return candidate
