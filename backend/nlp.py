from __future__ import annotations

import json
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple, Union

from fastapi import HTTPException

from .config import (
    ISO_DATE_RE,
    ISO_DATETIME_RE,
    SEOUL,
)
from .models import Event
from .utils import (
    normalize_text,
    _normalize_single_event_times,
    _normalize_end_datetime,
    is_all_day_span,
    _event_within_scope,
    _normalize_color_id,
)
from .recurrence import _resolve_recurrence, _expand_recurring_item
from . import state
from .state import (
    store_event,
    store_recurring_event,
    _recurring_definition_to_event,
    _collect_local_recurring_occurrences,
    _decode_occurrence_id,
)
from .gcal import (
    gcal_create_single_event,
    gcal_create_recurring_event,
    fetch_google_events_between,
    _clear_google_cache,
    _emit_google_sse,
)
from .llm import (
    async_client,
    _invoke_event_parser,
    _invoke_event_parser_stream,
    _parse_bool,
    build_delete_system_prompt,
    _chat_json,
)

async def create_events_from_natural_text_core(
    text: str,
    images: Optional[List[str]] = None,
    reasoning_effort: Optional[str] = None,
    model_name: Optional[str] = None,
    context_cache_key: Optional[str] = None,
    context_session_id: Optional[str] = None,
    session_id: Optional[str] = None,
    is_google: bool = False) -> List[Event]:
  images = images or []
  t = normalize_text(text)
  if not t and not images:
    raise HTTPException(status_code=400, detail="문장이나 이미지를 입력해주세요.")

  if async_client is None:
    raise HTTPException(
        status_code=500,
        detail="LLM client is not configured (OPENAI_API_KEY 미설정)")

  data = await _invoke_event_parser("preview",
                                    t,
                                    images,
                                    reasoning_effort,
                                    model_name=model_name,
                                    context_cache_key=context_cache_key,
                                    context_session_id=context_session_id,
                                    is_google=is_google)
  if _parse_bool(data.get("need_more_information")):
    content = (data.get("content") or "").strip()
    raise HTTPException(
        status_code=422,
        detail={
            "need_more_information": True,
            "content": content or "추가로 확인할 정보가 필요합니다.",
        })
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
                                                 all_day_flag,
                                                 session_id=session_id,
                                                 description=ev.get("description"),
                                                 attendees=ev.get("attendees"),
                                                 reminders=ev.get("reminders"),
                                                 visibility=ev.get("visibility"),
                                                 transparency=ev.get("transparency"),
                                                 meeting_url=ev.get("meeting_url"),
                                                 timezone_value=ev.get("timezone"))

    created.append(
        store_event(title=title,
                    start=start,
                    end=end,
                    location=location,
                    recur=recur,
                    google_event_id=google_event_id,
                    all_day=all_day_flag,
                    description=ev.get("description"),
                    attendees=ev.get("attendees"),
                    reminders=ev.get("reminders"),
                    visibility=ev.get("visibility"),
                    transparency=ev.get("transparency"),
                    meeting_url=ev.get("meeting_url"),
                    timezone_value=ev.get("timezone")))

  for rec_item in recurring_items:
    gcal_create_recurring_event(rec_item, session_id=session_id)

  return created


# -------------------------
# NLP Preview (추가 미리보기)
# -------------------------
async def preview_events_from_natural_text_core(
    text: str,
    images: Optional[List[str]] = None,
    reasoning_effort: Optional[str] = None,
    model_name: Optional[str] = None,
    context_cache_key: Optional[str] = None,
    context_session_id: Optional[str] = None,
    context_confirmed: bool = False,
    is_google: bool = False) -> Dict[str, Any]:
  images = images or []
  t = normalize_text(text)
  if not t and not images:
    raise HTTPException(status_code=400, detail="문장이나 이미지를 입력해주세요.")

  if async_client is None:
    raise HTTPException(
        status_code=500,
        detail="LLM client is not configured (OPENAI_API_KEY 미설정)")

  data = await _invoke_event_parser("parse",
                                    t,
                                    images,
                                    reasoning_effort,
                                    model_name=model_name,
                                    context_cache_key=context_cache_key,
                                    context_session_id=context_session_id,
                                    context_confirmed=context_confirmed,
                                    is_google=is_google)
  return _post_process_nlp_preview_result(data)


def _post_process_nlp_preview_result(data: Dict[str, Any]) -> Dict[str, Any]:
  if data.get("permission_required"):
    return data

  context_used = _parse_bool(data.get("context_used"))
  if _parse_bool(data.get("need_more_information")):
    content = (data.get("content") or "").strip()
    return {
        "need_more_information": True,
        "content": content or "추가로 확인할 정보가 필요합니다.",
        "items": [],
        "context_used": context_used,
    }
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

      recurrence = _resolve_recurrence(item)
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

      display_end_date = None
      legacy_end = item.get("end_date")
      if isinstance(legacy_end, str) and ISO_DATE_RE.match(legacy_end):
        display_end_date = legacy_end
      elif recurrence and recurrence.get("end"):
        rec_end = recurrence.get("end") or {}
        until_val = rec_end.get("until")
        if isinstance(until_val, str) and ISO_DATE_RE.match(until_val):
          display_end_date = until_val
        elif rec_end.get("count") and occurrences:
          last_start = occurrences[-1].get("start")
          if isinstance(last_start, str) and len(last_start) >= 10:
            display_end_date = last_start[:10]

      display_weekdays = item.get("weekdays")
      if not isinstance(display_weekdays, list) and recurrence:
        display_weekdays = recurrence.get("byweekday")
      requires_end = False
      if recurrence:
        rec_end_val = recurrence.get("end")
        requires_end = not (rec_end_val and (rec_end_val.get("until") or rec_end_val.get("count")))
      elif not display_end_date:
        requires_end = True

      finite_count = count if recurrence and recurrence.get("end") else None

      out_items.append({
          "type": "recurring",
          "title": title,
          "start_date": item.get("start_date"),
          "end_date": display_end_date,
          "weekdays": display_weekdays,
          "time": item.get("time"),
          "duration_minutes": item.get("duration_minutes"),
          "location": (item.get("location") or "").strip() or None,
          "recurrence": recurrence,
          "count": finite_count,
          "samples": [x.get("start") for x in samples if x.get("start")],
          "all_day":
          len(expanded) > 0 and bool(expanded[0].get("all_day")),
          "occurrences": occurrences,
          "requires_end_confirmation": requires_end,
      })

  if not out_items:
    raise HTTPException(status_code=422, detail="미리보기를 만들 수 없습니다.")

  return {"items": out_items, "context_used": context_used}


# -------------------------
# 선택 적용: Add (모달에서 체크한 것만)
# -------------------------
def apply_add_items_core(items: List[Dict[str, Any]],
                         session_id: Optional[str] = None) -> List[Event]:
  created: List[Event] = []
  touched_google = False

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
                                                 location,
                                                 all_day_flag,
                                                 session_id=session_id,
                                                 description=item.get("description"),
                                                 attendees=item.get("attendees"),
                                                 reminders=item.get("reminders"),
                                                 visibility=item.get("visibility"),
                                                 transparency=item.get("transparency"),
                                                 meeting_url=item.get("meeting_url"),
                                                 timezone_value=item.get("timezone"),
                                                 color_id=item.get("color_id"))
      if session_id and google_event_id:
        touched_google = True

      created.append(
          store_event(
              title=title,
              start=start,
              end=end_str,
              location=location,
              recur=None,
              google_event_id=google_event_id,
              all_day=all_day_flag,
              description=item.get("description"),
              attendees=item.get("attendees"),
              reminders=item.get("reminders"),
              visibility=item.get("visibility"),
              transparency=item.get("transparency"),
              meeting_url=item.get("meeting_url"),
              timezone_value=item.get("timezone"),
              color_id=_normalize_color_id(item.get("color_id")),
          ))

    elif typ == "recurring":
      recurrence_spec = _resolve_recurrence(item)
      if not recurrence_spec:
        continue
      override = item.get("recurring_end_override")
      if isinstance(override, dict):
        mode = override.get("mode")
        if mode == "none":
          recurrence_spec = {
              **recurrence_spec, "end": None,
          }
        elif mode == "until":
          until_val = override.get("value")
          if isinstance(until_val, str) and ISO_DATE_RE.match(until_val):
            recurrence_spec = {
                **recurrence_spec,
                "end": {
                    "until": until_val,
                    "count": None
                }
            }
        elif mode == "count":
          try:
            count_val = int(override.get("value"))
          except Exception:
            count_val = None
          if count_val and count_val > 0:
            recurrence_spec = {
                **recurrence_spec,
                "end": {
                    "until": None,
                    "count": count_val
                }
            }
      item["recurrence"] = recurrence_spec

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
        google_recur_id = gcal_create_recurring_event(item, session_id=session_id)
        if session_id and google_recur_id:
          touched_google = True
        start_date_value = item.get("start_date")
        if not isinstance(start_date_value, str):
          continue
        stored = store_recurring_event(
            title=(item.get("title") or "").strip(),
            start_date=start_date_value,
            time=item.get("time"),
            duration_minutes=item.get("duration_minutes"),
            location=(item.get("location") or "").strip() or None,
            description=item.get("description"),
            attendees=item.get("attendees"),
            reminders=item.get("reminders"),
            visibility=item.get("visibility"),
            transparency=item.get("transparency"),
            meeting_url=item.get("meeting_url"),
            recurrence=recurrence_spec,
            timezone_value=item.get("timezone") or "Asia/Seoul",
            color_id=_normalize_color_id(item.get("color_id")),
            google_event_id=google_recur_id,
        )
        created.append(_recurring_definition_to_event(stored))
        continue

      for ev in expanded:
        all_day_flag = bool(ev.get("all_day"))
        google_single_id = gcal_create_single_event(ev["title"], ev["start"],
                                                    ev.get("end"),
                                                    ev.get("location"),
                                                    all_day_flag,
                                                    session_id=session_id,
                                                    description=item.get("description"),
                                                    attendees=item.get("attendees"),
                                                    reminders=item.get("reminders"),
                                                    visibility=item.get("visibility"),
                                                    transparency=item.get("transparency"),
                                                    meeting_url=item.get("meeting_url"),
                                                    timezone_value=item.get("timezone"),
                                                    color_id=item.get("color_id"))
        if session_id and google_single_id:
          touched_google = True
        created.append(
            store_event(
                title=ev["title"],
                start=ev["start"],
                end=ev.get("end"),
                location=ev.get("location"),
                recur=None,
                google_event_id=google_single_id,
                all_day=all_day_flag,
                description=item.get("description"),
                attendees=item.get("attendees"),
                reminders=item.get("reminders"),
                visibility=item.get("visibility"),
                transparency=item.get("transparency"),
                meeting_url=item.get("meeting_url"),
                timezone_value=item.get("timezone"),
                color_id=_normalize_color_id(item.get("color_id")),
            ))

  if not created:
    raise HTTPException(status_code=422, detail="No valid items to create")

  if touched_google and session_id:
    _clear_google_cache(session_id)

  return created


# -------------------------
# 삭제 NLP (기존)
# -------------------------
async def create_delete_ids_from_natural_text(
    text: str,
    scope: Optional[Tuple[date, date]] = None,
    reasoning_effort: Optional[str] = None,
    model_name: Optional[str] = None,
    session_id: Optional[str] = None,
    context_confirmed: bool = False,
    is_google: bool = False
) -> Union[List[Union[int, str]], Dict[str, Any]]:
  if async_client is None:
    return []

  # 삭제 시에도 컨텍스트(기존 일정) 로드 전 허락 확인
  if not context_confirmed:
    return {
        "permission_required": True,
        "needs_context": True,
        "context_used": False,
    }

  if is_google:
    if session_id and scope:
      google_items = fetch_google_events_between(scope[0], scope[1], session_id)
      snapshot = [{
          "id": f"{item.get('calendar_id')}::{item.get('id')}"
          if item.get("calendar_id") else item.get("id"),
          "title": item.get("title"),
          "start": item.get("start"),
          "end": item.get("end"),
          "location": item.get("location"),
          "recur": None,
      } for item in google_items][:50]
    else:
      snapshot = []
  else:
    snapshot = [{
        "id": e.id,
        "title": e.title,
        "start": e.start,
        "end": e.end,
        "location": e.location,
        "recur": e.recur
    } for e in state.events if _event_within_scope(e, scope)][:50]

    if len(snapshot) < 50:
      rec_occurrences = _collect_local_recurring_occurrences(scope=scope)
      for occ in rec_occurrences:
        snapshot.append({
            "id": occ.id,
            "title": occ.title,
            "start": occ.start,
            "end": occ.end,
            "location": occ.location,
            "recur": occ.recur
        })
        if len(snapshot) >= 50:
          break

    if len(snapshot) < 50:
      for rec in state.recurring_events:
        rec_ev = _recurring_definition_to_event(rec)
        if not scope or _event_within_scope(rec_ev, scope):
          snapshot.append({
              "id": rec_ev.id,
              "title": rec_ev.title,
              "start": rec_ev.start,
              "end": rec_ev.end,
              "location": rec_ev.location,
              "recur": rec_ev.recur
          })
          if len(snapshot) >= 50:
            break

  user_payload = {
      "existing_events": snapshot,
      "delete_request": normalize_text(text),
      "timezone": "Asia/Seoul"
  }

  data = await _chat_json("delete",
                          build_delete_system_prompt(),
                          json.dumps(user_payload, ensure_ascii=False),
                          reasoning_effort=reasoning_effort,
                          model_name=model_name)

  ids = data.get("ids")
  if not isinstance(ids, list):
    return []

  if session_id:
    cleaned: List[str] = []
    seen = set()
    for x in ids:
      if x is None:
        continue
      value = str(x).strip()
      if not value:
        continue
      if value in seen:
        continue
      seen.add(value)
      cleaned.append(value)
    return cleaned

  cleaned: List[int] = []
  seen = set()
  for x in ids:
    try:
      i = int(x)
    except Exception:
      continue
    if i < 0:
      rec_target = _decode_occurrence_id(i)
      if rec_target:
        i = rec_target
    if i not in seen:
      seen.add(i)
      cleaned.append(i)

  return cleaned


async def delete_preview_groups(text: str,
                                scope: Optional[Tuple[date, date]] = None,
                                reasoning_effort: Optional[str] = None,
                                model_name: Optional[str] = None,
                                session_id: Optional[str] = None,
                                context_confirmed: bool = False,
                                is_google: bool = False
                                ) -> Dict[str, Any]:
  text = normalize_text(text)
  if not text:
    return {"groups": []}

  ids_or_perm = await create_delete_ids_from_natural_text(text,
                                                  scope=scope,
                                                  reasoning_effort=reasoning_effort,
                                                  model_name=model_name,
                                                  session_id=session_id,
                                                  context_confirmed=context_confirmed,
                                                  is_google=is_google)
  if isinstance(ids_or_perm, dict) and ids_or_perm.get("permission_required"):
    return ids_or_perm

  ids = ids_or_perm if isinstance(ids_or_perm, list) else []
  if not ids:
    return {"groups": []}

  if is_google:
    id_set = {str(x) for x in ids}
    combined_events = fetch_google_events_between(scope[0], scope[1], session_id)
    targets = []
    for e in combined_events:
      cal_id = e.get("calendar_id")
      raw_id = str(e.get("id"))
      full_id = f"{cal_id}::{raw_id}" if cal_id else raw_id
      if full_id in id_set or raw_id in id_set:
        targets.append(e)

    groups_map: Dict[str, Dict[str, Any]] = {}
    for e in targets:
      cal_id = e.get("calendar_id")
      raw_id = str(e.get("id"))
      full_id = f"{cal_id}::{raw_id}" if cal_id else raw_id
      key = f"single::{full_id}"
      g = groups_map.get(key)
      if g is None:
        t = (e.get("start") or "")[11:16] if isinstance(
            e.get("start"), str) and len(e.get("start")) >= 16 else None
        g = {
            "group_key": key,
            "kind": "single",
            "title": e.get("title"),
            "time": t,
            "location": e.get("location"),
            "ids": [],
            "items": [],
        }
        groups_map[key] = g

      g["ids"].append(full_id)
      g["items"].append({
          "id": full_id,
          "title": e.get("title"),
          "start": e.get("start"),
          "end": e.get("end"),
          "location": e.get("location"),
          "recur": None,
          "all_day": e.get("all_day"),
      })
  else:
    id_set = set()
    parent_id_set = set()
    for x in ids:
      try:
        val = int(x)
        id_set.add(val)
        # 만약 발생(occurrence) ID라면 변환된 부모 ID도 포함하여 탐색 허용
        p_id = _decode_occurrence_id(val)
        if p_id:
          parent_id_set.add(p_id)
      except Exception:
        continue

    combined_events = list(state.events)
    combined_events.extend(_collect_local_recurring_occurrences(scope=scope))
    # 반복 일정 원형(Prototype)도 id_set에 있으면 포함
    for rec in state.recurring_events:
      event_obj = _recurring_definition_to_event(rec)
      combined_events.append(event_obj)

    targets = []
    for e in combined_events:
      # 1. ID 자체가 매칭됨
      # 2. 혹은 부모 ID가 매칭됨 (발생 일정인 경우)
      # 3. 혹은 부모 ID 자체가 매칭됨 (원형 일정인 경우)
      matched = False
      if e.id in id_set:
        matched = True
      elif e.recur == "recurring":
        # 발생 일정인 경우
        p_id = _decode_occurrence_id(e.id)
        if p_id and p_id in id_set:
          matched = True
        elif e.id in parent_id_set:
          matched = True

      if matched:
        # 이미 snapshot 시점에 scope 필터링이 되었으므로 여기서는 최소한의 체크만
        if _event_within_scope(e, scope) or e.recur == "recurring":
          targets.append(e)

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
