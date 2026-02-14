from __future__ import annotations

from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple
import re

from fastapi import HTTPException

from .config import (
    LLM_DEBUG,
    SEOUL,
    ISO_DATETIME_RE,
    ISO_DATE_RE,
    ISO_DATETIME_24_RE,
    DATETIME_FLEX_RE,
    MAX_SCOPE_DAYS,
    MAX_IMAGE_ATTACHMENTS,
    MAX_IMAGE_DATA_URL_CHARS,
    IMAGE_TOO_LARGE_MESSAGE,
)


def _log_debug(message: str) -> None:
    if LLM_DEBUG:
        print(message, flush=True)


def _now_iso_minute() -> str:
    return datetime.now(SEOUL).strftime("%Y-%m-%dT%H:%M")


def normalize_text(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"\s+", " ", t)
    return t


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


def _normalize_exception_date(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if ISO_DATE_RE.match(raw):
        return raw
    if ISO_DATETIME_RE.match(raw) or ISO_DATETIME_24_RE.match(raw):
        return raw[:10]
    normalized = _normalize_datetime_minute(raw)
    if normalized:
        return normalized[:10]
    return None


def _normalize_end_datetime(raw_end: Any) -> Optional[str]:
    if not isinstance(raw_end, str):
        return None
    candidate = raw_end.strip()
    if not candidate:
        return None
    if ISO_DATETIME_RE.match(candidate):
        return candidate
    if ISO_DATETIME_24_RE.match(candidate):
        base = candidate[:10]
        try:
            base_date = datetime.strptime(base, "%Y-%m-%d").date()
        except Exception:
            return None
        next_day = base_date + timedelta(days=1)
        return next_day.strftime("%Y-%m-%dT00:00")
    if ISO_DATE_RE.match(candidate):
        try:
            base_date = datetime.strptime(candidate, "%Y-%m-%d").date()
            next_day = base_date + timedelta(days=1)
            return next_day.strftime("%Y-%m-%dT00:00")
        except Exception:
            return None
    normalized = _normalize_datetime_minute(candidate)
    if normalized:
        return normalized
    return None


def _normalize_datetime_minute(raw: str) -> Optional[str]:
    candidate = raw.strip()
    if not candidate:
        return None
    if ISO_DATETIME_RE.match(candidate):
        return candidate
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    match = DATETIME_FLEX_RE.match(candidate)
    if match:
        return f"{match.group(1)}T{match.group(2)}"
    try:
        dt = datetime.fromisoformat(candidate)
    except Exception:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M")


def is_all_day_span(start_iso: Optional[str],
                    end_iso: Optional[str]) -> bool:
    start_date, start_time = _split_iso_date_time(start_iso)
    if not start_date:
        return False
    # All-day start must be 00:00 (or None in some contexts)
    if start_time not in (None, "00:00"):
        return False

    if not end_iso:
        return True

    end_date, end_time = _split_iso_date_time(end_iso)
    if not end_date:
        return True

    if end_date < start_date:
        return False

    # Standard "exclusive" all-day end is 00:00 of some day > start_date
    if end_time == "00:00":
        return end_date > start_date

    # Legacy inclusive end 23:59 still recognized as all-day
    if end_time == "23:59":
        return True

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

    if not end_iso:
        return (start_date, start_date + timedelta(days=1))

    end_date, end_time = _split_iso_date_time(end_iso)
    if not end_date:
        return (start_date, start_date + timedelta(days=1))

    # Google Calendar expects end date to be exclusive.
    # If end_time is 00:00, it's already exclusive (common in FullCalendar).
    # If end_time is 23:59 or something else, it's inclusive, so we add 1 day.
    if end_time == "00:00" and end_date > start_date:
        end_exclusive = end_date
    else:
        end_exclusive = end_date + timedelta(days=1)

    return (start_date, end_exclusive)


def _normalize_single_event_times(
        start_raw: Any,
        end_raw: Any,
) -> Tuple[Optional[str], Optional[str], bool]:
    """
    start_raw/end_raw: raw strings from LLM or client.
    Returns (start_iso, end_iso, all_day_flag).
    - Accepts date-only strings and upgrades them to 00:00 (start) / property next day 00:00 (end).
    - Handles end at 24:00 by rolling to next day 00:00.
    """
    start_iso: Optional[str] = None
    end_iso: Optional[str] = None

    if isinstance(start_raw, str):
        s = start_raw.strip()
        if ISO_DATE_RE.match(s):
            start_iso = s + "T00:00"
        else:
            start_iso = _normalize_datetime_minute(s)

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
    if ISO_DATE_RE.match(candidate):
        return candidate + "T00:00"
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
                       require: bool = False,
                       max_days: Optional[int] = MAX_SCOPE_DAYS,
                       label: Optional[str] = None) -> Optional[Tuple[date, date]]:
    scope_label = label or ("삭제" if require else "조회")
    if not start_str or not end_str:
        if require:
            raise HTTPException(status_code=400,
                                detail=f"{scope_label} 범위의 시작/종료 날짜를 모두 입력해주세요.")
        return None

    try:
        start_date = datetime.strptime(start_str.strip(), "%Y-%m-%d").date()
        end_date = datetime.strptime(end_str.strip(), "%Y-%m-%d").date()
    except Exception:
        raise HTTPException(status_code=400,
                            detail=f"{scope_label} 범위 날짜 형식이 잘못되었습니다.")

    if end_date < start_date:
        raise HTTPException(status_code=400,
                            detail=f"{scope_label} 범위 종료일이 시작일보다 빠릅니다.")

    if max_days is not None and (end_date - start_date).days > max_days:
        raise HTTPException(status_code=400,
                            detail=f"{scope_label} 범위는 최대 {max_days}일까지만 설정할 수 있습니다.")

    return (start_date, end_date)


def _event_within_scope(ev: Any, scope: Optional[Tuple[date, date]]) -> bool:
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


def _merge_description(description: Optional[str],
                       meeting_url: Optional[str]) -> Optional[str]:
    desc = (description or "").strip()
    link = (meeting_url or "").strip()
    if not link:
        return desc or None
    if link in desc:
        return desc
    if desc:
        return f"{desc}\n\n회의 링크: {link}"
    return f"회의 링크: {link}"


def _build_gcal_attendees(attendees: Optional[List[str]]) -> Optional[List[Dict[str, str]]]:
    if attendees is None:
        return None
    if len(attendees) == 0:
        return []
    results: List[Dict[str, str]] = []
    for item in attendees:
        if not isinstance(item, str):
            continue
        email = item.strip()
        if email:
            results.append({"email": email})
    return results or None


def _build_gcal_reminders(reminders: Optional[List[int]]) -> Optional[Dict[str, Any]]:
    if reminders is None:
        return None
    if not reminders:
        return {"useDefault": True}
    overrides: List[Dict[str, Any]] = []
    for raw in reminders:
        try:
            minutes = int(raw)
        except Exception:
            continue
        if minutes < 0:
            continue
        overrides.append({"method": "popup", "minutes": minutes})
    if not overrides:
        return {"useDefault": True}
    return {"useDefault": False, "overrides": overrides}


def _normalize_visibility(value: Optional[str]) -> Optional[str]:
    if value in {"default", "public", "private"}:
        return value
    return None


def _normalize_transparency(value: Optional[str]) -> Optional[str]:
    if value in {"opaque", "transparent"}:
        return value
    return None


def _normalize_color_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit():
            number = int(cleaned)
            if 1 <= number <= 11:
                return str(number)
        if cleaned == "":
            return None
    return None
