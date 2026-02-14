from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TypeVar

from pydantic import BaseModel

from ..llm import get_async_client

try:
  from google import genai  # type: ignore
  from google.genai import types as genai_types  # type: ignore
except Exception:  # pragma: no cover - optional dependency
  genai = None  # type: ignore
  genai_types = None  # type: ignore


T = TypeVar("T", bound=BaseModel)

_gemini_client: Any = None
_gemini_api_key_cached: str = ""
_GEMINI_DEFAULT_THINKING_LEVEL = "MINIMAL"


def _is_llm_debug_enabled() -> bool:
  return os.getenv("LLM_DEBUG", "0").strip() == "1"


def _get_openai_reasoning_effort() -> str:
  """Get OpenAI reasoning_effort from environment or default to 'low'."""
  return os.getenv("OPENAI_REASONING_EFFORT", "low").strip() or "low"


def _get_openai_verbosity() -> str:
  """Get OpenAI verbosity from environment or default to 'low'."""
  return os.getenv("OPENAI_VERBOSITY", "low").strip() or "low"


def _print_raw_output(*,
                      kind: str,
                      provider: str,
                      model: str,
                      raw_output: str,
                      resolved_model: Optional[str] = None,
                      schema_mode: Optional[str] = None,
                      reasoning_effort: Optional[str] = None,
                      thinking_level: Optional[str] = None) -> None:
  if not _is_llm_debug_enabled():
    return
  meta_parts = [
      f"kind={kind}",
      f"provider={provider}",
      f"model={model}",
  ]
  if resolved_model:
    meta_parts.append(f"resolved_model={resolved_model}")
  if reasoning_effort:
    meta_parts.append(f"reasoning_effort={reasoning_effort}")
  if thinking_level:
    meta_parts.append(f"thinking_level={thinking_level}")
  if schema_mode:
    meta_parts.append(f"schema_mode={schema_mode}")
  print(f"[AGENT LLM RAW] {' '.join(meta_parts)}", flush=True)
  print(raw_output if raw_output else "(empty)", flush=True)
  print("[AGENT LLM RAW END]", flush=True)


def _extract_message_text(content: Any) -> str:
  if isinstance(content, str):
    return content.strip()
  if isinstance(content, list):
    chunks = []
    for item in content:
      if isinstance(item, dict):
        text_val = item.get("text")
        if isinstance(text_val, str) and text_val.strip():
          chunks.append(text_val.strip())
      elif isinstance(item, str) and item.strip():
        chunks.append(item.strip())
    return " ".join(chunks).strip()
  return ""


def _provider_for_model(model: str) -> str:
  provider = os.getenv("AGENT_LLM_PROVIDER", "auto").strip().lower()
  if provider in ("openai", "gemini"):
    return provider
  model_name = str(model or "").strip().lower()
  if model_name.startswith("gemini") or model_name.startswith("models/gemini"):
    return "gemini"
  return "openai"


def _canonical_gemini_model(model: str) -> str:
  model_name = str(model or "").strip()
  if not model_name:
    return "models/gemini-flash-latest"
  if model_name.startswith("models/"):
    return model_name
  return f"models/{model_name}"


def get_agent_llm_settings(prefix: str) -> Dict[str, Optional[str]]:
  """
  Unified helper to fetch agent-specific LLM settings from environment.
  Supports both OpenAI reasoning_effort and Gemini thinking_level.
  """
  prefix = prefix.upper().strip()
  return {
      "reasoning_effort": os.getenv(f"AGENT_{prefix}_OPENAI_REASONING_EFFORT") or os.getenv(f"AGENT_{prefix}_REASONING_EFFORT"),
      "gemini_thinking_level": os.getenv(f"AGENT_{prefix}_GEMINI_THINKING_LEVEL") or os.getenv(f"AGENT_{prefix}_THINKING_LEVEL"),
  }


def _gemini_text_from_response(response: Any) -> str:
  try:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
      return text.strip()
  except Exception:
    pass
  try:
    candidates = getattr(response, "candidates", None)
    if isinstance(candidates, list):
      chunks = []
      for candidate in candidates:
        content = getattr(candidate, "content", None)
        if content is None:
          continue
        parts = getattr(content, "parts", None)
        if not isinstance(parts, list):
          continue
        for part in parts:
          text_val = getattr(part, "text", None)
          if isinstance(text_val, str) and text_val.strip():
            chunks.append(text_val.strip())
      return " ".join(chunks).strip()
  except Exception:
    return ""
  return ""


def _gemini_client_or_reason() -> Tuple[Any, Optional[str]]:
  global _gemini_client, _gemini_api_key_cached
  if genai is None:
    return None, "google_genai_not_installed"
  gemini_api_key = os.getenv("GEMINI_API_KEY", "").strip()
  if not gemini_api_key:
    return None, "gemini_api_key_missing"
  if _gemini_client is None or _gemini_api_key_cached != gemini_api_key:
    _gemini_client = genai.Client(api_key=gemini_api_key)
    _gemini_api_key_cached = gemini_api_key
  return _gemini_client, None


def _compose_prompt(system_prompt: str,
                    user_content: str,
                    developer_prompt: Optional[str]) -> str:
  instruction = system_prompt
  if isinstance(developer_prompt, str) and developer_prompt.strip():
    instruction = f"{instruction}\n\n{developer_prompt.strip()}"
  return f"{instruction}\n\nUser:\n{user_content}"


def _clean_json_text(text: str) -> str:
  cleaned = (text or "").strip()
  if cleaned.startswith("```"):
    cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned).strip()
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
  return cleaned


def _validate_structured_response(response_model: Type[T],
                                  raw_output: str) -> Optional[T]:
  if not raw_output:
    return None
  candidates = [raw_output, _clean_json_text(raw_output)]
  cleaned = candidates[-1]
  if cleaned:
    left = cleaned.find("{")
    right = cleaned.rfind("}")
    if left != -1 and right != -1 and right > left:
      candidates.append(cleaned[left:right + 1])
    # Handle JSON array responses — try first element
    stripped = cleaned.strip()
    if stripped.startswith("["):
      try:
        arr = json.loads(stripped)
        if isinstance(arr, list) and arr and isinstance(arr[0], dict):
          candidates.append(json.dumps(arr[0], ensure_ascii=False))
      except Exception:
        pass
  seen = set()
  for candidate in candidates:
    text = (candidate or "").strip()
    if not text or text in seen:
      continue
    seen.add(text)
    try:
      return response_model.model_validate_json(text)
    except Exception:
      continue
  return None


def _coerce_gemini_parsed_response(response_model: Type[T],
                                   parsed_value: Any) -> Optional[T]:
  if parsed_value is None:
    return None
  if isinstance(parsed_value, response_model):
    return parsed_value
  if isinstance(parsed_value, str):
    return _validate_structured_response(response_model, parsed_value)
  try:
    return response_model.model_validate(parsed_value)
  except Exception:
    return None


def _gemini_thinking_level(override_level: Optional[str] = None) -> Optional[str]:
  raw = override_level
  if raw is None:
    raw = os.getenv("GEMINI_THINKING_LEVEL", _GEMINI_DEFAULT_THINKING_LEVEL)
  value = str(raw or "").strip().upper()
  if value in ("NONE", "MINIMAL", "LOW", "MEDIUM", "HIGH", "XHIGH", "THINKING_LEVEL_UNSPECIFIED"):
    return value
  return None


def _attach_gemini_thinking_config(config: Dict[str, Any],
                                   override_level: Optional[str] = None) -> None:
  level = _gemini_thinking_level(override_level=override_level)
  if not level:
    return
  config["thinking_config"] = {
      "thinking_level": level,
  }


def _build_gemini_structured_config(max_completion_tokens: int,
                                    gemini_thinking_level: Optional[str] = None) -> Optional[Any]:
  config: Dict[str, Any] = {
      "response_mime_type": "application/json",
  }
  if isinstance(max_completion_tokens, int) and max_completion_tokens > 0:
    config["max_output_tokens"] = max_completion_tokens
  _attach_gemini_thinking_config(config,
                                 override_level=gemini_thinking_level)

  if genai_types is not None:
    try:
      return genai_types.GenerateContentConfig(**config)
    except Exception:
      pass
  return config


def _gemini_structured_sync(client: Any,
                            model: str,
                            prompt: str,
                            response_model: Type[T],
                            max_completion_tokens: int,
                            gemini_thinking_level: Optional[str] = None) -> Tuple[Optional[T], str, str, str]:
  used_model = _canonical_gemini_model(model)
  used_schema_mode = "json_mime_only"
  config = _build_gemini_structured_config(
      max_completion_tokens=max_completion_tokens,
      gemini_thinking_level=gemini_thinking_level,
  )
  if config is None:
    return None, "", used_model, used_schema_mode

  # --- attempt 1 ---
  try:
    response = client.models.generate_content(
        model=used_model,
        contents=prompt,
        config=config,
    )
  except Exception as exc:
    print(f"[STRUCTURED #1] EXCEPTION model={used_model} mode={used_schema_mode}: {exc}", flush=True)
    raise

  raw_output = _gemini_text_from_response(response)
  parsed = _coerce_gemini_parsed_response(
      response_model, getattr(response, "parsed", None))
  if parsed is None:
    parsed = _validate_structured_response(response_model, raw_output)
  if parsed is not None:
    print(f"[STRUCTURED #1] OK model={used_model} mode={used_schema_mode} schema={response_model.__name__}", flush=True)
    return parsed, raw_output, used_model, used_schema_mode

  print(f"[STRUCTURED #1] PARSE_FAIL model={used_model} mode={used_schema_mode} schema={response_model.__name__} raw_len={len(raw_output)}", flush=True)

  # --- attempt 2: retry once with error feedback ---
  # Build a compact schema hint from the response model
  try:
    schema_fields = {
        k: v.annotation.__name__ if hasattr(v.annotation, "__name__") else str(v.annotation)
        for k, v in response_model.model_fields.items()
    }
  except Exception:
    schema_fields = list(response_model.model_fields.keys())

  retry_suffix = (
      f"\n\n[RETRY] Your previous response could not be parsed as valid JSON matching the expected schema.\n"
      f"Expected schema fields: {json.dumps(schema_fields, ensure_ascii=False)}\n"
      f"Your previous output (first 500 chars): {raw_output[:500]}\n"
      f"Please return ONLY valid JSON matching the schema. No markdown, no explanation."
  )
  retry_prompt = prompt + retry_suffix

  try:
    response2 = client.models.generate_content(
        model=used_model,
        contents=retry_prompt,
        config=config,
    )
  except Exception as exc:
    print(f"[STRUCTURED #2] EXCEPTION model={used_model} mode={used_schema_mode}: {exc}", flush=True)
    # Return first attempt's raw output on retry exception
    return None, raw_output, used_model, used_schema_mode

  raw_output2 = _gemini_text_from_response(response2)
  parsed2 = _coerce_gemini_parsed_response(
      response_model, getattr(response2, "parsed", None))
  if parsed2 is None:
    parsed2 = _validate_structured_response(response_model, raw_output2)
  if parsed2 is not None:
    print(f"[STRUCTURED #2] OK model={used_model} mode={used_schema_mode} schema={response_model.__name__}", flush=True)
    return parsed2, raw_output2, used_model, used_schema_mode

  print(f"[STRUCTURED #2] PARSE_FAIL model={used_model} mode={used_schema_mode} schema={response_model.__name__} raw_len={len(raw_output2)}", flush=True)
  return None, raw_output2, used_model, used_schema_mode


def _gemini_text_sync(client: Any,
                      model: str,
                      prompt: str,
                      max_completion_tokens: int,
                      gemini_thinking_level: Optional[str] = None) -> str:
  resolved_model = _canonical_gemini_model(model)
  config: Dict[str, Any] = {}
  if isinstance(max_completion_tokens, int) and max_completion_tokens > 0:
    config["max_output_tokens"] = max_completion_tokens
  _attach_gemini_thinking_config(config,
                                 override_level=gemini_thinking_level)
  response = client.models.generate_content(
      model=resolved_model,
      contents=prompt,
      config=config or None,
  )
  return _gemini_text_from_response(response)


def _extract_stream_delta_text(delta_content: Any) -> str:
  if isinstance(delta_content, str):
    return delta_content
  if isinstance(delta_content, list):
    chunks: List[str] = []
    for item in delta_content:
      if isinstance(item, str):
        chunks.append(item)
        continue
      text_val = None
      if isinstance(item, dict):
        text_val = item.get("text")
      else:
        text_val = getattr(item, "text", None)
      if isinstance(text_val, str):
        chunks.append(text_val)
    return "".join(chunks)
  text_val = getattr(delta_content, "text", None)
  if isinstance(text_val, str):
    return text_val
  return ""


def _gemini_stream_chunk_text(chunk: Any) -> str:
  text = getattr(chunk, "text", None)
  if isinstance(text, str):
    return text
  try:
    candidates = getattr(chunk, "candidates", None)
    if not isinstance(candidates, list):
      return ""
    collected: List[str] = []
    for candidate in candidates:
      content = getattr(candidate, "content", None)
      if content is None:
        continue
      parts = getattr(content, "parts", None)
      if not isinstance(parts, list):
        continue
      for part in parts:
        text_val = getattr(part, "text", None)
        if isinstance(text_val, str):
          collected.append(text_val)
    return "".join(collected)
  except Exception:
    return ""


def _gemini_text_stream_sync(client: Any,
                             model: str,
                             prompt: str,
                             max_completion_tokens: int,
                             gemini_thinking_level: Optional[str] = None,
                             on_chunk: Optional[Callable[[str], None]] = None) -> str:
  resolved_model = _canonical_gemini_model(model)
  config: Dict[str, Any] = {}
  if isinstance(max_completion_tokens, int) and max_completion_tokens > 0:
    config["max_output_tokens"] = max_completion_tokens
  _attach_gemini_thinking_config(config,
                                 override_level=gemini_thinking_level)
  stream = client.models.generate_content_stream(
      model=resolved_model,
      contents=prompt,
      config=config or None,
  )
  chunks: List[str] = []
  for chunk in stream:
    piece = _gemini_stream_chunk_text(chunk)
    if not piece:
      continue
    chunks.append(piece)
    if callable(on_chunk):
      try:
        on_chunk(piece)
      except Exception:
        continue
  return "".join(chunks)


def _compose_openai_messages(system_prompt: str,
                             developer_prompt: Optional[str],
                             user_content: str) -> List[Dict[str, str]]:
  instruction = system_prompt
  if isinstance(developer_prompt, str) and developer_prompt.strip():
    instruction = f"{instruction}\n\n{developer_prompt.strip()}"

  # OpenAI 공식 가이드: JSON 모드 사용 시 시스템 프롬프트에 'json' 명시 필수
  if "json" not in instruction.lower():
    instruction += "\n\nResponse must be a valid JSON object."

  return [
      {
          "role": "system",
          "content": instruction,
      },
      {
          "role": "user",
          "content": user_content,
      },
  ]


async def run_structured_completion(
    *,
    model: str,
    system_prompt: str,
    developer_prompt: Optional[str],
    user_payload: Dict[str, Any],
    response_model: Type[T],
    max_completion_tokens: int,
    reasoning_effort: Optional[str] = None,
    verbosity: Optional[str] = None,
    gemini_thinking_level: Optional[str] = None,
) -> Tuple[Optional[T], str, Dict[str, Any]]:
  # Use environment variables if not explicitly provided
  if reasoning_effort is None:
    reasoning_effort = _get_openai_reasoning_effort()
  if verbosity is None:
    verbosity = _get_openai_verbosity()
  provider = _provider_for_model(model)
  user_content = json.dumps(user_payload, ensure_ascii=False)

  if provider == "gemini":
    client, unavailable_reason = _gemini_client_or_reason()
    if client is None:
      return None, "", {
          "model": model,
          "provider": provider,
          "llm_available": False,
          "unavailable_reason": unavailable_reason,
      }
    prompt = _compose_prompt(system_prompt, user_content, developer_prompt)
    try:
      parsed, raw_output, resolved_model, schema_mode = await asyncio.to_thread(
          _gemini_structured_sync,
          client,
          model,
          prompt,
          response_model,
          max_completion_tokens,
          gemini_thinking_level,
      )
      _print_raw_output(
          kind="structured",
          provider=provider,
          model=model,
          raw_output=raw_output,
          resolved_model=resolved_model,
          schema_mode=schema_mode,
          thinking_level=gemini_thinking_level,
      )
      return parsed, raw_output, {
          "model": model,
          "resolved_model": resolved_model,
          "gemini_schema_mode": schema_mode,
          "thinking_level": gemini_thinking_level,
          "fallback_used": False,
          "provider": provider,
          "llm_available": True,
      }
    except Exception as exc:
      return None, "", {
          "model": model,
          "provider": provider,
          "thinking_level": gemini_thinking_level,
          "llm_available": True,
          "llm_output_empty_or_error": True,
          "llm_error": str(exc),
      }

  try:
    client = get_async_client()
  except Exception:
    return None, "", {
        "model": model,
        "provider": provider,
        "reasoning_effort": reasoning_effort,
        "llm_available": False,
    }

  messages = _compose_openai_messages(system_prompt, developer_prompt,
                                      user_content)

  try:
    completion = await client.chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        max_completion_tokens=max_completion_tokens,
    )
    raw_output = _extract_message_text(completion.choices[0].message.content)
    parsed = _validate_structured_response(response_model, raw_output)
    _print_raw_output(
        kind="structured",
        provider=provider,
        model=model,
        raw_output=raw_output,
        reasoning_effort=reasoning_effort,
    )
    return parsed, raw_output, {
        "model": model,
        "provider": provider,
        "reasoning_effort": reasoning_effort,
        "llm_available": True,
    }
  except Exception as exc:
    print(f"[AGENT LLM ERROR] model={model} provider={provider} error={exc}", flush=True)
    import traceback
    traceback.print_exc()
    return None, "", {
        "model": model,
        "provider": provider,
        "reasoning_effort": reasoning_effort,
        "llm_available": True,
        "llm_output_empty_or_error": True,
        "llm_error": str(exc),
    }


async def run_text_completion(
    *,
    model: str,
    system_prompt: str,
    developer_prompt: Optional[str],
    user_payload: Dict[str, Any],
    max_completion_tokens: int,
    reasoning_effort: Optional[str] = None,
    verbosity: Optional[str] = None,
    gemini_thinking_level: Optional[str] = None,
    on_stream_delta: Optional[Callable[[str], None]] = None,
) -> Tuple[str, Dict[str, Any]]:
  # Use environment variables if not explicitly provided
  if reasoning_effort is None:
    reasoning_effort = _get_openai_reasoning_effort()
  if verbosity is None:
    verbosity = _get_openai_verbosity()
  provider = _provider_for_model(model)
  user_content = json.dumps(user_payload, ensure_ascii=False)

  if provider == "gemini":
    client, unavailable_reason = _gemini_client_or_reason()
    if client is None:
      return "", {
          "model": model,
          "provider": provider,
          "llm_available": False,
          "unavailable_reason": unavailable_reason,
      }
    prompt = _compose_prompt(system_prompt, user_content, developer_prompt)
    resolved_model = _canonical_gemini_model(model)
    try:
      if callable(on_stream_delta):
        loop = asyncio.get_running_loop()
        stream_queue: asyncio.Queue[str] = asyncio.Queue()

        def _on_chunk_sync(piece: str) -> None:
          loop.call_soon_threadsafe(stream_queue.put_nowait, piece)

        worker = asyncio.create_task(
            asyncio.to_thread(
                _gemini_text_stream_sync,
                client,
                model,
                prompt,
                max_completion_tokens,
                gemini_thinking_level,
                _on_chunk_sync,
            ))
        emitted: List[str] = []
        while True:
          if worker.done() and stream_queue.empty():
            break
          try:
            piece = await asyncio.wait_for(stream_queue.get(), timeout=0.05)
          except asyncio.TimeoutError:
            continue
          if not piece:
            continue
          emitted.append(piece)
          try:
            on_stream_delta(piece)
          except Exception:
            continue
        text = await worker
        if not text and emitted:
          text = "".join(emitted)
      else:
        text = await asyncio.to_thread(
            _gemini_text_sync,
            client,
            model,
            prompt,
            max_completion_tokens,
            gemini_thinking_level,
        )
      _print_raw_output(
          kind="text",
          provider=provider,
          model=model,
          raw_output=text,
          resolved_model=resolved_model,
          thinking_level=gemini_thinking_level,
      )
      return text, {
          "model": model,
          "resolved_model": resolved_model,
          "provider": provider,
          "thinking_level": gemini_thinking_level,
          "llm_available": True,
      }
    except Exception as exc:
      print(f"[AGENT LLM ERROR] Gemini text model={model} error={exc}", flush=True)
      import traceback
      traceback.print_exc()
      return "", {
          "model": model,
          "provider": provider,
          "thinking_level": gemini_thinking_level,
          "llm_available": True,
          "llm_output_empty_or_error": True,
          "llm_error": str(exc),
      }

  try:
    client = get_async_client()
  except Exception:
    return "", {
        "model": model,
        "provider": provider,
        "reasoning_effort": reasoning_effort,
        "llm_available": False,
    }

  messages = _compose_openai_messages(system_prompt, developer_prompt,
                                      user_content)

  if callable(on_stream_delta):
    try:
      stream = await client.chat.completions.create(
          model=model,
          messages=messages,
          reasoning_effort=reasoning_effort,
          verbosity=verbosity,
          max_completion_tokens=max_completion_tokens,
          stream=True,
      )
      chunks: List[str] = []
      async for event in stream:
        choices = getattr(event, "choices", None)
        if not isinstance(choices, list) or not choices:
          continue
        delta = getattr(choices[0], "delta", None)
        if delta is None:
          continue
        piece = _extract_stream_delta_text(getattr(delta, "content", None))
        if not piece:
          continue
        chunks.append(piece)
        try:
          on_stream_delta(piece)
        except Exception:
          continue
      text = "".join(chunks)
      _print_raw_output(
          kind="text",
          provider=provider,
          model=model,
          raw_output=text,
          reasoning_effort=reasoning_effort,
      )
      return text, {
          "model": model,
          "provider": provider,
          "reasoning_effort": reasoning_effort,
          "llm_available": True,
      }
    except Exception:
      # Fall through to non-streaming path when stream mode is unavailable.
      pass

  try:
    completion = await client.chat.completions.create(
        model=model,
        messages=messages,
        reasoning_effort=reasoning_effort,
        verbosity=verbosity,
        max_completion_tokens=max_completion_tokens,
    )
    text = _extract_message_text(completion.choices[0].message.content)
    _print_raw_output(
        kind="text",
        provider=provider,
        model=model,
        raw_output=text,
        reasoning_effort=reasoning_effort,
    )
    return text, {
        "model": model,
        "provider": provider,
        "reasoning_effort": reasoning_effort,
        "llm_available": True,
    }
  except Exception as exc:
    print(f"[AGENT LLM ERROR] OpenAI text model={model} error={exc}", flush=True)
    import traceback
    traceback.print_exc()
    return "", {
        "model": model,
        "provider": provider,
        "llm_available": True,
        "llm_output_empty_or_error": True,
        "llm_error": str(exc),
    }


# Print OpenAI settings on module load
print(f"[LLM_PROVIDER] OpenAI reasoning_effort={_get_openai_reasoning_effort()}, verbosity={_get_openai_verbosity()}", flush=True)
