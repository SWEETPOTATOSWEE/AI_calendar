from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request
from openai import AsyncOpenAI

from .config import (
    OPENAI_API_KEY,
    LLM_DEBUG,
    SEOUL,
    ISO_DATE_RE,
    MAX_CONTEXT_DAYS,
    DEFAULT_CONTEXT_DAYS,
    MAX_CONTEXT_EVENTS,
    MAX_CONTEXT_SLICES,
    MAX_CONTEXT_DATES,
    ALLOWED_REASONING_EFFORTS,
    ALLOWED_ASSISTANT_MODELS,
    DEFAULT_TEXT_MODEL,
    DEFAULT_MULTIMODAL_MODEL,
    DEFAULT_TEXT_REASONING_EFFORT,
    DEFAULT_MULTIMODAL_REASONING_EFFORT,
    USD_TO_KRW,
    MODEL_PRICING,
)
from .utils import (_log_debug, normalize_text, _event_within_scope)
from . import state
from .gcal import fetch_google_events_between, _get_context_cache, _set_context_cache, _should_use_cached_context

async_client: Optional[AsyncOpenAI] = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
inflight_tasks: Dict[str, Dict[str, asyncio.Task]] = {}
inflight_lock = asyncio.Lock()


def get_async_client() -> AsyncOpenAI:
  if async_client is None:
    raise RuntimeError("OPENAI_API_KEY is not set")
  return async_client

# -------------------------
# LLM 프롬프트
# -------------------------
REQUEST_CLASSIFY_PROMPT = """너는 일정 요청 분류기다. 반드시 JSON 한 개만 반환한다. 설명 금지.
입력 형식:
{
  "request": string,
  "has_images": boolean
}

출력 스키마:
{
  "type": "add" | "delete" | "complex" | "garbage"
}

규칙:
1. add: 일정 추가/생성/등록/예약/미팅 잡기/일정 넣기 등 추가 요청만 포함.
2. delete: 일정 삭제/취소/제거/빼기/없애기 등 삭제 요청만 포함.
3. complex: 추가와 삭제가 함께 있거나 둘 다 명확히 요구됨.
4. garbage: 일정 추가/삭제와 무관, 의미 불명, 또는 수정/조회/이동/변경처럼 추가·삭제가 아닌 요청.
5. 판단이 모호하면 garbage.
"""

EVENTS_SYSTEM_PROMPT_TEMPLATE = """너는 한국어 일정 문장을 구조화하는 파서다. 반드시 JSON 한 개만 반환한다. 설명 금지.
기준 정보:
- 기준 날짜: {TODAY}
- 시간대: Asia/Seoul

출력 스키마:
{
  "needs_context": true | false,
  "context_dates": ["YYYY-MM-DD"],
  "context_slices": [
    {
      "start_date": "YYYY-MM-DD",
      "end_date": "YYYY-MM-DD"
    }
  ],
  "need_more_information": true | false,
  "content": string,
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
      "time": "HH:MM" | null,
      "duration_minutes": number | null,
      "location": string | null,
      "recurrence": {
        "freq": "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY",
        "interval": number | null,
        "byweekday": [0,1,2,3,4,5,6] | null,
        "bymonthday": [1..31, -1] | null,
        "bysetpos": number | null,
        "bymonth": [1..12] | null,
        "end": {
          "until": "YYYY-MM-DD" | null,
          "count": number | null
        } | null
      },
      "end_date": "YYYY-MM-DD" | null,
      "weekdays": [0,1,2,3,4,5,6] | null
    }
  ]
}

우선 규칙(1이 2에 우선함):
0. 필요하면 존재하는 일정 정보를 불러올 수 있으며 need_more_information보다 needs_context를 우선으로 사용한다.
1. 이미 존재하는 일정 정보가 필요하면 needs_context=true, need_more_information=false, items=[]로 바로 반환한다.
  - 특정 날짜만 필요하면 context_dates 배열로 요청한다.
  - 범위가 필요하면 context_slices 배열로 요청한다.
2. 이미 존재하는 일정 정보는 필요 없고 사용자에게 추가 질문이 필요하면 need_more_information=true, content에 질문만 작성, 이때 items=[], needs_context=false로 둔다.

규칙:
1. 반복은 recurrence를 우선 사용한다
2. 여러 일정이면 single을 여러 개로 만들고, 반복이 있으면 recurring을 사용하며 혼합이면 둘 다 포함한다.
3. weekdays: 0=월요일 … 6=일요일
4. 입력에 사용자:/assistant: 대화가 섞일 수 있으니 전체 대화를 참고해 요청을 해석한다.
5. title은 시간/장소를 넣지 않는다
6. 상대 날짜는 기준 날짜로 계산한다.
7. 시간 정보가 없으면 recurring.time과 recurring.duration_minutes는 null.
8. 종일 일정이 명확하면 질문하지 않는다. 휴가/연차/휴무/기념일/생일/공휴일 등.
9. recurrence.end는 until 또는 count 중 하나만 사용한다(동시 사용 금지).
10. 사용자의 요청이 없다면 과거 이벤트는 생성하지 않는다.
11. recurrence가 있으면 우선 사용한다. end_date/weekday는 이전 버전 호환용으로만 사용(필수 아님).
12. need_more_information=false이면 content는 빈 문자열로 둔다.
13. 마크다운은 need_more_information=true인 경우 content에서만 제한적으로 사용한다(지원 형식: 제목(#), **굵게**, *기울임*, ~~취소선~~, `인라인 코드`, ```코드 블록```, 리스트(-, 1.), 인용구(>), 구분선(---), 줄바꿈). 그 외 필드에는 마크다운을 쓰지 않는다.
14. needs_context와 need_more_information은 동시에 true로 두지 않는다.
15. 질문은 최대 3개까지만 작성한다.

"""


def build_events_system_prompt() -> str:
  today = datetime.now(SEOUL).date().isoformat()
  return EVENTS_SYSTEM_PROMPT_TEMPLATE.replace("{TODAY}", today)


EVENTS_SYSTEM_PROMPT_WITH_CONTEXT_TEMPLATE = """너는 한국어 일정 문장을 구조화하는 파서다. 반드시 JSON 한 개만 반환한다. 설명 금지.
입력 형식:
{
  "request": string,
  "has_images": boolean,
  "context": {
    "today": "YYYY-MM-DD",
    "timezone": "Asia/Seoul",
    "scope": {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"},
    "scopes": [{"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}],
    "dates": ["YYYY-MM-DD"],
    "events": [
      {
        "id": number,
        "title": string,
        "start": "YYYY-MM-DDTHH:MM",
        "end": "YYYY-MM-DDTHH:MM" | null,
        "location": string | null,
        "recur": "recurring" | null,
        "all_day": boolean
      }
    ]
  }
}

출력 스키마:
{
  "need_more_information": true | false,
  "content": string,
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
      "time": "HH:MM" | null,
      "duration_minutes": number | null,
      "location": string | null,
      "recurrence": {
        "freq": "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY",
        "interval": number | null,
        "byweekday": [0,1,2,3,4,5,6] | null,
        "bymonthday": [1..31, -1] | null,
        "bysetpos": number | null,
        "bymonth": [1..12] | null,
        "end": {
          "until": "YYYY-MM-DD" | null,
          "count": number | null
        } | null
      },
      "end_date": "YYYY-MM-DD" | null,
      "weekdays": [0,1,2,3,4,5,6] | null
    }
  ]
}

우선 규칙:
1. 사용자에게 추가 질문이 필요하면 need_more_information=true, content에 질문만 작성하고 items=[]로 둔다.

규칙:
1. 반복은 recurrence를 우선 사용한다
2. 여러 일정이면 single을 여러 개로 만들고, 반복이 있으면 recurring을 사용하며 혼합이면 둘 다 포함한다.
3. weekdays: 0=월요일 … 6=일요일
4. 입력에 사용자:/assistant: 대화가 섞일 수 있으니 전체 대화를 참고해 요청을 해석한다.
5. title은 시간/장소를 넣지 않는다
6. 상대 날짜는 기준 날짜로 계산한다.
7. 시간 정보가 없으면 recurring.time과 recurring.duration_minutes는 null.
8. 종일 일정이 명확하면 질문하지 않는다. 휴가/연차/휴무/기념일/생일/공휴일 등.
9. recurrence.end는 until 또는 count 중 하나만 사용한다(동시 사용 금지).
10. 사용자의 요청이 없다면 과거 이벤트는 생성하지 않는다.
11. recurrence가 있으면 우선 사용한다. end_date/weekday는 이전 버전 호환용으로만 사용(필수 아님)
12. context.events는 이미 존재하는 일정이다. 요청이 기존 일정과의 관계(직후/직전/같은 시간/충돌 회피 등)를 요구할 때만 참고한다.
13. context.events 자체를 수정하거나 삭제하지 말고, 새로 추가할 일정만 만든다.
14. 이미지 또는 텍스트에 정보가 없으면 null 처리
15. 가려진 구간이나 알아볼 수 없는 내용은 제외
16. 꼭 필요한 정보만 질문한다. 문맥이나 context.events로 알 수 있으면 묻지 않는다.
17. need_more_information=false이면 content는 빈 문자열로 둔다.
18. 마크다운은 need_more_information=true인 경우 content에서만 제한적으로 사용한다(지원 형식: 제목(#), **굵게**, *기울임*, ~~취소선~~, `인라인 코드`, ```코드 블록```, 리스트(-, 1.), 인용구(>), 구분선(---), 줄바꿈). 그 외 필드에는 마크다운을 쓰지 않는다.
19. 질문은 최대 3개까지만 작성한다.

"""


def build_events_system_prompt_with_context() -> str:
  return EVENTS_SYSTEM_PROMPT_WITH_CONTEXT_TEMPLATE


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
  "needs_context": true | false,
  "context_dates": ["YYYY-MM-DD"],
  "context_slices": [
    {
      "start_date": "YYYY-MM-DD",
      "end_date": "YYYY-MM-DD"
    }
  ],
  "need_more_information": true | false,
  "content": string,
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
      "time": "HH:MM" | null,
      "duration_minutes": number | null,
      "location": string | null,
      "recurrence": {
        "freq": "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY",
        "interval": number | null,
        "byweekday": [0,1,2,3,4,5,6] | null,
        "bymonthday": [1..31, -1] | null,
        "bysetpos": number | null,
        "bymonth": [1..12] | null,
        "end": {
          "until": "YYYY-MM-DD" | null,
          "count": number | null
        } | null
      },
      "end_date": "YYYY-MM-DD" | null,
      "weekdays": [0,1,2,3,4,5,6] | null
    }
  ]
}

우선 규칙(1이 2에 우선함):
0. 필요하면 존재하는 일정 정보를 불러올 수 있으며 need_more_information보다 needs_context를 우선으로 사용한다.
1. 이미 존재하는 일정 정보가 필요하면 needs_context=true, need_more_information=false, items=[]로 바로 반환한다.
  - 특정 날짜만 필요하면 context_dates 배열로 요청한다.
  - 범위가 필요하면 context_slices 배열로 요청한다.
2. 이미 존재하는 일정 정보는 필요 없고 사용자에게 추가 질문이 필요하면 need_more_information=true, content에 질문만 작성, 이때 items=[], needs_context=false로 둔다.

규칙:
1. 반복은 recurrence를 우선 사용한다
2. 여러 일정이면 single을 여러 개로 만들고, 반복이 있으면 recurring을 사용하며 혼합이면 둘 다 포함한다.
3. weekdays: 0=월요일 … 6=일요일
4. 입력에 사용자:/assistant: 대화가 섞일 수 있으니 전체 대화를 참고해 요청을 해석한다.
5. title은 시간/장소를 넣지 않는다
6. 상대 날짜는 기준 날짜로 계산한다.
7. 시간 정보가 없으면 recurring.time과 recurring.duration_minutes는 null.
8. 종일 일정이 명확하면 질문하지 않는다. 휴가/연차/휴무/기념일/생일/공휴일 등.
9. recurrence.end는 until 또는 count 중 하나만 사용한다(동시 사용 금지).
10. 사용자의 요청이 없다면 과거 이벤트는 생성하지 않는다.
11. recurrence가 있으면 우선 사용한다. end_date/weekday는 이전 버전 호환용으로만 사용(필수 아님).
12. 이미지 또는 텍스트에 정보가 없으면 null 처리
13. 가려진 구간이나 알아볼 수 없는 내용은 제외
14. need_more_information=false이면 content는 빈 문자열로 둔다.
15. 마크다운은 need_more_information=true인 경우 content에서만 제한적으로 사용한다(지원 형식: 제목(#), **굵게**, *기울임*, ~~취소선~~, `인라인 코드`, ```코드 블록```, 리스트(-, 1.), 인용구(>), 구분선(---), 줄바꿈). 그 외 필드에는 마크다운을 쓰지 않는다.
16. needs_context와 need_more_information은 동시에 true로 두지 않는다.
17. 질문은 최대 3개까지만 작성한다.

"""


def build_events_multimodal_prompt() -> str:
  today = datetime.now(SEOUL).date().isoformat()
  return EVENTS_MULTIMODAL_PROMPT_TEMPLATE.replace("{TODAY}", today)


EVENTS_MULTIMODAL_PROMPT_WITH_CONTEXT_TEMPLATE = """너는 한국어 일정 정보를 텍스트와 이미지에서 구조화하는 파서다. 반드시 JSON 한 개만 반환한다. 설명 금지.
입력 특징:
- 사용자가 항공권/티켓/메신저/시간표 등 캡처 이미지를 제공한다.
- 검은 박스로 가린 영역이나 알아볼 수 없는 부분은 추측하지 말고 무시한다.
- 텍스트 설명이 함께 있을 수 있으므로 반드시 모두 참고한다.

입력 형식:
{
  "request": string,
  "has_images": boolean,
  "context": {
    "today": "YYYY-MM-DD",
    "timezone": "Asia/Seoul",
    "scope": {"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"},
    "scopes": [{"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}],
    "dates": ["YYYY-MM-DD"],
    "events": [
      {
        "id": number,
        "title": string,
        "start": "YYYY-MM-DDTHH:MM",
        "end": "YYYY-MM-DDTHH:MM" | null,
        "location": string | null,
        "recur": "recurring" | null,
        "all_day": boolean
      }
    ]
  }
}

출력 스키마:
{
  "need_more_information": true | false,
  "content": string,
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
      "time": "HH:MM" | null,
      "duration_minutes": number | null,
      "location": string | null,
      "recurrence": {
        "freq": "DAILY" | "WEEKLY" | "MONTHLY" | "YEARLY",
        "interval": number | null,
        "byweekday": [0,1,2,3,4,5,6] | null,
        "bymonthday": [1..31, -1] | null,
        "bysetpos": number | null,
        "bymonth": [1..12] | null,
        "end": {
          "until": "YYYY-MM-DD" | null,
          "count": number | null
        } | null
      },
      "end_date": "YYYY-MM-DD" | null,
      "weekdays": [0,1,2,3,4,5,6] | null
    }
  ]
}

우선 규칙:
1. 사용자에게 추가 질문이 필요하면 need_more_information=true, content에 질문만 작성하고 items=[]로 둔다.

규칙:
1. 반복은 recurrence를 우선 사용한다
2. 여러 일정이면 single을 여러 개로 만들고, 반복이 있으면 recurring을 사용하며 혼합이면 둘 다 포함한다.
3. weekdays: 0=월요일 … 6=일요일
4. 입력에 사용자:/assistant: 대화가 섞일 수 있으니 전체 대화를 참고해 요청을 해석한다.
5. title은 시간/장소를 넣지 않는다
6. 상대 날짜는 기준 날짜로 계산한다.
7. 시간 정보가 없으면 recurring.time과 recurring.duration_minutes는 null.
8. 종일 일정이 명확하면 질문하지 않는다. 휴가/연차/휴무/기념일/생일/공휴일 등.
9. recurrence.end는 until 또는 count 중 하나만 사용한다(동시 사용 금지).
10. 사용자의 요청이 없다면 과거 이벤트는 생성하지 않는다.
11. recurrence가 있으면 우선 사용한다. end_date/weekday는 이전 버전 호환용으로만 사용(필수 아님)
12. context.events는 이미 존재하는 일정이다. 요청이 기존 일정과의 관계(직후/직전/같은 시간/충돌 회피 등)를 요구할 때만 참고한다.
13. context.events 자체를 수정하거나 삭제하지 말고, 새로 추가할 일정만 만든다.
14. 이미지 또는 텍스트에 정보가 없으면 null 처리
15. 가려진 구간이나 알아볼 수 없는 내용은 제외
16. 꼭 필요한 정보만 질문한다. 문맥이나 context.events로 알 수 있으면 묻지 않는다.
17. need_more_information=false이면 content는 빈 문자열로 둔다.
18. 마크다운은 need_more_information=true인 경우 content에서만 제한적으로 사용한다(지원 형식: 제목(#), **굵게**, *기울임*, ~~취소선~~, `인라인 코드`, ```코드 블록```, 리스트(-, 1.), 인용구(>), 구분선(---), 줄바꿈). 그 외 필드에는 마크다운을 쓰지 않는다.
19. 질문은 최대 3개까지만 작성한다.

"""


def build_events_multimodal_prompt_with_context() -> str:
  return EVENTS_MULTIMODAL_PROMPT_WITH_CONTEXT_TEMPLATE


def _build_events_user_payload(text: str,
                               has_images: bool,
                               context: Optional[Dict[str, Any]] = None
                               ) -> str:
  if context is not None:
    payload: Dict[str, Any] = {
        "request": text or "",
        "has_images": bool(has_images),
        "context": context,
    }
    return json.dumps(payload, ensure_ascii=False)

  lines = []
  if text:
    lines.append(f"문장: {text}")
  else:
    lines.append("문장: (제공되지 않음)")
  if has_images:
    lines.append("첨부 이미지를 참고해서 일정 정보를 추출해줘.")
  return "\n".join(lines)


async def classify_nlp_request(text: str,
                               has_images: bool = False,
                               model_name: Optional[str] = None) -> str:
  if async_client is None:
    raise HTTPException(
        status_code=500,
        detail="LLM client is not configured (OPENAI_API_KEY 미설정)")

  payload = {
      "request": normalize_text(text),
      "has_images": bool(has_images),
  }
  data = await _chat_json("classify",
                          REQUEST_CLASSIFY_PROMPT,
                          json.dumps(payload, ensure_ascii=False),
                          reasoning_effort="low",
                          model_name=model_name or "gpt-5-nano")
  value = (data.get("type") or "").strip().lower()
  if value not in ("add", "delete", "complex", "garbage"):
    return "garbage"
  return value


def _sanitize_reasoning_effort(value: Optional[str]) -> Optional[str]:
  if not value or not isinstance(value, str):
    return None
  normalized = value.strip().lower()
  if normalized in ALLOWED_REASONING_EFFORTS:
    return normalized
  return None


def _sanitize_model(value: Optional[str]) -> Optional[str]:
  if not value or not isinstance(value, str):
    return None
  normalized = value.strip().lower()
  if normalized in ALLOWED_ASSISTANT_MODELS:
    return ALLOWED_ASSISTANT_MODELS[normalized]
  if normalized in ALLOWED_ASSISTANT_MODELS.values():
    return normalized
  return None


def _resolve_request_reasoning_effort(request: Request,
                                      requested: Optional[str]) -> Optional[str]:
  if not requested:
    return None
  return _sanitize_reasoning_effort(requested)


def _resolve_request_model(request: Request,
                           requested: Optional[str]) -> Optional[str]:
  if not requested:
    return None
  return _sanitize_model(requested)


def _pick_reasoning_effort(value: Optional[str], default_value: str) -> str:
  sanitized = _sanitize_reasoning_effort(value)
  return sanitized or default_value


def _sanitize_context_days(value: Any) -> int:
  try:
    days = int(value)
  except (TypeError, ValueError):
    return 0
  if days < 0:
    return 0
  return min(days, MAX_CONTEXT_DAYS)


def _parse_iso_date(value: Any) -> Optional[date]:
  if not isinstance(value, str):
    return None
  candidate = value.strip()
  if len(candidate) < 10:
    return None
  date_part = candidate[:10]
  if not ISO_DATE_RE.match(date_part):
    return None
  try:
    return datetime.strptime(date_part, "%Y-%m-%d").date()
  except Exception:
    return None


def _normalize_context_dates(value: Any) -> List[date]:
  if not isinstance(value, list):
    return []
  out: List[date] = []
  seen: set[str] = set()
  for raw in value:
    dt = _parse_iso_date(raw)
    if not dt:
      continue
    key = dt.isoformat()
    if key in seen:
      continue
    seen.add(key)
    out.append(dt)
    if len(out) >= MAX_CONTEXT_DATES:
      break
  return out


def _normalize_context_slices(value: Any) -> List[Tuple[date, date]]:
  if not isinstance(value, list):
    return []
  out: List[Tuple[date, date]] = []
  seen: set[Tuple[str, str]] = set()
  for raw in value:
    if not isinstance(raw, dict):
      continue
    start = _parse_iso_date(raw.get("start_date"))
    end = _parse_iso_date(raw.get("end_date"))
    if not start or not end:
      continue
    if end < start:
      continue
    if (end - start).days > MAX_CONTEXT_DAYS:
      continue
    key = (start.isoformat(), end.isoformat())
    if key in seen:
      continue
    seen.add(key)
    out.append((start, end))
    if len(out) >= MAX_CONTEXT_SLICES:
      break
  return out


def _parse_bool(value: Any) -> bool:
  if isinstance(value, bool):
    return value
  if isinstance(value, (int, float)):
    return value != 0
  if isinstance(value, str):
    return value.strip().lower() in ("true", "yes", "1")
  return False


def _extract_context_request(
    data: Dict[str, Any]) -> Tuple[bool, List[Tuple[date, date]]]:
  slices = _normalize_context_slices(data.get("context_slices"))
  dates = _normalize_context_dates(data.get("context_dates"))
  needs = _parse_bool(data.get("needs_context")) or bool(slices or dates)
  if not needs:
    return False, []

  scopes: List[Tuple[date, date]] = list(slices)
  for dt in dates:
    scopes.append((dt, dt))

  if scopes:
    return True, scopes

  has_before = "days_before" in data
  has_after = "days_after" in data
  days_before = _sanitize_context_days(data.get("days_before"))
  days_after = _sanitize_context_days(data.get("days_after"))

  if not has_before and not has_after:
    days_before = DEFAULT_CONTEXT_DAYS
    days_after = DEFAULT_CONTEXT_DAYS
  else:
    if not has_before:
      days_before = days_after
    if not has_after:
      days_after = days_before
    if days_before == 0 and days_after == 0:
      days_before = DEFAULT_CONTEXT_DAYS
      days_after = DEFAULT_CONTEXT_DAYS

  today = datetime.now(SEOUL).date()
  start_date = today - timedelta(days=days_before)
  end_date = today + timedelta(days=days_after)
  return True, [(start_date, end_date)]


def _build_events_context(scopes: List[Tuple[date, date]],
                          session_id: Optional[str] = None,
                          is_google: bool = False) -> Dict[str, Any]:
  today = datetime.now(SEOUL).date()
  if not scopes:
    start_date = today - timedelta(days=DEFAULT_CONTEXT_DAYS)
    end_date = today + timedelta(days=DEFAULT_CONTEXT_DAYS)
    scopes = [(start_date, end_date)]

  snapshot: List[Dict[str, Any]] = []
  seen_ids: set[str] = set()
  if is_google:
    if session_id:
      for scope in scopes:
        google_items = fetch_google_events_between(scope[0], scope[1], session_id)
        for item in google_items:
          raw_id = item.get("id")
          if not raw_id:
            continue
          id_key = str(raw_id)
          if id_key in seen_ids:
            continue
          seen_ids.add(id_key)
          snapshot.append({
              "id": raw_id,
              "title": item.get("title"),
              "start": item.get("start"),
              "end": item.get("end"),
              "location": item.get("location"),
              "recur": None,
              "all_day": item.get("all_day"),
          })
  else:
    for scope in scopes:
      for ev in state.events:
        if not _event_within_scope(ev, scope):
          continue
        id_key = str(ev.id)
        if id_key in seen_ids:
          continue
        seen_ids.add(id_key)
        snapshot.append({
            "id": ev.id,
            "title": ev.title,
            "start": ev.start,
            "end": ev.end,
            "location": ev.location,
            "recur": ev.recur,
            "all_day": ev.all_day,
        })

      rec_occurrences = state._collect_local_recurring_occurrences(scope=scope)
      for occ in rec_occurrences:
        id_key = str(occ.id)
        if id_key in seen_ids:
          continue
        seen_ids.add(id_key)
        snapshot.append({
            "id": occ.id,
            "title": occ.title,
            "start": occ.start,
            "end": occ.end,
            "location": occ.location,
            "recur": occ.recur,
            "all_day": occ.all_day,
        })

  snapshot.sort(key=lambda x: x.get("start") or "")
  if len(snapshot) > MAX_CONTEXT_EVENTS:
    snapshot = snapshot[:MAX_CONTEXT_EVENTS]

  scope_payload = [
      {
          "start_date": scope[0].isoformat(),
          "end_date": scope[1].isoformat(),
      } for scope in scopes
  ]
  date_payload = [
      scope[0].isoformat() for scope in scopes if scope[0] == scope[1]
  ]

  payload: Dict[str, Any] = {
      "today": today.isoformat(),
      "timezone": "Asia/Seoul",
      "events": snapshot,
      "scopes": scope_payload,
      "dates": date_payload,
  }
  if len(scope_payload) == 1:
    payload["scope"] = scope_payload[0]
  return payload


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


async def _invoke_event_parser(kind: str,
                               text: str,
                               images: List[str],
                               reasoning_effort: Optional[str] = None,
                               model_name: Optional[str] = None,
                               context_cache_key: Optional[str] = None,
                               context_session_id: Optional[str] = None,
                               context_confirmed: bool = False,
                               is_google: bool = False
                               ) -> Dict[str, Any]:
  payload = _build_events_user_payload(text, bool(images))
  cached_context = _get_context_cache(context_cache_key)
  if cached_context and _should_use_cached_context(text):
    payload_with_context = _build_events_user_payload(text, bool(images),
                                                      cached_context)
    if images:
      data = await _chat_multimodal_json(
          kind,
          build_events_multimodal_prompt_with_context(),
          payload_with_context,
          images,
          reasoning_effort=reasoning_effort,
          model_name=model_name)
    else:
      data = await _chat_json(kind,
                              build_events_system_prompt_with_context(),
                              payload_with_context,
                              reasoning_effort=reasoning_effort,
                              model_name=model_name)
    if isinstance(data, dict):
      data["context_used"] = True
    return data
  if images:
    data = await _chat_multimodal_json(kind,
                                       build_events_multimodal_prompt(),
                                       payload,
                                       images,
                                       reasoning_effort=reasoning_effort,
                                       model_name=model_name)
  else:
    data = await _chat_json(kind,
                            build_events_system_prompt(),
                            payload,
                            reasoning_effort=reasoning_effort,
                            model_name=model_name)

  needs_context, scopes = _extract_context_request(data)
  if not needs_context:
    if isinstance(data, dict):
      data["context_used"] = False
    return data

  # 유저 허락 단계 추가
  if not context_confirmed:
    return {
        "permission_required": True,
        "needs_context": True,
        "context_used": False,
    }

  context = _build_events_context(scopes, session_id=context_session_id, is_google=is_google)
  _set_context_cache(context_cache_key, context)
  payload_with_context = _build_events_user_payload(text, bool(images), context)

  if images:
    data = await _chat_multimodal_json(
        kind,
        build_events_multimodal_prompt_with_context(),
        payload_with_context,
        images,
        reasoning_effort=reasoning_effort,
        model_name=model_name)
  else:
    data = await _chat_json(kind,
                            build_events_system_prompt_with_context(),
                            payload_with_context,
                            reasoning_effort=reasoning_effort,
                            model_name=model_name)

  if isinstance(data, dict):
    data["context_used"] = True
  return data


def _extract_content_from_partial_json(json_str: str) -> Optional[str]:
  """불완전한 JSON에서 content 필드 값을 실시간으로 추출"""
  match = re.search(r'"content"\s*:\s*"', json_str)
  if not match:
    return None
  
  start_idx = match.end()
  content_value = ""
  escaped = False
  
  for i in range(start_idx, len(json_str)):
    char = json_str[i]
    if escaped:
      if char == "n":
        content_value += "\n"
      elif char == "r":
        content_value += "\r"
      elif char == "t":
        content_value += "\t"
      elif char == '"':
        content_value += '"'
      elif char == "\\":
        content_value += "\\"
      else:
        content_value += char
      escaped = False
    elif char == "\\":
      escaped = True
    elif char == '"':
      break
    else:
      content_value += char
  
  return content_value


async def _invoke_event_parser_stream(kind: str,
                                      text: str,
                                      images: List[str],
                                      reasoning_effort: Optional[str] = None,
                                      model_name: Optional[str] = None,
                                      context_cache_key: Optional[str] = None,
                                      context_session_id: Optional[str] = None,
                                      context_confirmed: bool = False,
                                      is_google: bool = False):
  payload = _build_events_user_payload(text, bool(images))
  cached_context = _get_context_cache(context_cache_key)

  if cached_context and _should_use_cached_context(text):
    payload_with_context = _build_events_user_payload(text, bool(images),
                                                      cached_context)
    yield {"type": "status", "context_used": True}
    
    started = time.perf_counter()
    sys_prompt = build_events_system_prompt_with_context()
    user_txt = payload_with_context
    model = _sanitize_model(model_name) or DEFAULT_TEXT_MODEL
    
    if images:
      sys_prompt = build_events_multimodal_prompt_with_context()
      stream = await _chat_multimodal_json_stream(
          kind,
          sys_prompt,
          payload_with_context,
          images,
          reasoning_effort=reasoning_effort,
          model_name=model_name)
      model = _sanitize_model(model_name) or DEFAULT_MULTIMODAL_MODEL
    else:
      stream = await _chat_json_stream(
          kind,
          sys_prompt,
          payload_with_context,
          reasoning_effort=reasoning_effort,
          model_name=model_name)
    
    full_content = ""
    prev_extracted_content = ""
    async for chunk in stream:
      delta = chunk.choices[0].delta.content
      if delta:
        full_content += delta
        yield {"type": "chunk", "delta": delta}
        extracted = _extract_content_from_partial_json(full_content)
        if extracted is not None and len(extracted) > len(prev_extracted_content):
          content_delta = extracted[len(prev_extracted_content):]
          prev_extracted_content = extracted
          print(f"[STREAM DEBUG CACHED] content_delta: {content_delta[:50]}...")
          yield {"type": "content_delta", "content_delta": content_delta}
    
    latency_ms = (time.perf_counter() - started) * 1000.0
    _debug_print(kind, user_txt, sys_prompt, full_content, latency_ms, model_name=model)
    return

  # 1st Pass: Check context
  started = time.perf_counter()
  sys_prompt = build_events_system_prompt()
  user_txt = payload
  model = _sanitize_model(model_name) or DEFAULT_TEXT_MODEL

  if images:
    sys_prompt = build_events_multimodal_prompt()
    stream = await _chat_multimodal_json_stream(
        kind,
        sys_prompt,
        payload,
        images,
        reasoning_effort=reasoning_effort,
        model_name=model_name)
    model = _sanitize_model(model_name) or DEFAULT_MULTIMODAL_MODEL
  else:
    stream = await _chat_json_stream(
        kind,
        sys_prompt,
        payload,
        reasoning_effort=reasoning_effort,
        model_name=model_name)

  full_content = ""
  prev_extracted_content = ""
  yield {"type": "status", "context_used": False}
  async for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
      full_content += delta
      yield {"type": "chunk", "delta": delta}
      extracted = _extract_content_from_partial_json(full_content)
      if extracted is not None and len(extracted) > len(prev_extracted_content):
        content_delta = extracted[len(prev_extracted_content):]
        prev_extracted_content = extracted
        print(f"[STREAM DEBUG] content_delta: {content_delta[:50]}...")
        yield {"type": "content_delta", "content_delta": content_delta}

  latency_ms = (time.perf_counter() - started) * 1000.0
  _debug_print(kind, user_txt, sys_prompt, full_content, latency_ms, model_name=model)

  data = _safe_json_loads(full_content)
  if isinstance(data, dict):
    data["context_used"] = False
  needs_context, scopes = _extract_context_request(data)

  if not needs_context:
    yield {"type": "data", "data": data}
    return

  if not context_confirmed:
    yield {
        "type": "permission_required",
        "permission_required": True,
        "needs_context": True,
        "context_used": False,
    }
    return

  # 2nd Pass: With Context
  context = _build_events_context(scopes, session_id=context_session_id, is_google=is_google)
  _set_context_cache(context_cache_key, context)
  payload_with_context = _build_events_user_payload(text, bool(images), context)

  yield {"type": "status", "context_used": True}
  yield {"type": "reset_buffer"}

  started = time.perf_counter()
  sys_prompt = build_events_system_prompt_with_context()
  user_txt = payload_with_context
  model = _sanitize_model(model_name) or DEFAULT_TEXT_MODEL

  if images:
    sys_prompt = build_events_multimodal_prompt_with_context()
    stream = await _chat_multimodal_json_stream(
        kind,
        sys_prompt,
        payload_with_context,
        images,
        reasoning_effort=reasoning_effort,
        model_name=model_name)
    model = _sanitize_model(model_name) or DEFAULT_MULTIMODAL_MODEL
  else:
    stream = await _chat_json_stream(
        kind,
        sys_prompt,
        payload_with_context,
        reasoning_effort=reasoning_effort,
        model_name=model_name)

  full_content = ""
  prev_extracted_content = ""
  async for chunk in stream:
    delta = chunk.choices[0].delta.content
    if delta:
      full_content += delta
      yield {"type": "chunk", "delta": delta}
      extracted = _extract_content_from_partial_json(full_content)
      if extracted is not None and len(extracted) > len(prev_extracted_content):
        content_delta = extracted[len(prev_extracted_content):]
        prev_extracted_content = extracted
        print(f"[STREAM DEBUG 2ND] content_delta: {content_delta[:50]}...")
        yield {"type": "content_delta", "content_delta": content_delta}
  
  latency_ms = (time.perf_counter() - started) * 1000.0
  _debug_print(kind, user_txt, sys_prompt, full_content, latency_ms, model_name=model)

  data = _safe_json_loads(full_content)
  if isinstance(data, dict):
    data["context_used"] = True
  yield {"type": "data", "data": data}


def build_delete_system_prompt() -> str:
  return ("역할: '기존 일정 목록'과 '삭제 요청 문장'을 보고 삭제할 일정 id 목록만 고른다.\n"
          "항상 아래 형식의 JSON 한 개만 출력해라. 설명·코드블록·마크다운은 금지.\n\n"
          "{\n"
          '  \"ids\": [string | number]\n'
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
  if not LLM_DEBUG:
    return

  head = system_prompt[:220].replace("\n", "\\n")
  _log_debug(f"[LLM DEBUG] kind: {kind}")
  _log_debug(f"[LLM DEBUG] input_text: {input_text}")
  _log_debug(f"[LLM DEBUG] system_prompt(head): {head}")
  _log_debug(f"[LLM DEBUG] raw_content: {raw_content}")
  if model_name:
    _log_debug(f"[LLM DEBUG] model: {model_name}")

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


def _resolve_request_id(raw: Optional[str]) -> str:
  if isinstance(raw, str) and raw.strip():
    return raw.strip()
  return secrets.token_hex(8)


async def _register_inflight(session_id: str, request_id: str,
                             task: asyncio.Task) -> None:
  async with inflight_lock:
    session_map = inflight_tasks.setdefault(session_id, {})
    existing = session_map.pop(request_id, None)
    if existing:
      existing.cancel()
    session_map[request_id] = task


async def _clear_inflight(session_id: str, request_id: str) -> None:
  async with inflight_lock:
    session_map = inflight_tasks.get(session_id)
    if not session_map:
      return
    session_map.pop(request_id, None)
    if not session_map:
      inflight_tasks.pop(session_id, None)


async def _cancel_inflight(session_id: str,
                           request_id: Optional[str] = None) -> int:
  async with inflight_lock:
    session_map = inflight_tasks.get(session_id)
    if not session_map:
      return 0
    if request_id:
      task = session_map.pop(request_id, None)
      if task:
        task.cancel()
        return 1
      return 0
    count = 0
    for task in list(session_map.values()):
      task.cancel()
      count += 1
    inflight_tasks.pop(session_id, None)
    return count


async def _run_with_interrupt(session_id: str, request_id: str, coro):
  task = asyncio.create_task(coro)
  await _register_inflight(session_id, request_id, task)
  try:
    return await task
  except asyncio.CancelledError:
    raise HTTPException(status_code=499, detail="요청이 중단되었습니다.")
  finally:
    await _clear_inflight(session_id, request_id)


async def _chat_json(kind: str,
                     system_prompt: str,
                     user_text: str,
                     reasoning_effort: Optional[str] = None,
                     model_name: Optional[str] = None) -> Dict[str, Any]:
  """
    gpt-5-nano를 Chat Completions로 호출하는 버전.
    - max_tokens 대신 max_completion_tokens 사용
    - temperature / reasoning 등 gpt-5-nano에서 에러나는 옵션은 보내지 않는다.
    """
  c = get_async_client()

  input_text = _current_reference_line() + user_text

  started = time.perf_counter()
  effort_value = _pick_reasoning_effort(reasoning_effort,
                                        DEFAULT_TEXT_REASONING_EFFORT)
  model = _sanitize_model(model_name) or DEFAULT_TEXT_MODEL
  try:
    completion = await c.chat.completions.create(
        model=model,
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
        max_completion_tokens=10000,
        reasoning_effort=effort_value,
        verbosity="low",
        response_format={"type": "json_object"},
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
                 usage_dict, model)
    return _safe_json_loads(raw_content)

  except Exception as e:
    _log_debug(f"[LLM DEBUG] exception: {repr(e)}")
    raise


async def _chat_json_stream(kind: str,
                            system_prompt: str,
                            user_text: str,
                            reasoning_effort: Optional[str] = None,
                            model_name: Optional[str] = None):
  c = get_async_client()
  input_text = _current_reference_line() + user_text
  effort_value = _pick_reasoning_effort(reasoning_effort,
                                        DEFAULT_TEXT_REASONING_EFFORT)
  model = _sanitize_model(model_name) or DEFAULT_TEXT_MODEL
  try:
    return await c.chat.completions.create(
        model=model,
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
        max_completion_tokens=10000,
        reasoning_effort=effort_value,
        stream=True,
        response_format={"type": "json_object"},
    )
  except Exception as e:
    _log_debug(f"[LLM DEBUG] stream exception: {repr(e)}")
    raise


async def _chat_multimodal_json(kind: str,
                                system_prompt: str,
                                user_text: str,
                                images: List[str],
                                reasoning_effort: Optional[str] = None,
                                model_name: Optional[str] = None) -> Dict[str,
                                                                         Any]:
  c = get_async_client()

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
  effort_value = _pick_reasoning_effort(reasoning_effort,
                                        DEFAULT_MULTIMODAL_REASONING_EFFORT)
  model = _sanitize_model(model_name) or DEFAULT_MULTIMODAL_MODEL
  try:
    completion = await c.chat.completions.create(
        model=model,
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
        reasoning_effort=effort_value,
        verbosity="low",
        response_format={"type": "json_object"},
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
                 usage_dict, model)
    return _safe_json_loads(raw_content)

  except Exception as e:
    _log_debug(f"[LLM DEBUG] exception: {repr(e)}")
    raise


async def _chat_multimodal_json_stream(kind: str,
                                       system_prompt: str,
                                       user_text: str,
                                       images: List[str],
                                       reasoning_effort: Optional[str] = None,
                                       model_name: Optional[str] = None):
  c = get_async_client()
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
  effort_value = _pick_reasoning_effort(reasoning_effort,
                                        DEFAULT_MULTIMODAL_REASONING_EFFORT)
  model = _sanitize_model(model_name) or DEFAULT_MULTIMODAL_MODEL
  try:
    return await c.chat.completions.create(
        model=model,
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
        reasoning_effort=effort_value,
        stream=True,
        response_format={"type": "json_object"},
    )
  except Exception as e:
    _log_debug(f"[LLM DEBUG] multimodal stream exception: {repr(e)}")
    raise
