from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from .llm_provider import run_text_completion, get_agent_llm_settings
from .schemas import PlanStep, ValidationIssue

QUESTION_AGENT_MODEL = os.getenv("AGENT_QUESTION_MODEL", "gpt-5-nano").strip()
_SETTINGS = get_agent_llm_settings("QUESTION")
print(f"[QUESTION_AGENT] Loaded model: {QUESTION_AGENT_MODEL}, provider: {os.getenv('AGENT_LLM_PROVIDER', 'auto')}", flush=True)

EARLY_CLARIFY_SYSTEM_PROMPT = """You are a Meta Clarification Agent.

Your role: Clarify the type of action the user wants to take.

The system handles:
- Schedule events (add, change, move, cancel, find, review)
- Tasks (create, manage, mark done, review)
- Availability checks and time summaries
- Time block planning

Rules:
- Ask only to clarify the action type
- Do NOT ask for details (dates, times, titles, durations)
- Use natural, everyday language
- Output must be Markdown
- Allowed Markdown only: headings, inline style, lists, horizontal rules
- Do NOT use links, tables, blockquotes, code blocks, or raw HTML
- No JSON, no technical terms
- Keep it concise and friendly

Goal: Help the user express their intent clearly.
"""

EARLY_CLARIFY_DEVELOPER_PROMPT = """Available Data:
- router: Current intent classification
- user_text: Full conversation history
- now_iso: Current timestamp
- context: User context
- target_language: Response language

Task:
Focus on understanding the user's high-level intent.
If ambiguous (multiple possible actions), present options in simple language.
If unclear whether it's about schedules or tasks, ask directly.

Style: Short, natural questions. Avoid technical terms.
Format: Markdown only, using headings/style/lists/horizontal rules only.
"""

QUESTION_SYSTEM_PROMPT_TEMPLATE = """You are a clarification question generator.

Write concise {target_language} questions to resolve validation issues.
Output Markdown only (no JSON).
Allowed Markdown only: headings, inline style, lists, horizontal rules.
Do NOT use links, tables, blockquotes, code blocks, or raw HTML.
Ask minimal questions needed to continue.
Use natural, everyday language.
Keep it concise and friendly.
If multiple issues exist, ask multiple short questions.
For ambiguous references, ask user to choose one candidate.
"""

QUESTION_DEVELOPER_PROMPT = """Available Data:
- input_as_text: Full conversation
- issues: Validation problems to resolve
- issues_count: Total number of issues
- now_iso: Current timestamp
- target_language: Response language

Focus on the issues list and ask only what's needed to resolve them.
Style: Use natural, everyday language. Keep it concise and friendly.
Format: Markdown only, using headings/style/lists/horizontal rules only.
"""


def _language_label(language_code: str) -> str:
  mapping = {
      "ko": "Korean",
      "en": "English",
      "ja": "Japanese",
      "zh": "Chinese",
  }
  return mapping.get(language_code, "English")


def _default_question(language_code: str) -> str:
  _ = language_code
  return "Please share the required details so I can continue."


def _default_intent_clarify_question(language_code: str) -> str:
  _ = language_code
  return "What would you like to do right now: schedule, task, or availability check?"


async def build_clarification_question(input_as_text: str,
                                       plan: List[PlanStep],
                                       issues: List[ValidationIssue],
                                       now_iso: str,
                                       timezone: str,
                                       language_code: str,
                                       debug_capture: Optional[Dict[str, Any]] = None,
                                       on_stream_delta: Optional[Callable[[str], None]] = None) -> str:
  if not issues:
    return _default_question(language_code)

  payload = {
      "input_as_text": input_as_text,
      "issues": [issue.model_dump() for issue in issues[:8]],
      "issues_count": len(issues),
      "now_iso": now_iso,
      "target_language": language_code,
  }

  system_prompt = QUESTION_SYSTEM_PROMPT_TEMPLATE.format(
      target_language=_language_label(language_code))
  text, llm_meta = await run_text_completion(
      model=QUESTION_AGENT_MODEL,
      system_prompt=system_prompt,
      developer_prompt=QUESTION_DEVELOPER_PROMPT,
      user_payload=payload,
      reasoning_effort=_SETTINGS["reasoning_effort"],
      gemini_thinking_level=_SETTINGS["gemini_thinking_level"],
      verbosity="low",
      max_completion_tokens=5000,
      on_stream_delta=on_stream_delta,
  )
  if debug_capture is not None:
    debug_capture.clear()
    debug_capture.update({
        "payload": payload,
        "system_prompt": system_prompt,
        "developer_prompt": QUESTION_DEVELOPER_PROMPT,
        "raw_output": text,
        "model": str(llm_meta.get("model") or QUESTION_AGENT_MODEL),
        "resolved_model": llm_meta.get("resolved_model"),
        "reasoning_effort": llm_meta.get("reasoning_effort"),
        "thinking_level": llm_meta.get("thinking_level"),
        "provider": llm_meta.get("provider"),
        "llm_available": llm_meta.get("llm_available"),
        "llm_output_empty_or_error": llm_meta.get("llm_output_empty_or_error"),
    })
  if text:
    return text

  if debug_capture is not None:
    debug_capture.clear()
    debug_capture.update({
        "payload": payload,
        "system_prompt": system_prompt,
        "developer_prompt": QUESTION_DEVELOPER_PROMPT,
        "raw_output": "",
        "model": str(llm_meta.get("model") or QUESTION_AGENT_MODEL),
        "resolved_model": llm_meta.get("resolved_model"),
        "reasoning_effort": llm_meta.get("reasoning_effort"),
        "thinking_level": llm_meta.get("thinking_level"),
        "provider": llm_meta.get("provider"),
        "llm_available": llm_meta.get("llm_available"),
        "llm_output_empty_or_error": True,
    })
  return _default_question(language_code)


async def build_early_clarification_question(
    router_intent: str,
    input_as_text: str,
    now_iso: str,
    context: Dict[str, Any],
    language_code: str,
    debug_capture: Optional[Dict[str, Any]] = None,
    on_stream_delta: Optional[Callable[[str], None]] = None,
) -> str:
  payload = {
      "router": router_intent,
      "user_text": input_as_text,
      "now_iso": now_iso,
      "context": context,
      "target_language": language_code,
  }

  text, llm_meta = await run_text_completion(
      model=QUESTION_AGENT_MODEL,
      system_prompt=EARLY_CLARIFY_SYSTEM_PROMPT,
      developer_prompt=EARLY_CLARIFY_DEVELOPER_PROMPT,
      user_payload=payload,
      reasoning_effort=_SETTINGS["reasoning_effort"],
      gemini_thinking_level=_SETTINGS["gemini_thinking_level"],
      verbosity="low",
      max_completion_tokens=5000,
      on_stream_delta=on_stream_delta,
  )
  if debug_capture is not None:
    debug_capture.clear()
    debug_capture.update({
        "payload": payload,
        "system_prompt": EARLY_CLARIFY_SYSTEM_PROMPT,
        "developer_prompt": EARLY_CLARIFY_DEVELOPER_PROMPT,
        "raw_output": text,
        "model": str(llm_meta.get("model") or QUESTION_AGENT_MODEL),
        "resolved_model": llm_meta.get("resolved_model"),
        "reasoning_effort": llm_meta.get("reasoning_effort"),
        "thinking_level": llm_meta.get("thinking_level"),
        "provider": llm_meta.get("provider"),
        "llm_available": llm_meta.get("llm_available"),
        "llm_output_empty_or_error": llm_meta.get("llm_output_empty_or_error"),
    })
  if text:
    return text

  if debug_capture is not None:
    debug_capture.clear()
    debug_capture.update({
        "payload": payload,
        "system_prompt": EARLY_CLARIFY_SYSTEM_PROMPT,
        "developer_prompt": EARLY_CLARIFY_DEVELOPER_PROMPT,
        "raw_output": "",
        "model": str(llm_meta.get("model") or QUESTION_AGENT_MODEL),
        "resolved_model": llm_meta.get("resolved_model"),
        "reasoning_effort": llm_meta.get("reasoning_effort"),
        "thinking_level": llm_meta.get("thinking_level"),
        "provider": llm_meta.get("provider"),
        "llm_available": llm_meta.get("llm_available"),
        "llm_output_empty_or_error": True,
    })
  return _default_intent_clarify_question(language_code)
