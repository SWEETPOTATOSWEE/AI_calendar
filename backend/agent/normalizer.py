from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
import re
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Seoul"
DEFAULT_LANGUAGE = "en"

_KO_RE = re.compile("[\uac00-\ud7a3]")
_JA_RE = re.compile("[\u3040-\u30ff\u31f0-\u31ff]")
_ZH_RE = re.compile("[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def normalize_input_as_text(value: Optional[str]) -> str:
  if not isinstance(value, str):
    return ""
  return value.strip()


def detect_user_language(text: str) -> str:
  if not isinstance(text, str):
    return DEFAULT_LANGUAGE
  value = text.strip()
  if not value:
    return DEFAULT_LANGUAGE

  ko_count = len(_KO_RE.findall(value))
  ja_count = len(_JA_RE.findall(value))
  zh_count = len(_ZH_RE.findall(value))
  en_count = len(_LATIN_RE.findall(value))

  # Prioritize distinct scripts first.
  if ko_count > 0 and ko_count >= ja_count and ko_count >= zh_count:
    return "ko"
  if ja_count > 0 and ja_count > ko_count and ja_count >= zh_count:
    return "ja"
  if zh_count > 0 and zh_count > ko_count and zh_count > ja_count:
    return "zh"
  if en_count > 0:
    return "en"
  if ko_count > 0:
    return "ko"
  if ja_count > 0:
    return "ja"
  if zh_count > 0:
    return "zh"
  return DEFAULT_LANGUAGE


def resolve_timezone(requested_timezone: Optional[str],
                     preferences: Optional[Dict[str, Any]] = None) -> str:
  pref_timezone = None
  if isinstance(preferences, dict):
    pref_timezone = preferences.get("timezone")

  for candidate in (requested_timezone, pref_timezone, DEFAULT_TIMEZONE):
    if not isinstance(candidate, str):
      continue
    cleaned = candidate.strip()
    if not cleaned:
      continue
    try:
      ZoneInfo(cleaned)
      return cleaned
    except Exception:
      continue
  return DEFAULT_TIMEZONE


def now_iso_in_timezone(timezone_name: str) -> str:
  tz = ZoneInfo(timezone_name)
  return datetime.now(tz).isoformat(timespec="seconds")


_DATE_ONLY_RE_NORM = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def coerce_iso_minute(value: Any, timezone_name: str) -> Optional[str]:
  if not isinstance(value, str):
    return None
  raw = value.strip()
  if not raw:
    return None
  # Date-only string ("2026-02-14") → return None so caller treats it
  # as a missing time slot.  Do NOT silently upgrade to T00:00.
  if _DATE_ONLY_RE_NORM.match(raw):
    return None
  # Strip spaces around colons (LLM sometimes outputs "13: 00: 00+09: 00")
  raw = re.sub(r'\s*:\s*', ':', raw)

  parsed: Optional[datetime] = None
  try:
    parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M")
  except Exception:
    parsed = None

  if parsed is None:
    candidate = raw.replace("Z", "+00:00")
    try:
      parsed = datetime.fromisoformat(candidate)
    except Exception:
      parsed = None

  if parsed is None:
    return None

  tz = ZoneInfo(timezone_name)
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=tz)
  else:
    parsed = parsed.astimezone(tz)
  return parsed.strftime("%Y-%m-%dT%H:%M")


def coerce_rfc3339(value: Any, timezone_name: str) -> Optional[str]:
  if not isinstance(value, str):
    return None
  raw = value.strip()
  if not raw:
    return None
  # Strip spaces around colons
  raw = re.sub(r'\s*:\s*', ':', raw)

  parsed: Optional[datetime] = None
  candidate = raw.replace("Z", "+00:00")
  try:
    parsed = datetime.fromisoformat(candidate)
  except Exception:
    parsed = None

  if parsed is None:
    try:
      parsed = datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except Exception:
      parsed = None

  if parsed is None:
    return None

  tz = ZoneInfo(timezone_name)
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=tz)
  else:
    parsed = parsed.astimezone(tz)
  return parsed.isoformat()


def try_parse_date(value: Any) -> Optional[datetime.date]:
  if not isinstance(value, str):
    return None
  cleaned = value.strip()
  if not cleaned:
    return None
  # Strip time portion if present (e.g. "2026-02-01T00:00:00Z" → "2026-02-01")
  if "T" in cleaned:
    cleaned = cleaned.split("T")[0]
  try:
    return datetime.strptime(cleaned, "%Y-%m-%d").date()
  except Exception:
    return None
