from __future__ import annotations

import os
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from .llm_provider import run_structured_completion, get_agent_llm_settings

EVENT_TARGET_RESOLVER_MODEL = os.getenv("AGENT_EVENT_TARGET_RESOLVER_MODEL",
                                        "gpt-5-mini").strip()
_SETTINGS = get_agent_llm_settings("EVENT_TARGET_RESOLVER")
EVENT_TARGET_CANDIDATE_LIMIT = 40

EVENT_TARGET_RESOLVER_SYSTEM_PROMPT = """You resolve target events for calendar.update_event and calendar.cancel_event.
Return JSON only. No markdown or extra text.
Use only IDs from candidates. Never fabricate IDs.
Current date/time is {now_iso}. Timezone is {timezone}.

Choose one action:
- select_event: return selected_event_id from candidates.
- expand_context: return start_date/end_date (YYYY-MM-DD) to widen search.
- ask_user: when still ambiguous.

expand_context must be strictly broader than previous_range.
"""

EVENT_TARGET_RESOLVER_DEVELOPER_PROMPT = """Inputs:
- user_text
- intent
- args
- previous_range: {{start_date, end_date}}
- candidates: [{{event_id, title, start, end, calendar_id}}]

Decision:
- select_event when one candidate is clearly intended.
- expand_context when candidates are missing/out-of-range and a broader range can help.
- ask_user when multiple plausible candidates remain.

Examples:

User says "내일 팀미팅 취소", candidates has one match:
{{"action":"select_event","selected_event_id":"primary::abc123","reason":"Title and date match.","reason_codes":["EXACT_MATCH"],"confidence":0.92}}

User says "지난주 회의 수정", no candidates in current range:
{{"action":"expand_context","start_date":"2026-02-02","end_date":"2026-02-08","reason":"No candidate in current window, expanding to last week.","reason_codes":["OUT_OF_RANGE"],"confidence":0.7}}

User says "미팅 삭제해", multiple candidates with similar titles:
{{"action":"ask_user","reason":"Multiple meetings found. Which one?","reason_codes":["AMBIGUOUS"],"confidence":0.3}}
"""


class EventTargetResolverOutput(BaseModel):
  model_config = ConfigDict(extra="ignore")

  action: Literal["select_event", "expand_context", "ask_user"]
  selected_event_id: Optional[str] = None
  start_date: Optional[str] = None
  end_date: Optional[str] = None
  reason: str = ""
  reason_codes: List[str] = Field(default_factory=list, max_length=8)
  confidence: float = Field(default=0.0, ge=0.0, le=1.0)


def _default_ask_user() -> EventTargetResolverOutput:
  return EventTargetResolverOutput(
      action="ask_user",
      reason="Need user clarification to identify the target event.",
      reason_codes=["UNKNOWN"],
      confidence=0.0,
  )


async def resolve_event_target_with_debug(
    user_text: str,
    intent: str,
    args: Dict[str, Any],
    previous_start_date: str,
    previous_end_date: str,
    candidates: List[Dict[str, Any]],
    now_iso: str,
    timezone: str,
) -> Tuple[EventTargetResolverOutput, Dict[str, Any]]:
  payload = {
      "user_text": user_text,
      "intent": intent,
      "args": args,
      "previous_range": {
          "start_date": previous_start_date,
          "end_date": previous_end_date,
      },
      "candidates": candidates[:EVENT_TARGET_CANDIDATE_LIMIT],
  }

  system_prompt = EVENT_TARGET_RESOLVER_SYSTEM_PROMPT.format(
      now_iso=now_iso,
      timezone=timezone,
  )
  parsed, raw_output, llm_meta = await run_structured_completion(
      model=EVENT_TARGET_RESOLVER_MODEL,
      system_prompt=system_prompt,
      developer_prompt=EVENT_TARGET_RESOLVER_DEVELOPER_PROMPT,
      user_payload=payload,
      response_model=EventTargetResolverOutput,
      reasoning_effort=_SETTINGS["reasoning_effort"],
      gemini_thinking_level=_SETTINGS["gemini_thinking_level"],
      verbosity="low",
      max_completion_tokens=10000,
  )
  if parsed is None:
    fallback = _default_ask_user()
    return fallback, {
        "model": str(llm_meta.get("model") or EVENT_TARGET_RESOLVER_MODEL),
        "resolved_model": llm_meta.get("resolved_model"),
        "reasoning_effort": llm_meta.get("reasoning_effort"),
        "thinking_level": llm_meta.get("thinking_level"),
        "gemini_schema_mode": llm_meta.get("gemini_schema_mode"),
        "fallback_used": llm_meta.get("fallback_used"),
        "provider": llm_meta.get("provider"),
        "system_prompt": system_prompt,
        "developer_prompt": EVENT_TARGET_RESOLVER_DEVELOPER_PROMPT,
        "payload": payload,
        "raw_output": raw_output,
        "llm_available": llm_meta.get("llm_available"),
        "unavailable_reason": llm_meta.get("unavailable_reason"),
        "llm_error": llm_meta.get("llm_error"),
        "llm_output_empty_or_error": llm_meta.get("llm_output_empty_or_error", True),
    }
  return parsed, {
      "model": str(llm_meta.get("model") or EVENT_TARGET_RESOLVER_MODEL),
      "resolved_model": llm_meta.get("resolved_model"),
      "reasoning_effort": llm_meta.get("reasoning_effort"),
      "thinking_level": llm_meta.get("thinking_level"),
      "gemini_schema_mode": llm_meta.get("gemini_schema_mode"),
      "fallback_used": llm_meta.get("fallback_used"),
      "provider": llm_meta.get("provider"),
      "system_prompt": system_prompt,
      "developer_prompt": EVENT_TARGET_RESOLVER_DEVELOPER_PROMPT,
      "payload": payload,
      "raw_output": raw_output,
      "llm_available": llm_meta.get("llm_available", True),
      "parsed": parsed.model_dump(exclude_none=True),
  }
