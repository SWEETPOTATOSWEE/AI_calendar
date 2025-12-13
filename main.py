from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import os
import json
import re
import time
import pathlib
import urllib.parse

import requests
from openai import OpenAI
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

app = FastAPI()

# -------------------------
# OpenAI Client
# -------------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client: Optional[OpenAI] = OpenAI(
    api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

SEOUL = ZoneInfo("Asia/Seoul")
LLM_DEBUG = os.getenv("LLM_DEBUG", "0") == "1"

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


class EventCreate(BaseModel):
  title: str
  start: str
  end: Optional[str] = None
  location: Optional[str] = None
  recur: Optional[str] = None
  google_event_id: Optional[str] = None


class NaturalText(BaseModel):
  text: str


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


def store_event(
    title: str,
    start: str,
    end: Optional[str],
    location: Optional[str],
    recur: Optional[str] = None,
    google_event_id: Optional[str] = None,
) -> Event:
  global next_id, events
  new_event = Event(
      id=next_id,
      title=title,
      start=start,
      end=end,
      location=location,
      recur=recur,
      google_event_id=google_event_id,
  )
  next_id += 1
  events.append(new_event)
  return new_event


def is_admin(request: Request) -> bool:
  return request.cookies.get(ADMIN_COOKIE_NAME) == ADMIN_COOKIE_VALUE


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
- 시간 정보가 없으면 recurring의 time과 duration_minutes는 null.
"""


def build_events_system_prompt() -> str:
  today = datetime.now(SEOUL).date().isoformat()
  return EVENTS_SYSTEM_PROMPT_TEMPLATE.replace("{TODAY}", today)


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
) -> None:
  if not LLM_DEBUG:
    return

  head = system_prompt[:220].replace("\n", "\\n")
  print(f"[LLM DEBUG] kind: {kind}")
  print(f"[LLM DEBUG] input_text: {input_text}")
  print(f"[LLM DEBUG] system_prompt(head): {head}")
  print(f"[LLM DEBUG] raw_content: {raw_content}")

  if latency_ms is not None:
    print(f"[LLM DEBUG] latency_ms: {latency_ms:.1f} ms")

  if usage is not None:
    p = usage.get("prompt")
    c = usage.get("completion")
    t = usage.get("total")
    print(f"[LLM DEBUG] usage: prompt={p}, completion={c}, total={t}")


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


def _chat_json(kind: str, system_prompt: str,
               user_text: str) -> Dict[str, Any]:
  """
    gpt-5-nano를 Chat Completions로 호출하는 버전.
    - max_tokens 대신 max_completion_tokens 사용
    - temperature / reasoning 등 gpt-5-nano에서 에러나는 옵션은 보내지 않는다.
    """
  c = get_client()

  now = datetime.now(SEOUL)
  ref_line = f"기준 시각: {now.strftime('%Y-%m-%d')} (Asia/Seoul)\n"
  input_text = ref_line + user_text

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
      usage_dict = {
          "prompt": getattr(usage_obj, "prompt_tokens", None),
          "completion": getattr(usage_obj, "completion_tokens", None),
          "total": getattr(usage_obj, "total_tokens", None),
      }

    _debug_print(kind, user_text, system_prompt, raw_content, latency_ms,
                 usage_dict)
    return _safe_json_loads(raw_content)

  except Exception as e:
    if LLM_DEBUG:
      print(f"[LLM DEBUG] exception: {repr(e)}")
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
          "recur": "recurring"
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


def gcal_create_single_event(title: str, start_iso: str,
                             end_iso: Optional[str],
                             location: Optional[str]) -> Optional[str]:
  if not is_gcal_configured():
    return None

  try:
    service = get_gcal_service()
  except Exception as e:
    if LLM_DEBUG:
      print(f"[GCAL] get service error: {e}")
    return None

  try:
    start_dt = datetime.strptime(start_iso,
                                 "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
    if end_iso:
      end_dt = datetime.strptime(end_iso,
                                 "%Y-%m-%dT%H:%M").replace(tzinfo=SEOUL)
    else:
      end_dt = start_dt + timedelta(hours=1)

    event_body: Dict[str, Any] = {
        "summary": title,
        "start": {
            "dateTime": start_dt.isoformat(),
            "timeZone": "Asia/Seoul"
        },
        "end": {
            "dateTime": end_dt.isoformat(),
            "timeZone": "Asia/Seoul"
        },
    }
    if location:
      event_body["location"] = location

    created = service.events().insert(calendarId=GOOGLE_CALENDAR_ID,
                                      body=event_body).execute()
    return created.get("id")
  except Exception as e:
    if LLM_DEBUG:
      print(f"[GCAL] create single event error: {e}")
    return None


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
    if LLM_DEBUG:
      print(f"[GCAL] get service error: {e}")
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
    if LLM_DEBUG:
      print(f"[GCAL] create recurring event error: {e}")
    return None


# -------------------------
# 자연어 → 일정 생성(기존)
# -------------------------
def create_events_from_natural_text_core(text: str) -> List[Event]:
  t = normalize_text(text)
  if not t:
    raise HTTPException(status_code=400, detail="Empty text")

  if client is None:
    raise HTTPException(
        status_code=500,
        detail="LLM client is not configured (OPENAI_API_KEY 미설정)")

  data = _chat_json("parse", build_events_system_prompt(), f"문장: {t}")
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

      if not title or not isinstance(start, str) or not start.strip():
        continue

      start = start.strip()
      if not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", start):
        continue

      if end is not None:
        end = str(end).strip() or None
        if end is not None and not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$",
                                            end):
          end = None

      loc_str = (location or "").strip() or None

      flat_events.append({
          "title": title,
          "start": start,
          "end": end,
          "location": loc_str,
          "recur": None,
          "source_type": "single"
      })

    elif typ == "recurring":
      expanded = _expand_recurring_item(item)
      if expanded:
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
    google_event_id: Optional[str] = None

    if ev.get("source_type") == "single":
      google_event_id = gcal_create_single_event(title, start, end, location)

    created.append(
        store_event(title=title,
                    start=start,
                    end=end,
                    location=location,
                    recur=recur,
                    google_event_id=google_event_id))

  for rec_item in recurring_items:
    gcal_create_recurring_event(rec_item)

  return created


# -------------------------
# NLP Preview (추가 미리보기)
# -------------------------
def preview_events_from_natural_text_core(text: str) -> Dict[str, Any]:
  t = normalize_text(text)
  if not t:
    raise HTTPException(status_code=400, detail="Empty text")

  if client is None:
    raise HTTPException(
        status_code=500,
        detail="LLM client is not configured (OPENAI_API_KEY 미설정)")

  data = _chat_json("parse", build_events_system_prompt(), f"문장: {t}")
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

      if not title or not isinstance(start, str) or not re.match(
          r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", start.strip()):
        continue

      end_str: Optional[str] = None
      if isinstance(end, str) and re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$",
                                           end.strip()):
        end_str = end.strip()

      out_items.append({
          "type": "single",
          "title": title,
          "start": start.strip(),
          "end": end_str,
          "location": (location or "").strip() or None
      })

    elif typ == "recurring":
      title = (item.get("title") or "").strip()
      if not title:
        continue

      expanded = _expand_recurring_item(item)
      count = len(expanded)

      samples = []
      for ev in expanded[:5]:
        samples.append({"start": ev.get("start"), "end": ev.get("end")})

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

      if not title or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", start):
        continue

      end_str: Optional[str] = None
      if isinstance(end, str) and re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$",
                                           end.strip()):
        end_str = end.strip()

      google_event_id = gcal_create_single_event(title, start, end_str,
                                                 location)

      created.append(
          store_event(
              title=title,
              start=start,
              end=end_str,
              location=location,
              recur=None,
              google_event_id=google_event_id,
          ))

    elif typ == "recurring":
      google_recur_id = gcal_create_recurring_event(item)

      expanded = _expand_recurring_item(item)
      for ev in expanded:
        created.append(
            store_event(
                title=ev["title"],
                start=ev["start"],
                end=ev.get("end"),
                location=ev.get("location"),
                recur="recurring",
                google_event_id=google_recur_id,
            ))

  if not created:
    raise HTTPException(status_code=422, detail="No valid items to create")

  return created


# -------------------------
# 삭제 NLP (기존)
# -------------------------
def create_delete_ids_from_natural_text(text: str) -> List[int]:
  if client is None:
    return []

  snapshot = [{
      "id": e.id,
      "title": e.title,
      "start": e.start,
      "end": e.end,
      "location": e.location,
      "recur": e.recur
  } for e in events[:50]]

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
  return target_ids


# -------------------------
# 삭제 미리보기(그룹화)
# -------------------------
def delete_preview_groups(text: str) -> Dict[str, Any]:
  text = normalize_text(text)
  if not text:
    return {"groups": []}

  ids = create_delete_ids_from_natural_text(text)
  if not ids:
    return {"groups": []}

  id_set = set(ids)
  targets = [e for e in events if e.id in id_set]

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
        "recur": e.recur
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
def list_events():
  return events


@app.post("/api/events", response_model=Event)
def create_event(event_in: EventCreate):
  google_event_id: Optional[str] = None
  try:
    google_event_id = gcal_create_single_event(event_in.title, event_in.start,
                                               event_in.end, event_in.location)
  except Exception:
    if LLM_DEBUG:
      print("[GCAL] /api/events create: 실패 (무시)")

  return store_event(
      title=event_in.title,
      start=event_in.start,
      end=event_in.end,
      location=event_in.location,
      recur=event_in.recur,
      google_event_id=google_event_id,
  )


@app.delete("/api/events/{event_id}")
def delete_event(event_id: int):
  global events
  events = [e for e in events if e.id != event_id]
  return {"ok": True}


@app.post("/api/nlp-events", response_model=List[Event])
def create_events_from_natural_text(body: NaturalText):
  try:
    return create_events_from_natural_text_core(body.text)
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Natural language error: {str(e)}")


@app.post("/api/nlp-event", response_model=Event)
def create_event_from_natural_text_compat(body: NaturalText):
  try:
    created = create_events_from_natural_text_core(body.text)
    return created[0]
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502,
                        detail=f"Natural language error: {str(e)}")


@app.post("/api/nlp-preview")
def nlp_preview(body: NaturalText):
  try:
    return preview_events_from_natural_text_core(body.text)
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
def nlp_delete_preview(body: NaturalText):
  try:
    return delete_preview_groups(body.text)
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
def delete_events_from_natural_text(body: NaturalText):
  try:
    ids = create_delete_ids_from_natural_text(body.text)
    deleted = delete_events_by_ids(ids)
    return DeleteResult(ok=True, deleted_ids=deleted, count=len(deleted))
  except HTTPException:
    raise
  except Exception as e:
    raise HTTPException(status_code=502, detail=f"Delete NLP error: {str(e)}")


# -------------------------
# UI: Start / Calendar
# -------------------------
START_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Calendar</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap" rel="stylesheet">
  <style>
    :root{
      --bg:#f7f7f5; --card:#fff; --text:#2f3437; --muted:#6b6f73; --border:#e7e7e4;
    }
    *{box-sizing:border-box;}
    body{
      margin:0; font-family:"Noto Sans KR", system-ui, -apple-system, "Segoe UI", sans-serif;
      background:var(--bg); color:var(--text);
      min-height:100vh; display:flex; align-items:center; justify-content:center;
      padding:16px;
    }
    .card{
      width:min(520px, 100%);
      background:var(--card);
      border:1px solid var(--border);
      border-radius:16px;
      padding:18px;
    }
    h1{margin:0 0 8px; font-size:18px; font-weight:900;}
    p{margin:0 0 14px; color:var(--muted); font-weight:700; font-size:13px; line-height:1.45;}
    .row{display:flex; gap:10px; flex-wrap:wrap;}
    a.btn{
      display:inline-flex; align-items:center; justify-content:center;
      padding:10px 12px; border-radius:12px; border:1px solid var(--border);
      background:#fff; color:var(--text); text-decoration:none; font-weight:900; font-size:13px;
    }
    a.btn:hover{background:#f2f2f0;}
    .hint{margin-top:10px; font-size:12px; color:var(--muted); font-weight:800;}
  </style>
</head>
<body>
  <div class="card">
    <h1>Calendar</h1>
    <p>구글 캘린더 연동 또는 Admin 모드로 시작합니다.</p>
    <div class="row">
      <a class="btn" href="/auth/google/login">Google로 로그인</a>
      <a class="btn" href="/admin">Admin으로 접속</a>
    </div>
    <div class="hint">자동 로그인을 원하면 위 버튼을 누르세요. (테스트 환경)</div>
  </div>
</body>
</html>
"""

CALENDAR_HTML = """
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>캘린더</title>

  <script src="https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.js"></script>

  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link
    href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap"
    rel="stylesheet"
  >

  <style>
    :root{
      --bg:#f7f7f5;
      --card:#ffffff;
      --text:#2f3437;
      --muted:#6b6f73;
      --border:#e7e7e4;

      --primary:#2f6feb;
      --danger:#ef4444;

      --radius:14px;
      --shadow:none;

      --sidebar-w: 360px;
      --ctl-h: 34px;
    }

    *{box-sizing:border-box;}
    html, body{height:100%;}
    body{
      margin:0;
      font-family:"Noto Sans KR", system-ui, -apple-system, "Segoe UI", sans-serif;
      background:var(--bg);
      color:var(--text);
    }
    input, textarea, button, select{ font-family:inherit; }

    header{
      position:sticky;
      top:0;
      z-index:30;
      background:rgba(247,247,245,.92);
      backdrop-filter:saturate(180%) blur(10px);
      border-bottom:1px solid var(--border);
    }

    .topbar{
      display:flex;
      align-items:center;
      justify-content:space-between;
      padding:10px 14px;
      gap:12px;
      max-width: 1400px;
      margin:0 auto;
    }

    .topbar-left{
      display:flex;
      align-items:center;
      gap:10px;
      min-width: 0;
    }

    .brand{
      font-weight:800;
      letter-spacing:-0.2px;
      padding:6px 10px;
      border-radius:10px;
      background:#fff;
      border:1px solid var(--border);
      flex:0 0 auto;
    }

    .divider{
      width:1px;
      height:18px;
      background:var(--border);
      flex:0 0 auto;
    }

    .nav-btn{
      height:34px;
      padding:0 10px;
      border-radius:10px;
      border:1px solid var(--border);
      background:#fff;
      color:var(--text);
      font-weight:700;
      cursor:pointer;
      display:inline-flex;
      align-items:center;
      gap:6px;
      flex:0 0 auto;
    }
    .nav-btn:hover{background:#f2f2f0;}

    .ym-label{
      font-size:16px;
      font-weight:800;
      letter-spacing:-0.2px;
      padding:0 4px;
      white-space:nowrap;
      flex:0 0 auto;
    }

    .topbar-right{
      display:flex;
      align-items:center;
      gap:10px;
      flex:0 0 auto;
    }

    .view-switch{
      display:flex;
      align-items:center;
      gap:6px;
      padding:4px;
      border-radius:12px;
      border:1px solid var(--border);
      background:#fff;
    }
    .view-btn{
      height:30px;
      padding:0 10px;
      border-radius:10px;
      border:0;
      background:transparent;
      color:var(--muted);
      font-weight:800;
      cursor:pointer;
    }
    .view-btn:hover{background:#f2f2f0; color:var(--text);}
    .view-btn.active{
      background:rgba(47,52,55,.10);
      color:var(--text);
    }

    .header-actions{
      display:flex;
      align-items:center;
      gap:8px;
      padding-left:4px;
    }
    .badge{
      display:inline-flex;
      align-items:center;
      padding:.25rem .55rem;
      border-radius:999px;
      font-size:.78rem;
      font-weight:800;
      border:1px solid var(--border);
      background:#fff;
      color:var(--text);
      white-space:nowrap;
    }
    .badge.linked{
      background:#eaf6ee;
      border-color:#c8e9d3;
      color:#1f6f3d;
    }
    .badge.admin{
      background:#fff3d8;
      border-color:#ffe1a6;
      color:#7a4b00;
    }
    .header-btn{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      padding:.32rem .62rem;
      border-radius:10px;
      border:1px solid var(--border);
      background:#fff;
      color:var(--text);
      text-decoration:none;
      font-size:.82rem;
      font-weight:800;
      white-space:nowrap;
    }
    .header-btn:hover{ background:#f2f2f0; }

    /* ===== Layout: calendar left, utilities right ===== */
    .app{
      display:grid;
      grid-template-columns: 1fr var(--sidebar-w);
      gap:12px;
      padding:12px;
      max-width: 1400px;
      margin:0 auto;
      align-items:start;
    }

    main{min-width:0;}
    aside{
      position:sticky;
      top:62px;
      align-self:start;
      height: calc(100vh - 74px);
      overflow:auto;
      padding-right:2px;
    }

    .panel{
      background:var(--card);
      border:1px solid var(--border);
      border-radius:var(--radius);
      box-shadow:var(--shadow);
      padding:12px;
      margin-bottom:12px;
    }

    .panel-title{
      display:flex;
      align-items:baseline;
      justify-content:space-between;
      gap:10px;
      margin:0 0 10px;
    }
    .panel-title h2{
      margin:0;
      font-size:13px;
      font-weight:900;
      letter-spacing:-0.2px;
      color:var(--text);
    }
    .panel-title .sub{
      font-size:12px;
      color:var(--muted);
      font-weight:800;
      white-space:nowrap;
    }

    /* ===== Calendar ===== */
    #calendar-container{
      background:var(--card);
      border:1px solid var(--border);
      border-radius:var(--radius);
      box-shadow:var(--shadow);
      padding:10px;
      min-height: 740px;
    }

    #calendar .fc{
      font-size:0.92rem;
      color:var(--text);
    }
    #calendar .fc-theme-standard td,
    #calendar .fc-theme-standard th{
      border-color:var(--border);
    }
    #calendar .fc-scrollgrid{ border:0; }
    #calendar .fc-col-header-cell-cushion{
      font-weight:900;
      color:var(--muted);
      padding:10px 6px;
    }
    #calendar .fc-daygrid-day-number{
      font-weight:900;
      color:var(--text);
      padding:7px 8px;
    }
    #calendar .fc-day-today{
      background: rgba(47,52,55,.04) !important;
    }
    #calendar .fc .fc-daygrid-event{
      background:transparent;
      border:0;
      color:var(--text);
      padding:2px 6px 2px 18px;
      border-radius:8px;
      position:relative;
    }
    #calendar .fc .fc-daygrid-event:hover{ background:#f2f2f0; }
    #calendar .fc .fc-daygrid-event::before{
      content:"";
      width:6px;
      height:6px;
      border-radius:50%;
      background:var(--primary);
      position:absolute;
      left:8px;
      top:50%;
      transform:translateY(-50%);
      opacity:.9;
    }
    #calendar .fc .fc-daygrid-event .fc-event-title{ font-weight:900; }
    #calendar .fc .fc-daygrid-more-link{
      color:var(--muted);
      font-weight:900;
    }
    #calendar .fc .fc-daygrid-more-link:hover{ color:var(--text); }

    /* ✅ 선택 날짜 하이라이트 */
    #calendar .fc-daygrid-day.selected-day{
      background: rgba(47,111,235,.08) !important;
    }
    #calendar .fc-daygrid-day.selected-day .fc-daygrid-day-frame{
      border-radius: 10px;
      box-shadow: inset 0 0 0 2px rgba(47,111,235,.28);
    }
    #calendar .fc .selected-day-header{
      background: rgba(47,111,235,.08);
    }

    /* ===== NLP Composer ===== */
    .composer textarea{
      width:100%;
      border:1px solid var(--border);
      border-radius:12px;
      padding:10px 10px 44px 10px;
      font-size:14px;
      line-height:1.45;
      outline:none;
      resize:none;
      background:#fff;
      color:var(--text);
      min-height:56px;
      height:56px;
      overflow:hidden;
      transition: height .18s cubic-bezier(.25,.8,.25,1);
    }
    .composer textarea:focus{ border-color: rgba(47,52,55,.35); }

    .composer-controls{
      position:relative;
      margin-top:-40px;
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:10px;
      padding:0 8px 6px;
      pointer-events:none;
    }
    .composer-controls > *{ pointer-events:auto; }

    .seg-toggle{
      display:inline-flex;
      align-items:center;
      gap:0;
      border:1px solid var(--border);
      background:#fff;
      border-radius:12px;
      padding:3px;
      height:32px;
    }
    .seg-toggle input{ display:none; }
    .seg{
      height:26px;
      padding:0 10px;
      border-radius:10px;
      display:inline-flex;
      align-items:center;
      font-weight:900;
      font-size:12px;
      color:var(--muted);
      cursor:pointer;
      user-select:none;
    }
    #nlp-mode-toggle:not(:checked) ~ .seg.add{
      background:rgba(47,52,55,.10);
      color:var(--text);
    }
    #nlp-mode-toggle:checked ~ .seg.del{
      background:rgba(239,68,68,.12);
      color:#8a1f1f;
    }

    .inline-action{
      position:relative;
      width:var(--ctl-h);
      height:var(--ctl-h);
      flex:0 0 auto;
    }
    .inline-action > *{ position:absolute; inset:0; }

    .Btn{
      width:var(--ctl-h);
      height:var(--ctl-h);
      border-radius:10px;
      border:1px solid var(--border);
      background:#fff;
      cursor:pointer;
      display:flex;
      align-items:center;
      justify-content:center;
      transition:transform .15s ease, background .15s ease, opacity .35s ease;
    }
    .Btn:hover{ background:#f2f2f0; }
    .Btn:active{ transform:translateY(1px); }
    .Btn.mode-delete{ border-color: rgba(239,68,68,.35); }

    .Btn .sign svg{ width:16px; height:16px; }
    .Btn .sign svg path{ fill: var(--text); }
    .Btn.mode-delete .sign svg path{ fill: #8a1f1f; }

    .scale-0{transform:scale(.95); opacity:0; pointer-events:none;}
    .scale-1{transform:scale(1); opacity:1;}

    .loader-wrapper{
      display:inline-flex;
      align-items:center;
      justify-content:center;
      transition:opacity .35s ease, transform .35s ease;
    }
    .loader{
      width:18px;
      height:18px;
      border-radius:50%;
      border:2px solid rgba(47,52,55,.18);
      border-top-color: rgba(47,52,55,.55);
      animation:spin 0.9s linear infinite;
    }
    .loader.is-delete{
      border-color: rgba(239,68,68,.18);
      border-top-color: rgba(239,68,68,.6);
    }
    @keyframes spin{ to{ transform:rotate(360deg); } }

    /* ===== NLP Preview ===== */
    .nlp-preview{
      margin-top:10px;
      border-top:1px solid var(--border);
      padding-top:10px;
      display:none;
    }
    .nlp-preview .pv-title{
      font-size:12px;
      font-weight:900;
      color:var(--muted);
      margin-bottom:8px;
    }
    #nlp-preview-ul{
      list-style:none;
      padding:0;
      margin:0;
    }
    #nlp-preview-ul li{
      padding:8px 6px;
      border:1px solid var(--border);
      border-radius:12px;
      background:#fff;
      margin-bottom:8px;
    }
    .pv-line1{
      font-weight:900;
      font-size:13px;
      white-space:nowrap;
      overflow:hidden;
      text-overflow:ellipsis;
    }
    .pv-line2{
      margin-top:2px;
      font-size:12px;
      color:var(--muted);
      font-weight:800;
      white-space:nowrap;
      overflow:hidden;
      text-overflow:ellipsis;
    }

    /* ===== Quick add ===== */
    .field{
      display:flex;
      flex-direction:column;
      gap:6px;
      margin-bottom:10px;
    }
    .label{
      font-size:12px;
      font-weight:900;
      color:var(--muted);
    }
    .input, .pill-input{
      width:100%;
      border:1px solid var(--border);
      border-radius:12px;
      padding:10px 10px;
      background:#fff;
      color:var(--text);
      outline:none;
      font-size:14px;
    }
    .input:focus, .pill-input:focus{
      border-color: rgba(47,52,55,.35);
    }

    .pill-field{ position:relative; margin-bottom:10px; }
    .pill-placeholder{
      position:absolute;
      left:12px;
      top:50%;
      transform:translateY(-50%);
      color:var(--muted);
      font-size:13px;
      pointer-events:none;
      font-weight:800;
    }
    .pill-placeholder.hidden{display:none;}
    .pill-input{ color:transparent; }
    .pill-input.has-value{color:inherit;}

    .primary-btn{
      height:38px;
      padding:0 12px;
      border-radius:12px;
      border:1px solid var(--border);
      background: rgba(47,52,55,.10);
      color:var(--text);
      font-weight:900;
      cursor:pointer;
      display:inline-flex;
      align-items:center;
      justify-content:center;
    }
    .primary-btn:hover{ background: rgba(47,52,55,.14); }

    /* ===== Selected date list ===== */
    #events-ul{list-style:none; padding:0; margin:0;}
    #events-ul li{
      padding:10px 6px;
      border-bottom:1px solid var(--border);
      display:flex;
      align-items:center;
      gap:10px;
    }
    .event-dot{
      width:8px;
      height:8px;
      border-radius:50%;
      background:var(--primary);
      flex:0 0 auto;
      margin-top:2px;
    }
    .event-info{flex:1; min-width:0;}
    .event-title{
      font-weight:900;
      font-size:13px;
      white-space:nowrap;
      overflow:hidden;
      text-overflow:ellipsis;
    }
    .event-meta{
      color:var(--muted);
      font-size:12px;
      margin-top:2px;
      white-space:nowrap;
      overflow:hidden;
      text-overflow:ellipsis;
    }
    .delete-btn{
      border:1px solid var(--border);
      background:#fff;
      color:var(--muted);
      font-weight:900;
      border-radius:10px;
      padding:7px 10px;
      cursor:pointer;
      flex:0 0 auto;
    }
    .delete-btn:hover{
      border-color: rgba(239,68,68,.35);
      color:#8a1f1f;
      background: rgba(239,68,68,.06);
    }

    /* ===== Confirm Modal ===== */
    #confirm-overlay{
      position:fixed;
      inset:0;
      background: rgba(0,0,0,.28);
      display:none;
      align-items:center;
      justify-content:center;
      padding:14px;
      z-index:60;
    }
    #confirm-modal{
      width:min(560px, 100%);
      background:#fff;
      border:1px solid var(--border);
      border-radius: 16px;
      box-shadow: 0 10px 30px rgba(0,0,0,.18);
      overflow:hidden;
    }
    .cm-head{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:12px;
      padding:14px 14px 10px;
      border-bottom:1px solid var(--border);
    }
    .cm-title{ font-weight:900; font-size:14px; }
    .cm-desc{
      margin-top:4px;
      color:var(--muted);
      font-weight:800;
      font-size:12px;
      line-height:1.4;
    }
    .cm-x{
      border:1px solid var(--border);
      background:#fff;
      border-radius:10px;
      width:34px;
      height:34px;
      cursor:pointer;
      font-weight:900;
    }
    .cm-x:hover{ background:#f2f2f0; }
    .cm-body{
      padding:12px 14px;
      max-height: 56vh;
      overflow:auto;
    }
    .cm-foot{
      display:flex;
      justify-content:flex-end;
      gap:10px;
      padding:12px 14px;
      border-top:1px solid var(--border);
      background: #fafaf9;
    }
    .cm-btn{
      border-radius:12px;
      border:1px solid var(--border);
      padding:10px 12px;
      font-weight:900;
      cursor:pointer;
      background:#fff;
    }
    .cm-btn:hover{ background:#f2f2f0; }
    .cm-btn.primary{
      background: rgba(47,52,55,.10);
    }
    .cm-btn.primary:hover{
      background: rgba(47,52,55,.14);
    }

    .cm-row{
      border:1px solid var(--border);
      border-radius:12px;
      padding:10px 10px;
      margin-bottom:8px;
      background:#fff;
    }
    .cm-row-top{
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:10px;
    }
    .cm-left{
      display:flex;
      gap:10px;
      align-items:flex-start;
      min-width:0;
    }
    .cm-check{ margin-top:2px; }
    .cm-main{ min-width:0; }
    .cm-line1{
      font-weight:900;
      font-size:13px;
      white-space:nowrap;
      overflow:hidden;
      text-overflow:ellipsis;
    }
    .cm-line2{
      margin-top:2px;
      color:var(--muted);
      font-weight:800;
      font-size:12px;
      white-space:nowrap;
      overflow:hidden;
      text-overflow:ellipsis;
    }
    .cm-mini{
      margin-top:6px;
      color:var(--muted);
      font-weight:800;
      font-size:12px;
    }
    .cm-toggle{
      border:1px solid var(--border);
      background:#fff;
      border-radius:10px;
      padding:6px 10px;
      font-weight:900;
      cursor:pointer;
      color:var(--muted);
      white-space:nowrap;
    }
    .cm-toggle:hover{ background:#f2f2f0; color:var(--text); }
    .cm-sublist{
      margin-top:10px;
      padding-top:10px;
      border-top:1px solid var(--border);
      display:none;
    }
    .cm-subitem{
      display:flex;
      align-items:flex-start;
      gap:10px;
      padding:8px 6px;
      border-radius:10px;
    }
    .cm-subitem:hover{ background:#f7f7f5; }

    @media (max-width: 980px){
      .app{ grid-template-columns: 1fr; }
      aside{ position:relative; top:auto; height:auto; }
      :root{ --sidebar-w: 1fr; }
    }
  </style>
</head>

<body>
<header>
  <div class="topbar">
    <div class="topbar-left">
      <div class="brand">Calendar</div>
      <div class="divider"></div>

      <button class="nav-btn" id="cal-prev" type="button" aria-label="이전"><span aria-hidden="true">←</span></button>
      <button class="nav-btn" id="cal-today" type="button">오늘</button>
      <button class="nav-btn" id="cal-next" type="button" aria-label="다음"><span aria-hidden="true">→</span></button>

      <div class="ym-label" id="ym-label"></div>
    </div>

    <div class="topbar-right">
      <div class="view-switch" role="tablist" aria-label="뷰 전환">
        <button class="view-btn active" type="button" data-cal-view="dayGridMonth" id="cal-view-month">월</button>
        <button class="view-btn" type="button" data-cal-view="timeGridWeek" id="cal-view-week">주</button>
        <button class="view-btn" type="button" data-cal-view="timeGridDay" id="cal-view-day">일</button>
      </div>

      <div class="header-actions">__HEADER_ACTIONS__</div>
    </div>
  </div>
</header>

<div class="app">
  <!-- Calendar LEFT -->
  <main>
    <div id="calendar-container">
      <div id="calendar"></div>
    </div>
  </main>

  <!-- Utilities RIGHT -->
  <aside id="sidebar">

    <div class="panel composer">
      <div class="panel-title">
        <h2>자연어로 추가/삭제</h2>
        <div class="sub">Enter 실행 · Shift+Enter 줄바꿈</div>
      </div>

      <textarea
        id="nlp-unified-text"
        placeholder="예) 내일 오후 2시에 스타벅스에서 회의&#10;예) 매주 월수금 9시 운동 (1시간)"
        rows="1"
      ></textarea>

      <div class="composer-controls">
        <label class="seg-toggle" title="추가/삭제 전환">
          <input type="checkbox" id="nlp-mode-toggle"/>
          <span class="seg add">추가</span>
          <span class="seg del">삭제</span>
        </label>

        <div class="inline-action">
          <button
            type="button"
            id="nlp-action-btn"
            class="Btn mode-add scale-1"
            aria-label="문장으로 일정 추가/삭제"
            title="실행"
          >
            <div class="sign">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M12 5c.55 0 1 .45 1 1v5h5c.55 0 1 .45 1 1s-.45 1-1 1h-5v5c0 .55-.45 1-1 1s-1-.45-1-1v-5H6c-.55 0-1-.45-1-1s.45-1 1-1h5V6c0-.55.45-1 1-1z"/>
              </svg>
            </div>
          </button>

          <div class="loader-wrapper scale-0" id="nlp-unified-loader">
            <div class="loader"></div>
          </div>
        </div>
      </div>

      <!-- Preview -->
      <div class="nlp-preview" id="nlp-preview">
        <div class="pv-title">추가될 일정 미리보기</div>
        <ul id="nlp-preview-ul"></ul>
      </div>
    </div>

    <form id="event-form" class="panel">
      <div class="panel-title">
        <h2>빠른 등록</h2>
        <div class="sub">필수: 제목·시작</div>
      </div>

      <div class="field">
        <div class="label">제목</div>
        <input type="text" id="title" class="input" placeholder="예) 팀 미팅" required />
      </div>

      <div class="pill-field">
        <span class="pill-placeholder" data-target="start">시작</span>
        <input type="datetime-local" id="start" class="pill-input" required />
      </div>

      <div class="pill-field">
        <span class="pill-placeholder" data-target="end">종료</span>
        <input type="datetime-local" id="end" class="pill-input" />
      </div>

      <div class="pill-field">
        <span class="pill-placeholder" data-target="location">장소</span>
        <input type="text" id="location" class="pill-input" />
      </div>

      <button type="submit" class="primary-btn">일정 추가</button>
    </form>

    <div class="panel" id="events-list">
      <div class="panel-title">
        <h2>선택한 날짜 일정</h2>
        <div class="sub"><span id="selected-date-label"></span> · <span id="selected-count-label"></span></div>
      </div>
      <ul id="events-ul"></ul>
    </div>

  </aside>
</div>

<!-- Confirm Modal -->
<div id="confirm-overlay">
  <div id="confirm-modal" role="dialog" aria-modal="true">
    <div class="cm-head">
      <div>
        <div class="cm-title" id="confirm-title"></div>
        <div class="cm-desc" id="confirm-desc"></div>
      </div>
      <button class="cm-x" id="confirm-close" type="button">✕</button>
    </div>

    <div class="cm-body">
      <div id="confirm-list"></div>
    </div>

    <div class="cm-foot">
      <button class="cm-btn" id="confirm-cancel" type="button">취소</button>
      <button class="cm-btn primary" id="confirm-ok" type="button">적용</button>
    </div>
  </div>
</div>

<script>
  const apiBase = "/api";
  let calendar = null;

  let selectedDateStr = null; // YYYY-MM-DD
  let previewTimer = null;

  let confirmState = { mode: null, addItems: [], deleteGroups: [] };

  function toDateStrLocal(d){
    const y = d.getFullYear();
    const m = String(d.getMonth()+1).padStart(2,"0");
    const day = String(d.getDate()).padStart(2,"0");
    return `${y}-${m}-${day}`;
  }

  function updateYearMonthLabel(date){
    const ymLabel = document.getElementById("ym-label");
    const y = date.getFullYear();
    const m = date.getMonth() + 1;
    ymLabel.textContent = `${y}년 ${m}월`;
  }

  function setActiveView(viewType){
    document.querySelectorAll("[data-cal-view]").forEach(btn => {
      btn.classList.toggle("active", btn.dataset.calView === viewType);
    });
  }

  function setSelectedDate(dateStr){
    selectedDateStr = dateStr;
    const el = document.getElementById("selected-date-label");
    if(el) el.textContent = dateStr;
    if(calendar) calendar.rerenderDates(); // ✅ highlight refresh
  }

  function setupShadowAutoGrow(textareaId){
    const ta = document.getElementById(textareaId);
    if(!ta) return;

    let base = 56;
    let rafId = null;

    const computeBase = () => {
      const prev = ta.value;
      ta.value = "";
      ta.style.height = "auto";
      const measured = ta.scrollHeight;
      base = Math.max(measured || 0, 56);
      ta.value = prev;
    };

    const resize = () => {
      const value = ta.value ?? "";
      if(value.trim() === ""){
        if(rafId) cancelAnimationFrame(rafId);
        ta.style.height = base + "px";
        return;
      }

      const startH = ta.getBoundingClientRect().height;
      ta.style.height = "auto";
      let target = ta.scrollHeight;

      if(target <= base + 1){ target = base; }
      ta.style.height = startH + "px";

      if(rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => { ta.style.height = target + "px"; });
    };

    requestAnimationFrame(() => {
      computeBase();
      ta.style.height = base + "px";
      resize();
    });

    ["input","focus","change"].forEach(evt => ta.addEventListener(evt, resize));
    window.addEventListener("resize", () => { computeBase(); resize(); });
  }

  function fmtRange(start, end){
    if(!start) return "";
    const d = start.slice(0,10);
    const st = start.slice(11,16);
    if(end) return `${d} ${st}–${end.slice(11,16)}`;
    return `${d} ${st}`;
  }

  function renderPreview(data){
    const wrap = document.getElementById("nlp-preview");
    const ul = document.getElementById("nlp-preview-ul");
    if(!wrap || !ul) return;

    const items = data?.items;
    if(!Array.isArray(items) || items.length === 0){
      wrap.style.display = "none";
      ul.innerHTML = "";
      return;
    }

    ul.innerHTML = "";
    for(const it of items){
      const li = document.createElement("li");

      const line1 = document.createElement("div");
      line1.className = "pv-line1";
      line1.textContent = it.title || "(제목 없음)";

      const line2 = document.createElement("div");
      line2.className = "pv-line2";

      if(it.type === "single"){
        line2.textContent = fmtRange(it.start, it.end) + (it.location ? ` · ${it.location}` : "");
      }else if(it.type === "recurring"){
        const sd = it.start_date || "?";
        const ed = it.end_date || "?";
        const cnt = typeof it.count === "number" ? it.count : 0;
        const time = it.time ? it.time : "시간 없음";
        line2.textContent = `반복: ${sd}~${ed} · ${time} · ${cnt}회` + (it.location ? ` · ${it.location}` : "");

        if(Array.isArray(it.samples) && it.samples.length){
          const small = document.createElement("div");
          small.className = "pv-line2";
          const s = it.samples.slice(0,2).map(x => (x || "").replace("T"," ")).filter(Boolean).join(" / ");
          if(s) small.textContent = `예: ${s}${(cnt > 2) ? " …" : ""}`;
          li.appendChild(line1);
          li.appendChild(line2);
          li.appendChild(small);
          ul.appendChild(li);
          continue;
        }
      }else{
        line2.textContent = "형식 미확인";
      }

      li.appendChild(line1);
      li.appendChild(line2);
      ul.appendChild(li);
    }

    wrap.style.display = "block";
  }

  async function previewNlpIfNeeded(){
    const input = document.getElementById("nlp-unified-text");
    const toggle = document.getElementById("nlp-mode-toggle");
    const text = input?.value?.trim() ?? "";

    const wrap = document.getElementById("nlp-preview");
    const ul = document.getElementById("nlp-preview-ul");

    if(toggle?.checked || !text){
      if(wrap) wrap.style.display = "none";
      if(ul) ul.innerHTML = "";
      return;
    }

    try{
      const res = await fetch(apiBase + "/nlp-preview", {
        method:"POST",
        headers:{ "Content-Type":"application/json" },
        body: JSON.stringify({ text })
      });
      if(!res.ok){
        if(wrap) wrap.style.display = "none";
        if(ul) ul.innerHTML = "";
        return;
      }
      const data = await res.json();
      renderPreview(data);
    }catch{
      if(wrap) wrap.style.display = "none";
      if(ul) ul.innerHTML = "";
    }
  }

  function schedulePreview(){
    if(previewTimer) clearTimeout(previewTimer);
    previewTimer = setTimeout(previewNlpIfNeeded, 350);
  }

  async function loadEventListForDate(dateStr){
    const res = await fetch(apiBase + "/events");
    const events = await res.json();

    const ul = document.getElementById("events-ul");
    const countEl = document.getElementById("selected-count-label");
    ul.innerHTML = "";

    const dayEvents = events.filter(ev => (ev.start || "").slice(0,10) === dateStr);
    if(countEl) countEl.textContent = dayEvents.length ? `${dayEvents.length}개` : "없음";

    if(dayEvents.length === 0){
      ul.innerHTML = "<li style='padding:10px 6px; color:var(--muted); font-weight:800;'>선택한 날짜에 일정이 없습니다.</li>";
      return;
    }

    for(const ev of dayEvents){
      const li = document.createElement("li");

      const dot = document.createElement("div");
      dot.className = "event-dot";

      const info = document.createElement("div");
      info.className = "event-info";

      const title = document.createElement("div");
      title.className = "event-title";
      title.textContent = ev.title;

      const meta = document.createElement("div");
      meta.className = "event-meta";
      const timePart = ev.start.slice(11,16);
      meta.textContent = ev.location ? `시작 ${timePart} · ${ev.location}` : `시작 ${timePart}`;

      info.appendChild(title);
      info.appendChild(meta);

      const delBtn = document.createElement("button");
      delBtn.className = "delete-btn";
      delBtn.textContent = "삭제";
      delBtn.onclick = async () => {
        await fetch(apiBase + "/events/" + ev.id, { method:"DELETE" });
        await refreshAll();
      };

      li.appendChild(dot);
      li.appendChild(info);
      li.appendChild(delBtn);
      ul.appendChild(li);
    }
  }

  async function refreshAll(){
    if(calendar) calendar.refetchEvents();
    if(selectedDateStr) await loadEventListForDate(selectedDateStr);
  }

  async function createEvent(e){
    e.preventDefault();

    const title = document.getElementById("title").value.trim();
    const start = document.getElementById("start").value;
    const end = document.getElementById("end").value;
    const location = document.getElementById("location").value.trim();

    if(!title || !start){
      alert("제목과 시작 시각은 필수입니다.");
      return;
    }

    const payload = { title, start, end: end || null, location: location || null };

    await fetch(apiBase + "/events", {
      method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify(payload)
    });

    document.getElementById("event-form").reset();
    await refreshAll();
  }

  function setUnifiedMode(isDelete){
    const btn = document.getElementById("nlp-action-btn");
    const loaderEl = document.querySelector("#nlp-unified-loader .loader");
    if(!btn) return;

    btn.classList.toggle("mode-delete", isDelete);
    btn.classList.toggle("mode-add", !isDelete);

    if(loaderEl){
      loaderEl.classList.toggle("is-delete", isDelete);
    }

    previewNlpIfNeeded();
  }

  // -------- Confirm Modal helpers --------
  function openConfirm(){ document.getElementById("confirm-overlay").style.display = "flex"; }
  function closeConfirm(){
    document.getElementById("confirm-overlay").style.display = "none";
    document.getElementById("confirm-list").innerHTML = "";
    confirmState = { mode: null, addItems: [], deleteGroups: [] };
  }

  async function openAddConfirm(text){
    const res = await fetch(apiBase + "/nlp-preview", {
      method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify({ text })
    });
    if(!res.ok){
      alert("추가할 일정을 해석하지 못했습니다.");
      return;
    }
    const data = await res.json();
    const items = Array.isArray(data?.items) ? data.items : [];
    if(items.length === 0){
      alert("추가할 일정을 찾지 못했습니다.");
      return;
    }

    confirmState.mode = "add";
    confirmState.addItems = items;

    document.getElementById("confirm-title").textContent = "이 일정을 추가할까요?";
    document.getElementById("confirm-desc").textContent = "체크한 항목만 추가됩니다. 반복 일정은 묶어서 선택합니다.";

    const host = document.getElementById("confirm-list");
    host.innerHTML = "";

    items.forEach((it, idx) => {
      const row = document.createElement("div");
      row.className = "cm-row";

      const top = document.createElement("div");
      top.className = "cm-row-top";

      const left = document.createElement("div");
      left.className = "cm-left";

      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = true;
      cb.className = "cm-check";
      cb.dataset.addIndex = String(idx);

      const main = document.createElement("div");
      main.className = "cm-main";

      const line1 = document.createElement("div");
      line1.className = "cm-line1";
      line1.textContent = it.title || "(제목 없음)";

      const line2 = document.createElement("div");
      line2.className = "cm-line2";

      if(it.type === "single"){
        line2.textContent = fmtRange(it.start, it.end) + (it.location ? ` · ${it.location}` : "");
      }else{
        const sd = it.start_date || "?";
        const ed = it.end_date || "?";
        const cnt = (typeof it.count === "number") ? it.count : 0;
        const time = it.time ? it.time : "시간 없음";
        line2.textContent = `반복: ${sd}~${ed} · ${time} · ${cnt}회` + (it.location ? ` · ${it.location}` : "");

        if(Array.isArray(it.samples) && it.samples.length){
          const mini = document.createElement("div");
          mini.className = "cm-mini";
          const s = it.samples.slice(0,3).map(x => (x || "").replace("T"," ")).join(" / ");
          mini.textContent = `예: ${s}${(cnt > 3) ? " …" : ""}`;
          main.appendChild(mini);
        }
      }

      main.appendChild(line1);
      main.appendChild(line2);

      left.appendChild(cb);
      left.appendChild(main);

      top.appendChild(left);
      row.appendChild(top);
      host.appendChild(row);
    });

    openConfirm();
  }

  async function openDeleteConfirm(text){
    const res = await fetch(apiBase + "/nlp-delete-preview", {
      method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify({ text })
    });
    if(!res.ok){
      alert("삭제할 일정을 찾지 못했습니다.");
      return;
    }
    const data = await res.json();
    const groups = Array.isArray(data?.groups) ? data.groups : [];
    if(groups.length === 0){
      alert("삭제할 일정을 찾지 못했습니다.");
      return;
    }

    confirmState.mode = "delete";
    confirmState.deleteGroups = groups;

    document.getElementById("confirm-title").textContent = "이 일정을 삭제할까요?";
    document.getElementById("confirm-desc").textContent = "체크한 항목만 삭제됩니다. 반복 일정은 묶어서 선택할 수 있습니다.";

    const host = document.getElementById("confirm-list");
    host.innerHTML = "";

    groups.forEach((g, gi) => {
      const row = document.createElement("div");
      row.className = "cm-row";

      const top = document.createElement("div");
      top.className = "cm-row-top";

      const left = document.createElement("div");
      left.className = "cm-left";

      const gcb = document.createElement("input");
      gcb.type = "checkbox";
      gcb.checked = true;
      gcb.className = "cm-check";
      gcb.dataset.groupIndex = String(gi);

      const main = document.createElement("div");
      main.className = "cm-main";

      const line1 = document.createElement("div");
      line1.className = "cm-line1";
      const kindLabel = (g.kind === "recurring") ? "반복" : "단일";
      line1.textContent = `${kindLabel} · ${g.title || ""}`;

      const line2 = document.createElement("div");
      line2.className = "cm-line2";
      const time = g.time ? g.time : "";
      const loc = g.location ? g.location : "";
      const cnt = (typeof g.count === "number") ? g.count : (Array.isArray(g.ids) ? g.ids.length : 0);
      line2.textContent = `${time}${time && loc ? " · " : ""}${loc}${(time || loc) ? " · " : ""}${cnt}개`;

      main.appendChild(line1);
      main.appendChild(line2);

      left.appendChild(gcb);
      left.appendChild(main);

      const toggle = document.createElement("button");
      toggle.type = "button";
      toggle.className = "cm-toggle";
      toggle.textContent = "상세";

      const sub = document.createElement("div");
      sub.className = "cm-sublist";

      const items = Array.isArray(g.items) ? g.items : [];
      items.forEach((it) => {
        const si = document.createElement("div");
        si.className = "cm-subitem";

        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = true;
        cb.dataset.deleteId = String(it.id);

        const meta = document.createElement("div");
        meta.style.minWidth = "0";

        const l1 = document.createElement("div");
        l1.className = "cm-line1";
        l1.textContent = it.title || "";

        const l2 = document.createElement("div");
        l2.className = "cm-line2";
        l2.textContent = fmtRange(it.start, it.end) + (it.location ? ` · ${it.location}` : "");

        meta.appendChild(l1);
        meta.appendChild(l2);

        si.appendChild(cb);
        si.appendChild(meta);
        sub.appendChild(si);
      });

      gcb.addEventListener("change", () => {
        sub.querySelectorAll("input[type=checkbox][data-delete-id]").forEach(x => {
          x.checked = gcb.checked;
          x.indeterminate = false;
        });
      });

      sub.addEventListener("change", () => {
        const cbs = Array.from(sub.querySelectorAll("input[type=checkbox][data-delete-id]"));
        const all = cbs.every(x => x.checked);
        const any = cbs.some(x => x.checked);
        gcb.checked = any;
        gcb.indeterminate = any && !all;
      });

      toggle.addEventListener("click", () => {
        const open = sub.style.display === "block";
        sub.style.display = open ? "none" : "block";
        toggle.textContent = open ? "상세" : "접기";
      });

      top.appendChild(left);
      top.appendChild(toggle);

      row.appendChild(top);
      row.appendChild(sub);
      host.appendChild(row);
    });

    openConfirm();
  }

  // ✅ 실행 버튼/Enter: 바로 추가/삭제 X → 확인 모달 오픈
  async function runUnifiedNlpAction(){
    const input = document.getElementById("nlp-unified-text");
    const toggle = document.getElementById("nlp-mode-toggle");
    const text = input?.value?.trim() ?? "";
    if(!text){
      alert("문장을 입력해주세요.");
      return;
    }

    const isDelete = !!toggle?.checked;
    if(isDelete){
      await openDeleteConfirm(text);
    }else{
      await openAddConfirm(text);
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    // Calendar init
    const calendarEl = document.getElementById("calendar");
    selectedDateStr = toDateStrLocal(new Date());
    setSelectedDate(selectedDateStr);

    calendar = new FullCalendar.Calendar(calendarEl, {
      initialView:"dayGridMonth",
      locale:"ko",
      height:"auto",
      headerToolbar:false,
      fixedWeekCount:false,
      dayMaxEventRows:4,

      dayCellClassNames: (arg) => {
        const ds = toDateStrLocal(arg.date);
        return (ds === selectedDateStr) ? ["selected-day"] : [];
      },
      dayHeaderClassNames: (arg) => {
        const ds = toDateStrLocal(arg.date);
        return (ds === selectedDateStr) ? ["selected-day-header"] : [];
      },

      events: async (info, success, failure) => {
        try{
          const res = await fetch(apiBase + "/events");
          const data = await res.json();
          success(data.map(ev => ({
            id:String(ev.id),
            title:ev.title,
            start:ev.start,
            end:ev.end || null,
            extendedProps:{ location: ev.location || "" }
          })));
        }catch(err){
          console.error(err);
          failure(err);
        }
      },

      dateClick: (info) => {
        setSelectedDate(info.dateStr);
        loadEventListForDate(selectedDateStr);

        const startInput = document.getElementById("start");
        startInput.value = info.dateStr + "T09:00";
        startInput.focus();
      },

      eventClick: (info) => {
        const ev = info.event;
        const startStr = ev.start ? ev.start.toLocaleString() : "";
        const endStr = ev.end ? ev.end.toLocaleString() : "";
        const loc = ev.extendedProps.location;
        let msg = `제목: ${ev.title}\\n시작: ${startStr}`;
        if(endStr) msg += `\\n종료: ${endStr}`;
        if(loc) msg += `\\n장소: ${loc}`;
        alert(msg);
      },

      datesSet: (info) => {
        updateYearMonthLabel(info.view.calendar.getDate());
        setActiveView(info.view.type);
      }
    });

    calendar.render();
    updateYearMonthLabel(calendar.getDate());
    setActiveView(calendar.view.type);

    // initial right list
    loadEventListForDate(selectedDateStr);

    // topbar controls
    document.getElementById("cal-prev").addEventListener("click", () => calendar.prev());
    document.getElementById("cal-next").addEventListener("click", () => calendar.next());
    document.getElementById("cal-today").addEventListener("click", () => {
      calendar.today();
      const d = toDateStrLocal(new Date());
      setSelectedDate(d);
      loadEventListForDate(d);
    });

    document.querySelectorAll("[data-cal-view]").forEach(btn => {
      btn.addEventListener("click", () => {
        const view = btn.dataset.calView;
        calendar.changeView(view);
        setActiveView(view);
      });
    });

    // quick add
    document.getElementById("event-form").addEventListener("submit", createEvent);

    // NLP auto-grow + preview
    setupShadowAutoGrow("nlp-unified-text");

    const toggle = document.getElementById("nlp-mode-toggle");
    const actionBtn = document.getElementById("nlp-action-btn");

    setUnifiedMode(toggle.checked);
    toggle.addEventListener("change", () => setUnifiedMode(toggle.checked));
    actionBtn.addEventListener("click", runUnifiedNlpAction);

    const ta = document.getElementById("nlp-unified-text");
    ta.addEventListener("input", schedulePreview);

    ta.addEventListener("keydown", (e) => {
      if(e.key === "Enter" && !e.shiftKey){
        e.preventDefault();
        runUnifiedNlpAction();
      }
    });

    // pill placeholders
    function setupPillPlaceholder(inputId){
      const input = document.getElementById(inputId);
      const wrap = input?.closest(".pill-field");
      const ph = wrap?.querySelector(".pill-placeholder");
      if(!input || !ph) return;

      const update = () => {
        if(input.value && input.value.trim() !== ""){
          ph.classList.add("hidden");
          input.classList.add("has-value");
        }else{
          ph.classList.remove("hidden");
          input.classList.remove("has-value");
        }
      };
      input.addEventListener("input", update);
      input.addEventListener("change", update);
      update();
    }
    setupPillPlaceholder("start");
    setupPillPlaceholder("end");
    setupPillPlaceholder("location");

    // confirm modal bindings
    document.getElementById("confirm-close").addEventListener("click", closeConfirm);
    document.getElementById("confirm-cancel").addEventListener("click", closeConfirm);
    document.getElementById("confirm-overlay").addEventListener("click", (e) => {
      if(e.target && e.target.id === "confirm-overlay") closeConfirm();
    });

    document.getElementById("confirm-ok").addEventListener("click", async () => {
      if(confirmState.mode === "add"){
        const chosenIdx = Array.from(document.querySelectorAll("input[type=checkbox][data-add-index]"))
          .filter(x => x.checked)
          .map(x => parseInt(x.dataset.addIndex, 10));

        const selected = chosenIdx.map(i => confirmState.addItems[i]).filter(Boolean);
        if(selected.length === 0){
          alert("추가할 항목을 선택해주세요.");
          return;
        }

        const res = await fetch(apiBase + "/nlp-apply-add", {
          method:"POST",
          headers:{ "Content-Type":"application/json" },
          body: JSON.stringify({ items: selected })
        });

        if(!res.ok){
          alert("일정 추가 적용에 실패했습니다.");
          return;
        }

        closeConfirm();

        const input = document.getElementById("nlp-unified-text");
        input.value = "";
        input.dispatchEvent(new Event("input"));

        const pv = document.getElementById("nlp-preview");
        const pvul = document.getElementById("nlp-preview-ul");
        if(pv) pv.style.display = "none";
        if(pvul) pvul.innerHTML = "";

        await refreshAll();
        return;
      }

      if(confirmState.mode === "delete"){
        const ids = Array.from(document.querySelectorAll("input[type=checkbox][data-delete-id]"))
          .filter(x => x.checked)
          .map(x => parseInt(x.dataset.deleteId, 10))
          .filter(n => Number.isFinite(n));

        if(ids.length === 0){
          alert("삭제할 항목을 선택해주세요.");
          return;
        }

        const res = await fetch(apiBase + "/delete-by-ids", {
          method:"POST",
          headers:{ "Content-Type":"application/json" },
          body: JSON.stringify({ ids })
        });

        if(!res.ok){
          alert("일정 삭제 적용에 실패했습니다.");
          return;
        }

        closeConfirm();

        const input = document.getElementById("nlp-unified-text");
        input.value = "";
        input.dispatchEvent(new Event("input"));

        await refreshAll();
        return;
      }
    });
  });
</script>
</body>
</html>
"""


def build_header_actions(request: Request) -> str:
  admin = is_admin(request)
  token = load_gcal_token() is not None

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

  actions_html = build_header_actions(request)
  html = CALENDAR_HTML.replace("__HEADER_ACTIONS__", actions_html)
  return HTMLResponse(html)
