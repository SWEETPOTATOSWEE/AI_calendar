from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any, Tuple
import os
import json
import re
import time
import pathlib
import urllib.parse

import requests
from openai import OpenAI
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

app = FastAPI()

import os
print("OPENAI_API_KEY:", bool(os.getenv("OPENAI_API_KEY")))
print("ENABLE_GCAL:", os.getenv("ENABLE_GCAL"))
print("GOOGLE_CLIENT_ID:", bool(os.getenv("GOOGLE_CLIENT_ID")))
print("GOOGLE_REDIRECT_URI:", os.getenv("GOOGLE_REDIRECT_URI"))
print("PWD:", __import__("os").getcwd())

# -------------------------
# OpenAI Client
# -------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client: Optional[OpenAI] = OpenAI(
    api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

SEOUL = ZoneInfo("Asia/Seoul")
LLM_DEBUG = os.getenv("LLM_DEBUG", "0") == "1"

ISO_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ISO_DATETIME_24_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T24:00$")


def _log_debug(message: str) -> None:
  if LLM_DEBUG:
    print(message, flush=True)


def _now_iso_minute() -> str:
  return datetime.now(SEOUL).strftime("%Y-%m-%dT%H:%M")

# -------------------------
# Google Calendar 설정
# -------------------------
ENABLE_GCAL = os.getenv("ENABLE_GCAL", "0") == "1"
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI")
GOOGLE_TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "google_token.json")
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
GCAL_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

ADMIN_COOKIE_NAME = "admin"
ADMIN_COOKIE_VALUE = "1"

BASE_DIR = pathlib.Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
EVENTS_DATA_FILE = pathlib.Path(
    os.getenv("EVENTS_DATA_FILE", str(BASE_DIR / "events_data.json")))


def _load_frontend_html(filename: str) -> str:
  path = FRONTEND_DIR / filename
  try:
    return path.read_text(encoding="utf-8")
  except FileNotFoundError as exc:
    raise RuntimeError(f"Front-end file not found: {path}") from exc


START_HTML = _load_frontend_html("start.html")
CALENDAR_HTML_TEMPLATE = _load_frontend_html("calendar.html")

# -------------------------
# 데이터 모델
# -------------------------
class Event(BaseModel):
  id: int
  title: str
  start: str  # "YYYY-MM-DDTHH:MM"
  end: Optional[str] = None
  location: Optional[str] = None
  recur: Optional[str] = None
  google_event_id: Optional[str] = None
  all_day: bool = False
  created_at: Optional[str] = None


class EventCreate(BaseModel):
  title: str
  start: str
  end: Optional[str] = None
  location: Optional[str] = None
  recur: Optional[str] = None
  google_event_id: Optional[str] = None
  all_day: Optional[bool] = None
  created_at: Optional[str] = None


class EventUpdate(BaseModel):
  title: Optional[str] = None
  start: Optional[str] = None
  end: Optional[str] = None
  location: Optional[str] = None
  all_day: Optional[bool] = None


class NaturalText(BaseModel):
  text: str
  images: Optional[List[str]] = None


class NaturalTextWithScope(BaseModel):
  text: str
  start_date: Optional[str] = None
  end_date: Optional[str] = None
  images: Optional[List[str]] = None


class DeleteResult(BaseModel):
  ok: bool
  deleted_ids: List[int]
  count: int


class ApplyItems(BaseModel):
  items: List[Dict[str, Any]]


class IdsPayload(BaseModel):
  ids: List[int]


# 메모리 저장
events: List[Event] = []
next_id: int = 1
UNDO_RETENTION_DAYS = 14
GOOGLE_RECENT_DAYS = 14
MAX_SCOPE_DAYS = 365
MAX_IMAGE_ATTACHMENTS = 5
MAX_IMAGE_DATA_URL_CHARS = 4_500_000  # 약 3.4MB base64
IMAGE_TOO_LARGE_MESSAGE = "첨부한 이미지가 너무 큽니다. 이미지는 약 3MB 이하로 축소해 주세요."

USD_TO_KRW = 1450.0
MODEL_PRICING = {
    "gpt-5-nano": {
        "input_per_m": 0.05,
        "cached_input_per_m": 0.01,
        "output_per_m": 0.4,
    },
    "gpt-5-mini": {
        "input_per_m": 0.25,
        "cached_input_per_m": 0.03,
        "output_per_m": 2.0,
    },
}


def _save_events_to_disk() -> None:
  try:
    payload = [e.dict() for e in events]
    EVENTS_DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8")
  except Exception as exc:
    _log_debug(f"[EVENT STORE] save failed: {exc}")


def _load_events_from_disk() -> None:
  global events, next_id
  if not EVENTS_DATA_FILE.exists():
    events.clear()
    next_id = 1
    return
  try:
    data = json.loads(EVENTS_DATA_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, list):
      events.clear()
      next_id = 1
      return

    loaded: List[Event] = []
    max_id = 0
    for item in data:
      if not isinstance(item, dict):
        continue
      if not item.get("created_at"):
        item["created_at"] = _now_iso_minute()
      try:
        ev = Event(**item)
      except Exception:
        continue
      loaded.append(ev)
      if ev.id > max_id:
        max_id = ev.id
    events[:] = loaded
    next_id = max_id + 1 if max_id else 1
  except Exception as exc:
    events.clear()
    next_id = 1
    _log_debug(f"[EVENT STORE] load failed: {exc}")


_load_events_from_disk()


def _save_events_to_disk() -> None:
  try:
    payload = [e.dict() for e in events]
    EVENTS_DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                encoding="utf-8")
  except Exception as exc:
    _log_debug(f"[EVENT STORE] save failed: {exc}")


def _load_events_from_disk() -> None:
  global events, next_id
  if not EVENTS_DATA_FILE.exists():
    events.clear()
    next_id = 1
    return
  try:
    data = json.loads(EVENTS_DATA_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, list):
      events.clear()
      next_id = 1
      return

    loaded: List[Event] = []
    max_id = 0
    for item in data:
      if not isinstance(item, dict):
        continue
      if not item.get("created_at"):
        item["created_at"] = _now_iso_minute()
      try:
        ev = Event(**item)
      except Exception:
        continue
      loaded.append(ev)
      if ev.id > max_id:
        max_id = ev.id
    events[:] = loaded
    next_id = max_id + 1 if max_id else 1
  except Exception as exc:
    events.clear()
    next_id = 1
    _log_debug(f"[EVENT STORE] load failed: {exc}")


_load_events_from_disk()


# -------------------------
# 공통 유틸
# -------------------------
def get_client() -> OpenAI:
  if client is None:
    raise RuntimeError("OPENAI_API_KEY is not set")
  return client


def normalize_text(text: str) -> str:
  t = (text or "").strip()
  t = re.sub(r"\s+", " ", t)
  return t


def _now_iso_minute() -> str:
  return datetime.now(SEOUL).strftime("%Y-%m-%dT%H:%M")


def _parse_created_at(dt_str: Optional[str]) -> datetime:
  if isinstance(dt_str, str):
    try:
      return datetime.strptime(dt_str.strip(), "%Y-%m-%dT%H:%M").replace(
          tzinfo=SEOUL)
    except Exception:
      pass
  return datetime.now(SEOUL)


def _split_iso_date_time(value: Optional[str]) -> Tuple[Optional[date], Optional[str]]:
  if not isinstance(value, str):
    return (None, None)
  raw = value.strip()
  if len(raw) < 10:
    return (None, None)
  date_part = raw[:10]
  try:
    dt = datetime.strptime(date_part, "%Y-%m-%d").date()
  except Exception:
    return (None, None)
  time_part: Optional[str] = None
  if len(raw) >= 16:
    time_part = raw[11:16]
  return (dt, time_part)


def _normalize_end_datetime(raw_end: Any) -> Optional[str]:
  if not isinstance(raw_end, str):
    return None
  candidate = raw_end.strip()
  if not candidate:
    return None
  if ISO_DATETIME_RE.match(candidate):
    return candidate
  if ISO_DATE_RE.match(candidate):
    return candidate + "T23:59"
  if ISO_DATETIME_24_RE.match(candidate):
    base = candidate[:10]
    try:
      base_date = datetime.strptime(base, "%Y-%m-%d").date()
    except Exception:
      return None
    next_day = base_date + timedelta(days=1)
    return next_day.strftime("%Y-%m-%dT00:00")
  return None


def is_all_day_span(start_iso: Optional[str],
                    end_iso: Optional[str]) -> bool:
  start_date, start_time = _split_iso_date_time(start_iso)
  if not start_date:
    return False
  if start_time not in (None, "00:00"):
    return False

  if not end_iso:
    return True

  end_date, end_time = _split_iso_date_time(end_iso)
  if not end_date:
    return True

  if end_date < start_date:
    return False

  if end_time in (None, "23:59"):
    return True

  if end_time == "00:00":
    return end_date >= start_date

  return False


def _validate_image_payload(images: Optional[List[str]]) -> List[str]:
  if not images:
    return []

  allowed_prefixes = (
      "data:image/png;base64,",
      "data:image/jpeg;base64,",
      "data:image/webp;base64,",
  )
  cleaned: List[str] = []
  for raw in images:
    if not isinstance(raw, str):
      continue
    data = raw.strip()
    if not data:
      continue
    if not any(data.startswith(prefix) for prefix in allowed_prefixes):
      raise HTTPException(status_code=400,
                          detail="이미지는 data:image/...;base64 형식이어야 합니다.")
    if len(data) > MAX_IMAGE_DATA_URL_CHARS:
      raise HTTPException(status_code=400, detail=IMAGE_TOO_LARGE_MESSAGE)
    cleaned.append(data)
    if len(cleaned) >= MAX_IMAGE_ATTACHMENTS:
      break
  return cleaned


def _compute_all_day_bounds(start_iso: str,
                            end_iso: Optional[str]) -> Tuple[date, date]:
  start_date, _ = _split_iso_date_time(start_iso)
  if not start_date:
    start_date = datetime.now(SEOUL).date()

  end_exclusive: date
  if end_iso:
    end_date, end_time = _split_iso_date_time(end_iso)
    if not end_date:
      end_exclusive = start_date + timedelta(days=1)
    else:
      if end_time == "00:00":
        if end_date <= start_date:
          end_exclusive = start_date + timedelta(days=1)
        else:
          end_exclusive = end_date + timedelta(days=1)
      else:
        end_exclusive = end_date + timedelta(days=1)
  else:
    end_exclusive = start_date + timedelta(days=1)

  if end_exclusive <= start_date:
    end_exclusive = start_date + timedelta(days=1)

  return (start_date, end_exclusive)


def _normalize_single_event_times(
    start_raw: Any,
    end_raw: Any,
) -> Tuple[Optional[str], Optional[str], bool]:
  """
    start_raw/end_raw: raw strings from LLM or client.
    Returns (start_iso, end_iso, all_day_flag).
    - Accepts date-only strings and upgrades them to 00:00 / 23:59.
    - Handles end at 24:00 by rolling to next day 00:00.
  """
  start_iso: Optional[str] = None
  end_iso: Optional[str] = None

  if isinstance(start_raw, str):
    s = start_raw.strip()
    if ISO_DATETIME_RE.match(s):
      start_iso = s
    elif ISO_DATE_RE.match(s):
      start_iso = s + "T00:00"

  if start_iso is None:
    return (None, None, False)

  end_iso = _normalize_end_datetime(end_raw)
  all_day_flag = is_all_day_span(start_iso, end_iso)
  return (start_iso, end_iso, all_day_flag)


def _coerce_patch_start(value: Any) -> Optional[str]:
  if value is None:
    return None
  if not isinstance(value, str):
    raise HTTPException(status_code=400, detail="시작 시각 형식이 잘못되었습니다.")
  candidate = value.strip()
  if not candidate:
    return None
  if not ISO_DATETIME_RE.match(candidate):
    raise HTTPException(status_code=400, detail="시작 시각 형식이 잘못되었습니다.")
  return candidate


def _coerce_patch_end(value: Any) -> Optional[str]:
  if value is None:
    return None
  if not isinstance(value, str):
    raise HTTPException(status_code=400, detail="종료 시각 형식이 잘못되었습니다.")
  candidate = value.strip()
  if not candidate:
    return None
  normalized = _normalize_end_datetime(candidate)
  if not normalized:
    raise HTTPException(status_code=400, detail="종료 시각 형식이 잘못되었습니다.")
  return normalized


def _clean_optional_str(value: Any) -> Optional[str]:
  if value is None:
    return None
  if not isinstance(value, str):
    return None
  trimmed = value.strip()
  return trimmed or None


def _parse_scope_dates(start_str: Optional[str],
                       end_str: Optional[str],
                       require: bool = False) -> Optional[Tuple[date, date]]:
  if not start_str or not end_str:
    if require:
      raise HTTPException(status_code=400,
                          detail="삭제 범위의 시작/종료 날짜를 모두 입력해주세요.")
    return None

  try:
    start_date = datetime.strptime(start_str.strip(), "%Y-%m-%d").date()
    end_date = datetime.strptime(end_str.strip(), "%Y-%m-%d").date()
  except Exception:
    raise HTTPException(status_code=400,
                        detail="삭제 범위 날짜 형식이 잘못되었습니다.")

  if end_date < start_date:
    raise HTTPException(status_code=400,
                        detail="삭제 범위 종료일이 시작일보다 빠릅니다.")

  if (end_date - start_date).days > MAX_SCOPE_DAYS:
    raise HTTPException(status_code=400,
                        detail=f"삭제 범위는 최대 {MAX_SCOPE_DAYS}일까지만 설정할 수 있습니다.")

  return (start_date, end_date)


def _event_within_scope(ev: Event, scope: Optional[Tuple[date, date]]) -> bool:
  if not scope:
    return True
  start_date, _ = _split_iso_date_time(ev.start)
  if not start_date:
    return False
  return scope[0] <= start_date <= scope[1]


def _normalize_google_timestamp(ts: Optional[str]) -> str:
  if not ts:
    return _now_iso_minute()
  try:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    dt = dt.astimezone(SEOUL)
    return dt.strftime("%Y-%m-%dT%H:%M")
  except Exception:
    return _now_iso_minute()


def store_event(
    title: str,
    start: str,
    end: Optional[str],
    location: Optional[str],
    recur: Optional[str] = None,
    google_event_id: Optional[str] = None,
    all_day: bool = False,
    created_at: Optional[str] = None,
) -> Event:
  global next_id, events
  created_str = created_at or _now_iso_minute()
  new_event = Event(
      id=next_id,
      title=title,
      start=start,
      end=end,
      location=location,
      recur=recur,
      google_event_id=google_event_id,
      all_day=bool(all_day),
      created_at=created_str,
  )
  next_id += 1
  events.append(new_event)
  _save_events_to_disk()
  return new_event


def is_admin(request: Request) -> bool:
  return request.cookies.get(ADMIN_COOKIE_NAME) == ADMIN_COOKIE_VALUE


def is_google_mode_active(request: Request, has_token: Optional[bool] = None) -> bool:
  token_present = load_gcal_token() is not None if has_token is None else has_token
  return (not is_admin(request)) and ENABLE_GCAL and token_present


# -------------------------
# LLM 프롬프트
# -------------------------
EVENTS_SYSTEM_PROMPT_TEMPLATE = """너는 한국어 일정 문장을 구조화하는 파서다. 반드시 JSON 한 개만 반환한다. 설명 금지.
기준 정보:
- 기준 날짜: {TODAY}
- 시간대: Asia/Seoul

출력 스키마:
{
  "items": [
    {
      "type": "single",
      "title": string,
      "start": "YYYY-MM-DDTHH:MM",
      "end": "YYYY-MM-DDTHH:MM" | null,
      "location": string | null
    },
    {
      "type": "recurring",
      "title": string,
      "start_date": "YYYY-MM-DD",
      "end_date": "YYYY-MM-DD",
      "weekdays": [0,1,2,3,4,5,6],
      "time": "HH:MM" | null,
      "duration_minutes": number | null,
      "location": string | null
    }
  ]
}

규칙:
- 여러 일정이면 single을 여러 개.
- 반복이 있으면 recurring.
- 단일+반복 혼합이면 둘 다 넣는다.
- title에는 시간/장소를 넣지 않는다.
- 상대 날짜는 기준 날짜로 계산
- weekdays는 0=월요일, 6=일요일
- 시간 정보가 없으면 recurring의 time과 duration_minutes는 null.
- 사용자의 요청이 없다면 과거 이벤트는 생성하지 않음
"""


def build_events_system_prompt() -> str:
  today = datetime.now(SEOUL).date().isoformat()
  return EVENTS_SYSTEM_PROMPT_TEMPLATE.replace("{TODAY}", today)


EVENTS_MULTIMODAL_PROMPT_TEMPLATE = """너는 한국어 일정 정보를 텍스트와 이미지에서 구조화하는 파서다. 반드시 JSON 한 개만 반환한다. 설명 금지.
입력 특징:
- 사용자가 항공권/티켓/메신저/시간표 등 캡처 이미지를 제공한다.
- 검은 박스로 가린 영역이나 알아볼 수 없는 부분은 추측하지 말고 무시한다.
- 텍스트 설명이 함께 있을 수 있으므로 반드시 모두 참고한다.

기준 정보:
- 기준 날짜: {TODAY}
- 시간대: Asia/Seoul

출력 스키마:
{
  "items": [
    {
      "type": "single",
      "title": string,
      "start": "YYYY-MM-DDTHH:MM",
      "end": "YYYY-MM-DDTHH:MM" | null,
      "location": string | null
    },
    {
      "type": "recurring",
      "title": string,
      "start_date": "YYYY-MM-DD",
      "end_date": "YYYY-MM-DD",
      "weekdays": [0,1,2,3,4,5,6],
      "time": "HH:MM" | null,
      "duration_minutes": number | null,
      "location": string | null
    }
  ]
}

규칙:
- 여러 일정이면 single을 여러 개.
- 반복이 있으면 recurring.
- 단일+반복 혼합이면 둘 다 넣는다.
- title에는 시간/장소를 넣지 않는다.
- 상대 날짜는 기준 날짜로 계산
- weekdays는 0=월요일, 6=일요일
- 이미지 또는 텍스트에 정보가 없으면 null 처리
- 가려진 구간이나 알아볼 수 없는 내용은 제외
- 사용자의 요청이 없다면 과거 이벤트는 생성하지 않음
"""


def build_events_multimodal_prompt() -> str:
  today = datetime.now(SEOUL).date().isoformat()
  return EVENTS_MULTIMODAL_PROMPT_TEMPLATE.replace("{TODAY}", today)


def _build_events_user_payload(text: str, has_images: bool) -> str:
  lines = []
  if text:
    lines.append(f"문장: {text}")
  else:
    lines.append("문장: (제공되지 않음)")
  if has_images:
    lines.append("첨부 이미지를 참고해서 일정 정보를 추출해줘.")
  return "\n".join(lines)


def _get_detail_value(detail: Any, key: str) -> Optional[int]:
  if detail is None:
    return None
  if isinstance(detail, dict):
    value = detail.get(key)
  else:
    value = getattr(detail, key, None)
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def _estimate_llm_cost(model_name: str, prompt_tokens: Optional[int],
                       cached_prompt_tokens: Optional[int],
                       completion_tokens: Optional[int]) -> Optional[Tuple[float,
                                                                           float]]:
  pricing = MODEL_PRICING.get(model_name)
  if not pricing:
    return None

  prompt = max(int(prompt_tokens or 0), 0)
  cached = max(int(cached_prompt_tokens or 0), 0)
  cached = min(cached, prompt)
  uncached = max(prompt - cached, 0)
  completion = max(int(completion_tokens or 0), 0)

  usd = ((uncached / 1_000_000) * pricing["input_per_m"] +
         (cached / 1_000_000) * pricing["cached_input_per_m"] +
         (completion / 1_000_000) * pricing["output_per_m"])
  krw = usd * USD_TO_KRW
  return usd, krw


def _invoke_event_parser(kind: str, text: str,
                         images: List[str]) -> Dict[str, Any]:
  payload = _build_events_user_payload(text, bool(images))
  if images:
    return _chat_multimodal_json(kind, build_events_multimodal_prompt(),
                                 payload, images)
  return _chat_json(kind, build_events_system_prompt(), payload)


def build_delete_system_prompt() -> str:
  return ("역할: '기존 일정 목록'과 '삭제 요청 문장'을 보고 삭제할 일정 id 목록만 고른다.\n"
          "항상 아래 형식의 JSON 한 개만 출력해라. 설명·코드블록·마크다운은 금지.\n\n"
          "{\n"
          '  \"ids\": [number]\n'
          "}\n\n"
          "규칙:\n"
          "- 문장과 명확히 매칭되는 일정의 id만 넣는다.\n"
          "- '전부', '모든 일정'이면 목록의 모든 id.\n"
          "- 애매하면 빈 배열[].\n")


# -------------------------
# LLM 호출 & 디버그
# -------------------------
def _debug_print(
    kind: str,
    input_text: str,
    system_prompt: str,
    raw_content: str,
    latency_ms: Optional[float] = None,
    usage: Optional[Dict[str, Any]] = None,
    model_name: str = "",
) -> None:
  if not LLM_DEBUG or kind == "preview":
    return

  head = system_prompt[:220].replace("\n", "\\n")
  _log_debug(f"[LLM DEBUG] kind: {kind}")
  _log_debug(f"[LLM DEBUG] input_text: {input_text}")
  _log_debug(f"[LLM DEBUG] system_prompt(head): {head}")
  _log_debug(f"[LLM DEBUG] raw_content: {raw_content}")

  if latency_ms is not None:
    _log_debug(f"[LLM DEBUG] latency_ms: {latency_ms:.1f} ms")

  if usage is not None:
    p = usage.get("prompt")
    c = usage.get("completion")
    t = usage.get("total")
    cached_prompt = usage.get("cached_prompt")
    _log_debug(
        f"[LLM DEBUG] usage: prompt={p}, completion={c}, total={t}, cached_prompt={cached_prompt}"
    )
    cost = _estimate_llm_cost(model_name, p, cached_prompt, c)
    if cost:
      usd, krw = cost
      _log_debug(
          f"[LLM DEBUG] cost: ${usd:.6f} ≈ ₩{krw:,.0f} (model={model_name})")


def _safe_json_loads(raw: str) -> Dict[str, Any]:
  if not raw or not isinstance(raw, str):
    return {}
  raw = raw.strip()

  try:
    obj = json.loads(raw)
    return obj if isinstance(obj, dict) else {}
  except Exception:
    pass

  start = raw.find("{")
  end = raw.rfind("}")
  if start != -1 and end != -1 and end > start:
    try:
      obj = json.loads(raw[start:end + 1])
      return obj if isinstance(obj, dict) else {}
    except Exception:
      return {}

  return {}


def _current_reference_line() -> str:
  now = datetime.now(SEOUL)
  return f"기준 시각: {now.strftime('%Y-%m-%d')} (Asia/Seoul)\n"


def _chat_json(kind: str, system_prompt: str,
               user_text: str) -> Dict[str, Any]:
  """
    gpt-5-nano를 Chat Completions로 호출하는 버전.
    - max_tokens 대신 max_completion_tokens 사용
    - temperature / reasoning 등 gpt-5-nano에서 에러나는 옵션은 보내지 않는다.
    """
  c = get_client()

  input_text = _current_reference_line() + user_text

  started = time.perf_counter()
  try:
    completion = c.chat.completions.create(
        model="gpt-5-nano",
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": input_text
            },
        ],
        max_completion_tokens=5000,
    )

    latency_ms = (time.perf_counter() - started) * 1000.0

    choice = completion.choices[0]
    content = choice.message.content
    raw_content = content if isinstance(content, str) else ""

    usage_dict: Optional[Dict[str, Any]] = None
    usage_obj = getattr(completion, "usage", None)
    if usage_obj is not None:
      prompt_details = getattr(usage_obj, "prompt_tokens_details", None)
      cached_prompt = (_get_detail_value(prompt_details, "cached_tokens")
                       or _get_detail_value(prompt_details,
                                            "cached_prompt_tokens"))
      usage_dict = {
          "prompt": getattr(usage_obj, "prompt_tokens", None),
          "completion": getattr(usage_obj, "completion_tokens", None),
          "total": getattr(usage_obj, "total_tokens", None),
          "cached_prompt": cached_prompt,
      }

    _debug_print(kind, user_text, system_prompt, raw_content, latency_ms,
                 usage_dict, "gpt-5-nano")
    return _safe_json_loads(raw_content)

  except Exception as e:
    _log_debug(f"[LLM DEBUG] exception: {repr(e)}")
    raise


def _chat_multimodal_json(kind: str,
                          system_prompt: str,
                          user_text: str,
                          images: List[str]) -> Dict[str, Any]:
  c = get_client()

  user_parts: List[Dict[str, Any]] = [{
      "type": "text",
      "text": _current_reference_line() + user_text
  }]

  for img in images:
    user_parts.append({
        "type": "image_url",
        "image_url": {
            "url": img
        }
    })

  started = time.perf_counter()
  try:
    completion = c.chat.completions.create(
        model="gpt-5-mini",
        messages=[
            {
                "role": "system",
                "content": [{
                    "type": "text",
                    "text": system_prompt
                }]
            },
            {
                "role": "user",
                "content": user_parts
            },
        ],
        max_completion_tokens=10000,
    )

    latency_ms = (time.perf_counter() - started) * 1000.0

    choice = completion.choices[0]
    content = choice.message.content
    raw_content = content if isinstance(content, str) else ""

    usage_dict: Optional[Dict[str, Any]] = None
    usage_obj = getattr(completion, "usage", None)
    if usage_obj is not None:
      prompt_details = getattr(usage_obj, "prompt_tokens_details", None)
      cached_prompt = (_get_detail_value(prompt_details, "cached_tokens")
                       or _get_detail_value(prompt_details,
                                            "cached_prompt_tokens"))
      usage_dict = {
          "prompt": getattr(usage_obj, "prompt_tokens", None),
          "completion": getattr(usage_obj, "completion_tokens", None),
          "total": getattr(usage_obj, "total_tokens", None),
          "cached_prompt": cached_prompt,
      }

    _debug_print(kind, user_text, system_prompt, raw_content, latency_ms,
                 usage_dict, "gpt-5-mini")
    return _safe_json_loads(raw_content)

  except Exception as e:
    _log_debug(f"[LLM DEBUG] exception: {repr(e)}")
    raise


# -------------------------
# recurring 전개 & RRULE
# -------------------------
def _expand_recurring_item(item: Dict[str, Any]) -> List[Dict[str, Any]]:
  """
    recurring item -> 여러 개의 단일 일정 dict로 전개
    """
  title = (item.get("title") or "").strip()
  start_date_str = item.get("start_date")
  end_date_str = item.get("end_date")
  weekdays_raw = item.get("weekdays")

  if not title or not isinstance(start_date_str, str) or not isinstance(
      end_date_str, str):
    return []

  try:
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
  except Exception:
    return []

  if end_date < start_date:
    return []

  # 최대 1년까지만 전개
  max_span_days = 365
  span = (end_date - start_date).days
  if span > max_span_days:
    end_date = start_date + timedelta(days=max_span_days)

  if not isinstance(weekdays_raw, list) or not weekdays_raw:
    return []

  weekday_set = set()
  for w in weekdays_raw:
    try:
      iw = int(w)
    except Exception:
      continue
    if 0 <= iw <= 6:
      weekday_set.add(iw)

  if not weekday_set:
    return []

  time_str = item.get("time")
  duration_minutes = item.get("duration_minutes")
  location = item.get("location")

  hh, mm = 0, 0
  time_valid = False
  if isinstance(time_str, str) and re.match(r"^\d{2}:\d{2}$",
                                            time_str.strip()):
    hh, mm = [int(x) for x in time_str.strip().split(":")]
    time_valid = 0 <= hh <= 23 and 0 <= mm <= 59

  dur: Optional[int] = None
  if duration_minutes is not None:
    try:
      dur = int(duration_minutes)
      if dur <= 0:
        dur = None
    except Exception:
      dur = None

  location_str = (location or "").strip() or None

  results: List[Dict[str, Any]] = []

  cur = start_date
  while cur <= end_date:
    if cur.weekday() in weekday_set:
      if time_valid:
        start_dt = datetime(cur.year, cur.month, cur.day, hh, mm, tzinfo=SEOUL)
        start_str = start_dt.strftime("%Y-%m-%dT%H:%M")

        end_str: Optional[str] = None
        if dur is not None:
          end_dt = start_dt + timedelta(minutes=dur)
          end_str = end_dt.strftime("%Y-%m-%dT%H:%M")
      else:
        start_dt = datetime(cur.year, cur.month, cur.day, 0, 0, tzinfo=SEOUL)
        end_dt = datetime(cur.year, cur.month, cur.day, 23, 59, tzinfo=SEOUL)
        start_str = start_dt.strftime("%Y-%m-%dT%H:%M")
        end_str = end_dt.strftime("%Y-%m-%dT%H:%M")

      results.append({
          "title": title,
          "start": start_str,
          "end": end_str,
          "location": location_str,
          "recur": "recurring",
          "all_day": not time_valid
      })
    cur += timedelta(days=1)

  return results


def recurring_to_rrule(item: Dict[str, Any]) -> Optional[str]:
  """
    recurring item -> RRULE 문자열 (FREQ=WEEKLY)
    """
  weekdays_raw = item.get("weekdays")
  if not isinstance(weekdays_raw, list) or not weekdays_raw:
    return None

  weekday_map = {0: "MO", 1: "TU", 2: "WE", 3: "TH", 4: "FR", 5: "SA", 6: "SU"}

  byday_list: List[str] = []
  for w in weekdays_raw:
    try:
      iw = int(w)
    except Exception:
      continue
    if iw in weekday_map:
      byday_list.append(weekday_map[iw])

  if not byday_list:
    return None

  end_date_str = item.get("end_date")
  if not isinstance(end_date_str, str):
    return None

  try:
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
  except Exception:
    return None

  until = end_date.strftime("%Y%m%dT235959Z")
  byday = ",".join(byday_list)
  return f"FREQ=WEEKLY;BYDAY={byday};UNTIL={until}"


# -------------------------
# Google Calendar 유틸
# -------------------------
def is_gcal_configured() -> bool:
  return bool(ENABLE_GCAL and GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
              and GOOGLE_REDIRECT_URI)


def load_gcal_token() -> Optional[Dict[str, Any]]:
  path = pathlib.Path(GOOGLE_TOKEN_FILE)
  if not path.exists():
    return None
  try:
    with path.open("r", encoding="utf-8") as f:
      return json.load(f)
  except Exception:
    return None


def clear_gcal_token() -> None:
  try:
    path = pathlib.Path(GOOGLE_TOKEN_FILE)
    if path.exists():
      path.unlink()
  except Exception:
    pass


def save_gcal_token(data: Dict[str, Any]) -> None:
  path = pathlib.Path(GOOGLE_TOKEN_FILE)
  path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                  encoding="utf-8")


def get_gcal_service():
  if not is_gcal_configured():
    raise RuntimeError("Google Calendar is not configured.")

  token_data = load_gcal_token()
  if not token_data:
    raise RuntimeError(
        "Google OAuth token not found. Run /auth/google/login first.")

  creds = Credentials.from_authorized_user_info(token_data, GCAL_SCOPES)

  if creds.expired and creds.refresh_token:
    creds.refresh(GoogleRequest())
    new_data = json.loads(creds.to_json())
    save_gcal_token(new_data)

  service = build("calendar", "v3", credentials=creds)
  return service


def gcal_create_single_event(title: str,
                             start_iso: str,
                             end_iso: Optional[str],
                             location: Optional[str],
                             all_day: Optional[bool] = None) -> Optional[str]:
  if not is_gcal_configured():
    return None

  try:
    service = get_gcal_service()
  except Exception as e:
    _log_debug(f"[GCAL] get service error: {e}")
    return None

  try:
    use_all_day = bool(all_day)
    if all_day is None:
      use_all_day = is_all_day_span(start_iso, end_iso)

    event_body: Dict[str, Any] = {"summary": title}

    if use_all_day:
      start_date_obj, end_exclusive = _compute_all_day_bounds(start_iso, end_iso)
      event_body["start"] = {"date": start_date_obj.strftime("%Y-%m-%d")}
      event_body["end"] = {"date": end_exclusive.strftime("%Y-%m-%d")}
    else:
      start_dt = datetime.strptime(start_iso,
                                   "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
      if end_iso:
        end_dt = datetime.strptime(end_iso,
                                   "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
      else:
        end_dt = start_dt + timedelta(hours=1)

      event_body["start"] = {
          "dateTime": start_dt.isoformat(),
          "timeZone": "Asia/Seoul"
      }
      event_body["end"] = {
          "dateTime": end_dt.isoformat(),
          "timeZone": "Asia/Seoul"
      }

    if location:
      event_body["location"] = location

    created = service.events().insert(calendarId=GOOGLE_CALENDAR_ID,
                                      body=event_body).execute()
    return created.get("id")
  except Exception as e:
    _log_debug(f"[GCAL] create single event error: {e}")
    return None


def _build_gcal_event_body(title: Optional[str],
                           start_iso: Optional[str],
                           end_iso: Optional[str],
                           location: Optional[str],
                           all_day: Optional[bool]) -> Dict[str, Any]:
  if not isinstance(start_iso, str) or not ISO_DATETIME_RE.match(start_iso):
    raise ValueError("Invalid start time for Google Calendar update.")

  use_all_day = bool(all_day)
  if all_day is None:
    use_all_day = is_all_day_span(start_iso, end_iso)

  body: Dict[str, Any] = {}
  if title is not None:
    body["summary"] = title
  if location is not None:
    body["location"] = location

  if use_all_day:
    start_date, end_exclusive = _compute_all_day_bounds(start_iso, end_iso)
    body["start"] = {"date": start_date.strftime("%Y-%m-%d")}
    body["end"] = {"date": end_exclusive.strftime("%Y-%m-%d")}
  else:
    start_dt = datetime.strptime(start_iso,
                                 "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
    if end_iso:
      end_dt = datetime.strptime(end_iso,
                                 "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
    else:
      end_dt = start_dt + timedelta(hours=1)
    body["start"] = {
        "dateTime": start_dt.isoformat(),
        "timeZone": "Asia/Seoul"
    }
    body["end"] = {
        "dateTime": end_dt.isoformat(),
        "timeZone": "Asia/Seoul"
    }
  return body


def gcal_update_event(event_id: str,
                      title: Optional[str],
                      start_iso: Optional[str],
                      end_iso: Optional[str],
                      location: Optional[str],
                      all_day: Optional[bool]) -> None:
  if not event_id:
    raise ValueError("event_id is empty")
  if not is_gcal_configured():
    raise RuntimeError("Google Calendar is not configured.")

  service = get_gcal_service()
  body = _build_gcal_event_body(title, start_iso, end_iso, location, all_day)
  service.events().patch(calendarId=GOOGLE_CALENDAR_ID,
                         eventId=event_id,
                         body=body).execute()


def gcal_create_recurring_event(item: Dict[str, Any]) -> Optional[str]:
  if not is_gcal_configured():
    return None

  rrule_core = recurring_to_rrule(item)
  if not rrule_core:
    return None

  title = (item.get("title") or "").strip()
  if not title:
    return None

  start_date_str = item.get("start_date")
  time_str = item.get("time")
  duration_minutes = item.get("duration_minutes")
  location = item.get("location")

  if not isinstance(start_date_str, str):
    return None

  try:
    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
  except Exception:
    return None

  all_day = not (isinstance(time_str, str)
                 and re.match(r"^\d{2}:\d{2}$", time_str.strip()))

  try:
    service = get_gcal_service()
  except Exception as e:
    _log_debug(f"[GCAL] get service error: {e}")
    return None

  try:
    event_body: Dict[str, Any] = {
        "summary": title,
        "recurrence": [f"RRULE:{rrule_core}"]
    }

    if all_day:
      start_date_str2 = start_date.strftime("%Y-%m-%d")
      end_date_excl = (start_date + timedelta(days=1)).strftime("%Y-%m-%d")
      event_body["start"] = {"date": start_date_str2}
      event_body["end"] = {"date": end_date_excl}
    else:
      hh, mm = [int(x) for x in time_str.strip().split(":")]
      start_dt = datetime(start_date.year,
                          start_date.month,
                          start_date.day,
                          hh,
                          mm,
                          tzinfo=SEOUL)
      if duration_minutes and isinstance(
          duration_minutes, (int, float)) and duration_minutes > 0:
        end_dt = start_dt + timedelta(minutes=int(duration_minutes))
      else:
        end_dt = start_dt + timedelta(hours=1)

      event_body["start"] = {
          "dateTime": start_dt.isoformat(),
          "timeZone": "Asia/Seoul"
      }
      event_body["end"] = {
          "dateTime": end_dt.isoformat(),
          "timeZone": "Asia/Seoul"
      }

    if location:
      event_body["location"] = (location or "").strip() or None

    created = service.events().insert(calendarId=GOOGLE_CALENDAR_ID,
                                      body=event_body).execute()
    return created.get("id")
  except Exception as e:
    _log_debug(f"[GCAL] create recurring event error: {e}")
    return None


def gcal_delete_event(event_id: str) -> None:
  if not event_id:
    raise ValueError("event_id is empty")
  if not is_gcal_configured():
    raise RuntimeError("Google Calendar is not configured.")
  service = get_gcal_service()
  service.events().delete(calendarId=GOOGLE_CALENDAR_ID, eventId=event_id).execute()


def _convert_gcal_time(obj: Dict[str, Any],
                       is_end: bool,
                       start_iso: Optional[str]) -> Tuple[Optional[str], bool]:
  if not isinstance(obj, dict):
    return (None, False)

  dt_value = obj.get("dateTime")
  if isinstance(dt_value, str):
    try:
      dt = datetime.fromisoformat(dt_value.replace("Z", "+00:00"))
      dt = dt.astimezone(SEOUL)
      return (dt.strftime("%Y-%m-%dT%H:%M"), False)
    except Exception:
      return (None, False)

  date_value = obj.get("date")
  if isinstance(date_value, str):
    try:
      date_obj = datetime.strptime(date_value, "%Y-%m-%d").date()
    except Exception:
      return (None, True)

    if not is_end:
      return (date_obj.strftime("%Y-%m-%dT00:00"), True)

    inclusive = date_obj - timedelta(days=1)
    if start_iso:
      start_date, _ = _split_iso_date_time(start_iso)
      if start_date and inclusive < start_date:
        inclusive = start_date
    return (inclusive.strftime("%Y-%m-%dT23:59"), True)

  return (None, False)


def fetch_google_events_between(range_start: date,
                                range_end: date) -> List[Dict[str, Any]]:
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar 연동이 설정되지 않았습니다.")

  try:
    service = get_gcal_service()
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar 인증에 실패했습니다: {exc}") from exc

  time_min = datetime(range_start.year,
                      range_start.month,
                      range_start.day,
                      0,
                      0,
                      tzinfo=SEOUL)
  time_max = datetime(range_end.year,
                      range_end.month,
                      range_end.day,
                      0,
                      0,
                      tzinfo=SEOUL) + timedelta(days=1)

  events_data: List[Dict[str, Any]] = []
  page_token: Optional[str] = None

  while True:
    request = service.events().list(calendarId=GOOGLE_CALENDAR_ID,
                                    timeMin=time_min.isoformat(),
                                    timeMax=time_max.isoformat(),
                                    singleEvents=True,
                                    orderBy="startTime",
                                    pageToken=page_token)
    response = request.execute()
    items = response.get("items", [])

    for raw in items:
      if not isinstance(raw, dict):
        continue
      start_raw = raw.get("start") or {}
      start_iso, all_day_flag = _convert_gcal_time(start_raw, False, None)
      if not start_iso:
        continue
      end_raw = raw.get("end") or {}
      end_iso, _ = _convert_gcal_time(end_raw, True, start_iso)

      events_data.append({
          "id": raw.get("id"),
          "title": raw.get("summary") or "(제목 없음)",
          "start": start_iso,
          "end": end_iso,
          "location": raw.get("location"),
          "all_day": all_day_flag,
          "status": raw.get("status"),
          "html_link": raw.get("htmlLink"),
          "organizer": (raw.get("organizer") or {}).get("email"),
          "created": raw.get("created"),
          "updated": raw.get("updated"),
      })

    page_token = response.get("nextPageToken")
    if not page_token:
      break

  return events_data


def fetch_recent_google_events(days: int = GOOGLE_RECENT_DAYS) -> List[Dict[str, Any]]:
  if days <= 0:
    days = GOOGLE_RECENT_DAYS

  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar 연동이 설정되지 않았습니다.")

  try:
    service = get_gcal_service()
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google Calendar 인증에 실패했습니다: {exc}") from exc

  now = datetime.now(SEOUL)
  time_min = now - timedelta(days=days)

  events_data: List[Dict[str, Any]] = []
  page_token: Optional[str] = None

  while True:
    request = service.events().list(calendarId=GOOGLE_CALENDAR_ID,
                                    updatedMin=time_min.astimezone(
                                        timezone.utc).isoformat().replace(
                                            "+00:00", "Z"),
                                    singleEvents=True,
                                    orderBy="updated",
                                    maxResults=100,
                                    pageToken=page_token)
    response = request.execute()
    items = response.get("items", [])

    for raw in items:
      if not isinstance(raw, dict):
        continue

      start_raw = raw.get("start") or {}
      start_iso, all_day_flag = _convert_gcal_time(start_raw, False, None)
      if not start_iso:
        continue
      end_raw = raw.get("end") or {}
      end_iso, _ = _convert_gcal_time(end_raw, True, start_iso)

      events_data.append({
          "id": raw.get("id"),
          "title": raw.get("summary") or "(제목 없음)",
          "start": start_iso,
          "end": end_iso,
          "location": raw.get("location"),
          "all_day": all_day_flag,
          "status": raw.get("status"),
          "html_link": raw.get("htmlLink"),
          "organizer": (raw.get("organizer") or {}).get("email"),
          "created": raw.get("created"),
          "updated": raw.get("updated"),
      })

    page_token = response.get("nextPageToken")
    if not page_token:
      break

  events_data.sort(key=lambda ev: ev.get("updated") or ev.get("created") or "",
                   reverse=True)
  return events_data


# -------------------------
# 자연어 → 일정 생성(기존)
# -------------------------
def create_events_from_natural_text_core(text: str,
                                         images: Optional[List[str]] = None) -> List[Event]:
  images = images or []
  t = normalize_text(text)
  if not t and not images:
    raise HTTPException(status_code=400, detail="문장이나 이미지를 입력해주세요.")

  if client is None:
    raise HTTPException(
        status_code=500,
        detail="LLM client is not configured (OPENAI_API_KEY 미설정)")

  data = _invoke_event_parser("preview", t, images)
  items = data.get("items")
  if not isinstance(items, list):
    raise HTTPException(status_code=502, detail="LLM 응답 형식 오류입니다.")

  if len(items) == 0:
    raise HTTPException(status_code=422,
                        detail="일정을 만들 수 없습니다. 날짜/시간/기간이 모호한 문장입니다.")

  flat_events: List[Dict[str, Any]] = []
  recurring_items: List[Dict[str, Any]] = []

  for item in items:
    if not isinstance(item, dict):
      continue

    typ = (item.get("type") or "").strip().lower()

    if typ == "single":
      title = (item.get("title") or "").strip()
      start = item.get("start")
      end = item.get("end")
      location = item.get("location")

      start_iso, end_iso, all_day = _normalize_single_event_times(
          start, end)

      if not title or not start_iso:
        continue

      loc_str = (location or "").strip() or None

      flat_events.append({
          "title": title,
          "start": start_iso,
          "end": end_iso,
          "location": loc_str,
          "recur": None,
          "source_type": "single",
          "all_day": all_day
      })

    elif typ == "recurring":
      expanded = _expand_recurring_item(item)
      if not expanded:
        continue

      if len(expanded) == 1:
        only = expanded[0]
        title = (only.get("title") or "").strip()
        start_single = only.get("start")
        if not title or not isinstance(start_single, str):
          continue
        flat_events.append({
            "title": title,
            "start": start_single,
            "end": only.get("end"),
            "location": only.get("location"),
            "recur": None,
            "source_type": "single",
            "all_day": bool(only.get("all_day"))
        })
        continue

      flat_events.extend({
          **ev, "source_type": "recurring"
      } for ev in expanded)
      recurring_items.append(item)

  if not flat_events:
    raise HTTPException(status_code=422,
                        detail="일정을 만들 수 없습니다. 날짜/시간/형식을 다시 한 번 명확히 적어주세요.")

  created: List[Event] = []

  for ev in flat_events:
    title = ev["title"]
    start = ev["start"]
    end = ev.get("end")
    location = ev.get("location")
    recur = ev.get("recur")
    all_day_flag = bool(ev.get("all_day"))
    google_event_id: Optional[str] = None

    if ev.get("source_type") == "single":
      google_event_id = gcal_create_single_event(title, start, end, location,
                                                 all_day_flag)

    created.append(
        store_event(title=title,
                    start=start,
                    end=end,
                    location=location,
                    recur=recur,
                    google_event_id=google_event_id,
                    all_day=all_day_flag))

  for rec_item in recurring_items:
    gcal_create_recurring_event(rec_item)

  return created


# -------------------------
# NLP Preview (추가 미리보기)
# -------------------------
def preview_events_from_natural_text_core(
    text: str,
    images: Optional[List[str]] = None) -> Dict[str, Any]:
  images = images or []
  t = normalize_text(text)
  if not t and not images:
    raise HTTPException(status_code=400, detail="문장이나 이미지를 입력해주세요.")

  if client is None:
    raise HTTPException(
        status_code=500,
        detail="LLM client is not configured (OPENAI_API_KEY 미설정)")

  data = _invoke_event_parser("parse", t, images)
  items = data.get("items")
  if not isinstance(items, list) or len(items) == 0:
    raise HTTPException(status_code=422, detail="미리보기를 만들 수 없습니다.")

  out_items: List[Dict[str, Any]] = []

  for item in items:
    if not isinstance(item, dict):
      continue

    typ = (item.get("type") or "").strip().lower()

    if typ == "single":
      title = (item.get("title") or "").strip()
      start = item.get("start")
      end = item.get("end")
      location = item.get("location")

      start_iso, end_iso, all_day = _normalize_single_event_times(
          start, end)
      if not title or not start_iso:
        continue

      out_items.append({
          "type": "single",
          "title": title,
          "start": start_iso,
          "end": end_iso,
          "location": (location or "").strip() or None,
          "all_day": all_day
      })

    elif typ == "recurring":
      title = (item.get("title") or "").strip()
      if not title:
        continue

      expanded = _expand_recurring_item(item)
      count = len(expanded)

      if count == 1:
        only = expanded[0]
        single_title = (only.get("title") or title).strip()
        start_single = only.get("start")
        if not single_title or not isinstance(start_single, str):
          continue
        out_items.append({
            "type": "single",
            "title": single_title,
            "start": start_single,
            "end": only.get("end"),
            "location": only.get("location"),
            "all_day": bool(only.get("all_day"))
        })
        continue

      samples = []
      for ev in expanded[:5]:
        samples.append({"start": ev.get("start"), "end": ev.get("end")})

      occurrences = []
      for idx, ev in enumerate(expanded):
        occurrences.append({
            "index": idx,
            "title": ev.get("title"),
            "start": ev.get("start"),
            "end": ev.get("end"),
            "location": ev.get("location"),
            "all_day": ev.get("all_day", False)
        })

      out_items.append({
          "type":
          "recurring",
          "title":
          title,
          "start_date":
          item.get("start_date"),
          "end_date":
          item.get("end_date"),
          "weekdays":
          item.get("weekdays"),
          "time":
          item.get("time"),
          "duration_minutes":
          item.get("duration_minutes"),
          "location": (item.get("location") or "").strip() or None,
          "count":
          count,
          "samples": [x.get("start") for x in samples if x.get("start")],
          "all_day":
          len(expanded) > 0 and bool(expanded[0].get("all_day")),
          "occurrences": occurrences,
      })

  if not out_items:
    raise HTTPException(status_code=422, detail="미리보기를 만들 수 없습니다.")

  return {"items": out_items}


# -------------------------
# 선택 적용: Add (모달에서 체크한 것만)
# -------------------------
def apply_add_items_core(items: List[Dict[str, Any]]) -> List[Event]:
  created: List[Event] = []

  for item in items:
    if not isinstance(item, dict):
      continue

    typ = (item.get("type") or "").strip().lower()

    if typ == "single":
      title = (item.get("title") or "").strip()
      start = (item.get("start") or "").strip()
      end = item.get("end")
      location = (item.get("location") or "").strip() or None
      all_day_flag = bool(item.get("all_day"))

      if not title or not ISO_DATETIME_RE.match(start):
        continue

      end_str = _normalize_end_datetime(end)
      if not all_day_flag:
        all_day_flag = is_all_day_span(start, end_str)

      google_event_id = gcal_create_single_event(title, start, end_str,
                                                 location, all_day_flag)

      created.append(
          store_event(
              title=title,
              start=start,
              end=end_str,
              location=location,
              recur=None,
              google_event_id=google_event_id,
              all_day=all_day_flag,
          ))

    elif typ == "recurring":
      expanded_all = _expand_recurring_item(item)
      if not expanded_all:
        continue

      selected_idx_raw = item.get("selected_occurrence_indexes")
      selected_idx: Optional[List[int]] = None
      if isinstance(selected_idx_raw, list):
        dedup: List[int] = []
        seen: set[int] = set()
        for val in selected_idx_raw:
          try:
            i = int(val)
          except Exception:
            continue
          if i < 0 or i >= len(expanded_all) or i in seen:
            continue
          seen.add(i)
          dedup.append(i)
        if dedup:
          selected_idx = dedup

      expanded = ([
          ev for idx, ev in enumerate(expanded_all) if selected_idx is None
          or idx in selected_idx
      ])

      if not expanded:
        continue

      full_recurring = selected_idx is None or len(expanded) == len(expanded_all)
      google_recur_id: Optional[str] = None
      if full_recurring:
        google_recur_id = gcal_create_recurring_event(item)

      for ev in expanded:
        all_day_flag = bool(ev.get("all_day"))
        if google_recur_id is None:
          google_single_id = gcal_create_single_event(ev["title"],
                                                      ev["start"],
                                                      ev.get("end"),
                                                      ev.get("location"),
                                                      all_day_flag)
        else:
          google_single_id = google_recur_id

        created.append(
            store_event(
                title=ev["title"],
                start=ev["start"],
                end=ev.get("end"),
                location=ev.get("location"),
                recur="recurring" if full_recurring else None,
                google_event_id=google_single_id,
                all_day=all_day_flag,
            ))

  if not created:
    raise HTTPException(status_code=422, detail="No valid items to create")

  return created


# -------------------------
# 삭제 NLP (기존)
# -------------------------
def create_delete_ids_from_natural_text(text: str,
                                        scope: Optional[Tuple[date, date]]
                                        = None) -> List[int]:
  if client is None:
    return []

  snapshot = [{
      "id": e.id,
      "title": e.title,
      "start": e.start,
      "end": e.end,
      "location": e.location,
      "recur": e.recur
  } for e in events if _event_within_scope(e, scope)][:50]

  user_payload = {
      "existing_events": snapshot,
      "delete_request": normalize_text(text),
      "timezone": "Asia/Seoul"
  }

  data = _chat_json("delete", build_delete_system_prompt(),
                    json.dumps(user_payload, ensure_ascii=False))

  ids = data.get("ids")
  if not isinstance(ids, list):
    return []

  cleaned: List[int] = []
  seen = set()
  for x in ids:
    try:
      i = int(x)
    except Exception:
      continue
    if i not in seen:
      seen.add(i)
      cleaned.append(i)

  return cleaned


def delete_events_by_ids(ids: List[int]) -> List[int]:
  global events
  if not ids:
    return []

  id_set = set(ids)
  existing_ids = {e.id for e in events}
  target_ids = sorted(list(id_set & existing_ids))
  if not target_ids:
    return []

  events = [e for e in events if e.id not in id_set]
  _save_events_to_disk()
  return target_ids


# -------------------------
# 삭제 미리보기(그룹화)
# -------------------------
def delete_preview_groups(text: str,
                          scope: Optional[Tuple[date, date]] = None
                          ) -> Dict[str, Any]:
  text = normalize_text(text)
  if not text:
    return {"groups": []}

  ids = create_delete_ids_from_natural_text(text, scope=scope)
  if not ids:
    return {"groups": []}

  id_set = set(ids)
  targets = [e for e in events if e.id in id_set and _event_within_scope(e, scope)]

  def group_key(e: Event) -> str:
    t = (e.start or
         "")[11:16] if isinstance(e.start, str) and len(e.start) >= 16 else ""
    loc = e.location or ""
    if e.recur == "recurring":
      return f"recur::{e.title}::{t}::{loc}"
    return f"single::{e.id}"

  groups_map: Dict[str, Dict[str, Any]] = {}

  for e in targets:
    key = group_key(e)
    g = groups_map.get(key)
    if g is None:
      t = (e.start or "")[11:16] if isinstance(e.start, str) and len(
          e.start) >= 16 else None
      g = {
          "group_key": key,
          "kind": "recurring" if e.recur == "recurring" else "single",
          "title": e.title,
          "time": t,
          "location": e.location,
          "ids": [],
          "items": [],
      }
      groups_map[key] = g

    g["ids"].append(e.id)
    g["items"].append({
        "id": e.id,
        "title": e.title,
        "start": e.start,
        "end": e.end,
        "location": e.location,
        "recur": e.recur,
        "all_day": e.all_day,
    })

  groups = list(groups_map.values())
  groups.sort(
      key=lambda x: (0 if x["kind"] == "recurring" else 1, x["title"] or ""))

  for g in groups:
    g["items"].sort(key=lambda it: it.get("start") or "")
    g["count"] = len(g["ids"])
    g["samples"] = [it.get("start") for it in g["items"][:5]]

  return {"groups": groups}


# -------------------------
# Google OAuth 엔드포인트
# -------------------------
@app.get("/auth/google/login")
def google_login():
  if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI):
    raise HTTPException(
        status_code=500,
        detail=
        "Google OAuth 환경변수(GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI)가 설정되지 않았습니다.",
    )

  params = {
      "client_id": GOOGLE_CLIENT_ID,
      "redirect_uri": GOOGLE_REDIRECT_URI,
      "response_type": "code",
      "scope": "https://www.googleapis.com/auth/calendar.events",
      "access_type": "offline",
      "prompt": "consent",
  }
  url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
      params)
  return RedirectResponse(url)


@app.get("/auth/google/callback")
def google_callback(request: Request):
  code = request.query_params.get("code")
  error = request.query_params.get("error")
  if error:
    return JSONResponse({"ok": False, "error": error})

  if not code:
    raise HTTPException(status_code=400, detail="code가 없습니다.")

  if not (GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REDIRECT_URI):
    raise HTTPException(
        status_code=500,
        detail=
        "Google OAuth 환경변수(GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI)가 설정되지 않았습니다.",
    )

  token_endpoint = "https://oauth2.googleapis.com/token"
  data = {
      "code": code,
      "client_id": GOOGLE_CLIENT_ID,
      "client_secret": GOOGLE_CLIENT_SECRET,
      "redirect_uri": GOOGLE_REDIRECT_URI,
      "grant_type": "authorization_code",
  }

  resp = requests.post(token_endpoint, data=data)
  if not resp.ok:
    raise HTTPException(status_code=500,
                        detail=f"토큰 교환 실패: {resp.status_code} {resp.text}")

  token_json = resp.json()
  access_token = token_json.get("access_token")
  refresh_token = token_json.get("refresh_token")
  expires_in = token_json.get("expires_in")

  if not access_token or not refresh_token:
    raise HTTPException(status_code=500,
                        detail=f"access_token/refresh_token 누락: {token_json}")

  expiry_dt = datetime.now(
      timezone.utc) + timedelta(seconds=int(expires_in or 0))
  token_data = {
      "token": access_token,
      "refresh_token": refresh_token,
      "token_uri": "https://oauth2.googleapis.com/token",
      "client_id": GOOGLE_CLIENT_ID,
      "client_secret": GOOGLE_CLIENT_SECRET,
      "scopes": GCAL_SCOPES,
      "expiry": expiry_dt.isoformat().replace("+00:00", "Z"),
  }

  save_gcal_token(token_data)

  # ✅ 성공 시 달력으로 이동
  return RedirectResponse("/calendar")


@app.get("/auth/google/status")
def google_status():
  token_data = load_gcal_token()
  return {
      "enabled": ENABLE_GCAL,
      "configured": is_gcal_configured(),
      "has_token": token_data is not None
  }


# -------------------------
# Admin / Logout
# -------------------------
@app.get("/admin")
def enter_admin():
  resp = RedirectResponse("/calendar")
  resp.set_cookie(ADMIN_COOKIE_NAME,
                  ADMIN_COOKIE_VALUE,
                  httponly=True,
                  samesite="lax")
  return resp


@app.get("/admin/exit")
def exit_admin():
  resp = RedirectResponse("/")
  resp.delete_cookie(ADMIN_COOKIE_NAME)
  return resp


@app.get("/logout")
def logout():
  clear_gcal_token()
  resp = RedirectResponse("/")
  resp.delete_cookie(ADMIN_COOKIE_NAME)
  return resp


# -------------------------
# API 엔드포인트
# -------------------------
@app.get("/api/events", response_model=List[Event])
def list_events(request: Request):
  if is_google_mode_active(request):
    return []
  return events


def _format_recent_local_event(ev: Event) -> Dict[str, Any]:
  return {
      "id": ev.id,
      "title": ev.title,
      "start": ev.start,
      "end": ev.end,
      "location": ev.location,
      "all_day": ev.all_day,
      "created_at": ev.created_at,
      "source": "local",
      "google_event_id": ev.google_event_id,
  }


def _format_recent_google_event(item: Dict[str, Any]) -> Dict[str, Any]:
  created_raw = item.get("created") or item.get("updated")
  return {
      "id": item.get("id"),
      "title": item.get("title"),
      "start": item.get("start"),
      "end": item.get("end"),
      "location": item.get("location"),
      "all_day": item.get("all_day"),
      "created_at": _normalize_google_timestamp(created_raw),
      "source": "google",
      "google_event_id": item.get("id"),
  }


@app.get("/api/recent-events")
def list_recent_events(request: Request):
  if is_google_mode_active(request):
    try:
      data = fetch_recent_google_events()
      formatted = [_format_recent_google_event(item) for item in data]
      return formatted[:200]
    except HTTPException:
      raise
    except Exception as exc:
      raise HTTPException(status_code=502,
                          detail=f"Google recent events 실패: {exc}") from exc

  cutoff = datetime.now(SEOUL) - timedelta(days=UNDO_RETENTION_DAYS)
  recent = [
      _format_recent_local_event(e)
      for e in events
      if _parse_created_at(e.created_at) >= cutoff
  ]
  recent.sort(key=lambda ev: _parse_created_at(ev.get("created_at")), reverse=True)
  return recent[:200]


@app.get("/api/google/events")
def google_events(start_date: str = Query(..., alias="start_date"),
                  end_date: str = Query(..., alias="end_date")):
  scope = _parse_scope_dates(start_date, end_date, require=True)
  return fetch_google_events_between(scope[0], scope[1])


@app.delete("/api/google/events/{event_id}")
def google_delete_event(event_id: str):
  if not event_id:
    raise HTTPException(status_code=400, detail="event_id가 없습니다.")
  try:
    gcal_delete_event(event_id)
    return {"ok": True}
  except HTTPException:
    raise
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google event 삭제 실패: {exc}") from exc


@app.patch("/api/google/events/{event_id}")
def google_update_event_api(event_id: str, payload: EventUpdate):
  if not event_id:
    raise HTTPException(status_code=400, detail="event_id가 없습니다.")
  if not is_gcal_configured():
    raise HTTPException(status_code=400,
                        detail="Google Calendar 연동이 설정되지 않았습니다.")

  start_iso = _coerce_patch_start(payload.start)
  if not start_iso:
    raise HTTPException(status_code=400, detail="시작 시각은 필수입니다.")

  end_iso = None
  if payload.end is not None:
    end_iso = _coerce_patch_end(payload.end)

  title_value: Optional[str] = None
  if payload.title is not None:
    title_value = payload.title.strip()
    if not title_value:
      raise HTTPException(status_code=400, detail="제목을 비울 수 없습니다.")

  location_value: Optional[str] = None
  if payload.location is not None:
    cleaned = _clean_optional_str(payload.location)
    location_value = "" if cleaned is None else cleaned

  all_day_flag = payload.all_day
  if all_day_flag is None:
    all_day_flag = is_all_day_span(start_iso, end_iso)

  try:
    gcal_update_event(event_id, title_value, start_iso, end_iso,
                      location_value, bool(all_day_flag))
  except Exception as exc:
    raise HTTPException(status_code=502,
                        detail=f"Google event 업데이트 실패: {exc}") from exc

  return {"ok": True}


@app.post("/api/events", response_model=Event)
def create_event(event_in: EventCreate):
  google_event_id: Optional[str] = None
  detected_all_day = event_in.all_day
  if detected_all_day is None:
    detected_all_day = is_all_day_span(event_in.start, event_in.end)
  try:
    google_event_id = gcal_create_single_event(event_in.title, event_in.start,
                                               event_in.end, event_in.location,
                                               detected_all_day)
  except Exception:
    _log_debug("[GCAL] /api/events create: 실패 (무시)")

  return store_event(
      title=event_in.title,
      start=event_in.start,
      end=event_in.end,
      location=event_in.location,
      recur=event_in.recur,
      google_event_id=google_event_id,
      all_day=bool(detected_all_day),
      created_at=event_in.created_at or _now_iso_minute(),
  )


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int):
  global events
  events = [e for e in events if e.id != event_id]
  return {"ok": True}


@app.patch("/api/events/{event_id}", response_model=Event)
def update_event(event_id: int, payload: EventUpdate):
  target = next((e for e in events if e.id == event_id), None)
  if target is None:
    raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")

  new_title = target.title
  if payload.title is not None:
    new_title = payload.title.strip()
    if not new_title:
      raise HTTPException(status_code=400, detail="제목을 입력해 주세요.")

  new_start = target.start
  if payload.start is not None:
    new_start = _coerce_patch_start(payload.start)
    if not new_start:
      raise HTTPException(status_code=400, detail="시작 시각 형식이 잘못되었습니다.")

  new_end = target.end
  if payload.end is not None:
    new_end = _coerce_patch_end(payload.end)

  location_provided = payload.location is not None
  new_location = target.location
  if location_provided:
    new_location = _clean_optional_str(payload.location)

  if not new_start:
    raise HTTPException(status_code=400, detail="시작 시각을 설정할 수 없습니다.")

  new_all_day = payload.all_day
  if new_all_day is None:
    new_all_day = is_all_day_span(new_start, new_end)

  if target.google_event_id:
    try:
      gcal_location = None
      if location_provided:
        gcal_location = "" if new_location is None else new_location
      gcal_update_event(target.google_event_id, new_title, new_start, new_end,
                        gcal_location, bool(new_all_day))
    except Exception as exc:
      _log_debug(f"[GCAL] local event update failed: {exc}")

  target.title = new_title
  target.start = new_start
  target.end = new_end
  target.location = new_location
  target.all_day = bool(new_all_day)
  _save_events_to_disk()
  return target


@app.post("/api/nlp-events", response_model=List[Event])
def create_events_from_natural_text(body: NaturalText):
  try:
    images = _validate_image_payload(body.images)
    return create_events_from_natural_text_core(body.text, images)
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Natural language error: {str(e)}")


@app.post("/api/nlp-event", response_model=Event)
def create_event_from_natural_text_compat(body: NaturalText):
  try:
    images = _validate_image_payload(body.images)
    created = create_events_from_natural_text_core(body.text, images)
    return created[0]
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Natural language error: {str(e)}")


@app.post("/api/nlp-preview")
def nlp_preview(body: NaturalText):
  try:
    images = _validate_image_payload(body.images)
    return preview_events_from_natural_text_core(body.text, images)
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502, detail=f"Preview NLP error: {str(e)}")


@app.post("/api/nlp-apply-add", response_model=List[Event])
def nlp_apply_add(body: ApplyItems):
  try:
    items = body.items or []
    if not items:
      raise HTTPException(status_code=400, detail="items is empty")
    return apply_add_items_core(items)
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502, detail=f"Apply Add error: {str(e)}")


@app.post("/api/nlp-delete-preview")
def nlp_delete_preview(body: NaturalTextWithScope):
  try:
    scope = _parse_scope_dates(body.start_date, body.end_date, require=True)
    return delete_preview_groups(body.text, scope=scope)
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Delete Preview error: {str(e)}")


@app.post("/api/delete-by-ids", response_model=DeleteResult)
def delete_by_ids(body: IdsPayload):
  deleted = delete_events_by_ids(body.ids or [])
  return DeleteResult(ok=True, deleted_ids=deleted, count=len(deleted))


@app.post("/api/nlp-delete-events", response_model=DeleteResult)
def delete_events_from_natural_text(body: NaturalTextWithScope):
  try:
    scope = _parse_scope_dates(body.start_date, body.end_date, require=True)
    ids = create_delete_ids_from_natural_text(body.text, scope=scope)
    deleted = delete_events_by_ids(ids)
    return DeleteResult(ok=True, deleted_ids=deleted, count=len(deleted))
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502, detail=f"Delete NLP error: {str(e)}")


def build_header_actions(request: Request, has_token: bool) -> str:
  admin = is_admin(request)
  token = has_token

  parts: List[str] = []

  if admin:
    parts.append('<span class="badge admin">ADMIN</span>')
    if token:
      parts.append('<span class="badge linked">Google 연동됨</span>')
    parts.append('<a class="header-btn" href="/admin/exit">Admin 해제</a>')
    parts.append('<a class="header-btn" href="/logout">로그아웃</a>')
    return "\n".join(parts)

  if token:
    parts.append('<span class="badge linked">Google 연동됨</span>')
    parts.append('<a class="header-btn" href="/logout">로그아웃</a>')
    return "\n".join(parts)

  # 토큰 없음(이 경우는 보통 /calendar 접근이 막히지만, 혹시 ENABLE_GCAL=0 등)
  if ENABLE_GCAL:
    parts.append(
        '<a class="header-btn" href="/auth/google/login">Google 로그인</a>')
  parts.append('<a class="header-btn" href="/admin">Admin</a>')
  return "\n".join(parts)


@app.get("/", response_class=HTMLResponse)
def start_page(request: Request):
  # ✅ 조건:
  # - admin 이거나
  # - gcal 비활성 이거나
  # - 토큰이 있으면
  # => 바로 캘린더로
  if is_admin(request):
    return RedirectResponse("/calendar")
  if not ENABLE_GCAL:
    return RedirectResponse("/calendar")
  if load_gcal_token() is not None:
    return RedirectResponse("/calendar")

  # 그 외: 시작 페이지
  return START_HTML


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
  # 접근 조건: admin or (gcal 비활성) or (token 있음)
  if not is_admin(request) and ENABLE_GCAL and load_gcal_token() is None:
    return RedirectResponse("/")

  token_present = load_gcal_token() is not None
  admin_mode = is_admin(request)
  actions_html = build_header_actions(request, token_present)
  context = {
      "admin": admin_mode,
      "google_linked": token_present,
      "mode": "admin"
      if admin_mode else ("google" if token_present and ENABLE_GCAL else "local"),
  }
  html = CALENDAR_HTML_TEMPLATE.replace("__HEADER_ACTIONS__", actions_html)
  context_json = json.dumps(context, ensure_ascii=False)
  context_script = f"<script>window.__APP_CONTEXT__ = {context_json};</script>"
  if "</head>" in html:
    html = html.replace("</head>", f"{context_script}\n</head>", 1)
  else:
    html = context_script + html
  return HTMLResponse(html)
