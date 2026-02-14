from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import calendar
import re

from zoneinfo import ZoneInfo

from .config import (
    ISO_DATE_RE,
    MAX_RECURRENCE_EXPANSION_DAYS,
    MAX_RECURRENCE_OCCURRENCES,
)
from .utils import _normalize_exception_date

_RRULE_FREQS = {"DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
_RRULE_WEEKDAY_TO_INDEX = {
    "MO": 0,
    "TU": 1,
    "WE": 2,
    "TH": 3,
    "FR": 4,
    "SA": 5,
    "SU": 6,
}
_RRULE_INDEX_TO_WEEKDAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]


def _normalize_int_list(value: Any,
                        min_val: int,
                        max_val: int,
                        allow_neg1: bool = False) -> List[int]:
    if not isinstance(value, list):
        return []
    out: List[int] = []
    seen: set[int] = set()
    for raw in value:
        try:
            iv = int(raw)
        except Exception:
            continue
        if allow_neg1 and iv == -1:
            if iv not in seen:
                out.append(iv)
                seen.add(iv)
            continue
        if min_val <= iv <= max_val and iv not in seen:
            out.append(iv)
            seen.add(iv)
    return out


def _normalize_recurrence_dict(recurrence: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(recurrence, dict):
        return None
    freq = (recurrence.get("freq") or "").strip().upper()
    if freq not in _RRULE_FREQS:
        return None

    interval_raw = recurrence.get("interval")
    try:
        interval = int(interval_raw) if interval_raw is not None else 1
    except Exception:
        interval = 1
    if interval < 1:
        interval = 1

    byweekday = _normalize_int_list(recurrence.get("byweekday"), 0, 6)
    bymonthday = _normalize_int_list(recurrence.get("bymonthday"),
                                     1,
                                     31,
                                     allow_neg1=True)
    bymonth = _normalize_int_list(recurrence.get("bymonth"), 1, 12)

    bysetpos_raw = recurrence.get("bysetpos")
    bysetpos: Optional[int] = None
    if bysetpos_raw is not None:
        try:
            iv = int(bysetpos_raw)
            if iv == -1 or 1 <= iv <= 5:
                bysetpos = iv
        except Exception:
            bysetpos = None

    end_raw = recurrence.get("end")
    # LLM may also use "end_date" as a shorthand for the until date
    if end_raw is None:
        end_raw = recurrence.get("end_date")
    end: Optional[Dict[str, Any]] = None
    if isinstance(end_raw, dict):
        until_raw = end_raw.get("until")
        count_raw = end_raw.get("count")
        until = (until_raw.strip() if isinstance(until_raw, str) else None)
        if until and not ISO_DATE_RE.match(until):
            until = None
        count: Optional[int] = None
        if count_raw is not None:
            try:
                count = int(count_raw)
            except Exception:
                count = None
            if count is not None and count <= 0:
                count = None
        if until and count:
            count = None
        if until or count:
            end = {"until": until, "count": count}
    elif isinstance(end_raw, str):
        # LLM shorthand: "end": "2026-04-10" → treat as until date
        stripped = end_raw.strip()
        if stripped and ISO_DATE_RE.match(stripped):
            end = {"until": stripped, "count": None}
    elif isinstance(end_raw, (int, float)) and int(end_raw) > 0:
        # LLM shorthand: "end": 10 → treat as count
        end = {"until": None, "count": int(end_raw)}

    # Also check top-level "count" if end is still unresolved
    if end is None:
        top_count = recurrence.get("count")
        if top_count is not None:
            try:
                cv = int(top_count)
                if cv > 0:
                    end = {"until": None, "count": cv}
            except Exception:
                pass

    return {
        "freq": freq,
        "interval": interval,
        "byweekday": byweekday or None,
        "bymonthday": bymonthday or None,
        "bysetpos": bysetpos,
        "bymonth": bymonth or None,
        "end": end,
    }


def _normalize_rrule_core(rrule: Any) -> Optional[str]:
    if not isinstance(rrule, str):
        return None
    raw = rrule.strip()
    if not raw:
        return None
    if raw.upper().startswith("RRULE:"):
        raw = raw.split(":", 1)[1].strip()
    if not raw:
        return None

    parts_raw = [part.strip() for part in raw.split(";") if part.strip()]
    if not parts_raw:
        return None

    values: Dict[str, str] = {}
    for part in parts_raw:
        if "=" not in part:
            return None
        key, val = part.split("=", 1)
        key = key.strip().upper()
        val = val.strip().upper()
        if not key or not val:
            return None
        values[key] = val

    freq = values.get("FREQ")
    if freq not in _RRULE_FREQS:
        return None

    interval_raw = values.get("INTERVAL")
    if interval_raw is not None:
        try:
            interval = int(interval_raw)
        except Exception:
            return None
        if interval <= 0:
            return None
        values["INTERVAL"] = str(interval)

    count_raw = values.get("COUNT")
    if count_raw is not None:
        try:
            count = int(count_raw)
        except Exception:
            return None
        if count <= 0:
            return None
        values["COUNT"] = str(count)

    until_raw = values.get("UNTIL")
    if until_raw is not None:
        until_clean = until_raw.strip().upper()
        if not re.match(r"^\d{8}(T\d{6}Z?)?$", until_clean):
            return None
        values["UNTIL"] = until_clean

    if "COUNT" in values and "UNTIL" in values:
        return None

    byday_raw = values.get("BYDAY")
    if byday_raw is not None:
        normalized_days: List[str] = []
        for token in [tok.strip().upper() for tok in byday_raw.split(",") if tok.strip()]:
            match = re.match(r"^([+-]?\d)?(MO|TU|WE|TH|FR|SA|SU)$", token)
            if not match:
                return None
            pos_raw = match.group(1)
            day_code = match.group(2)
            if pos_raw is None:
                normalized_days.append(day_code)
                continue
            try:
                pos = int(pos_raw)
            except Exception:
                return None
            if pos != -1 and not (1 <= pos <= 5):
                return None
            normalized_days.append(f"{pos}{day_code}")
        if not normalized_days:
            return None
        values["BYDAY"] = ",".join(normalized_days)

    bymonthday_raw = values.get("BYMONTHDAY")
    if bymonthday_raw is not None:
        days: List[str] = []
        for token in [tok.strip() for tok in bymonthday_raw.split(",") if tok.strip()]:
            try:
                day = int(token)
            except Exception:
                return None
            if day == 0 or day < -31 or day > 31:
                return None
            days.append(str(day))
        if not days:
            return None
        values["BYMONTHDAY"] = ",".join(days)

    bymonth_raw = values.get("BYMONTH")
    if bymonth_raw is not None:
        months: List[str] = []
        for token in [tok.strip() for tok in bymonth_raw.split(",") if tok.strip()]:
            try:
                month = int(token)
            except Exception:
                return None
            if not (1 <= month <= 12):
                return None
            months.append(str(month))
        if not months:
            return None
        values["BYMONTH"] = ",".join(months)

    bysetpos_raw = values.get("BYSETPOS")
    if bysetpos_raw is not None:
        try:
            bysetpos = int(bysetpos_raw)
        except Exception:
            return None
        if bysetpos == 0 or bysetpos < -366 or bysetpos > 366:
            return None
        values["BYSETPOS"] = str(bysetpos)

    ordered = ["FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "BYMONTH", "BYSETPOS",
               "UNTIL", "COUNT", "WKST"]
    parts: List[str] = []
    for key in ordered:
        if key in values:
            parts.append(f"{key}={values[key]}")
    for key in sorted(values.keys()):
        if key not in ordered:
            parts.append(f"{key}={values[key]}")
    return ";".join(parts) if parts else None


def _rrule_to_recurrence(rrule_core: str) -> Optional[Dict[str, Any]]:
    normalized = _normalize_rrule_core(rrule_core)
    if not normalized:
        return None
    values: Dict[str, str] = {}
    for part in normalized.split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        values[key] = val

    freq = values.get("FREQ")
    if freq not in _RRULE_FREQS:
        return None

    recurrence: Dict[str, Any] = {"freq": freq}
    if "INTERVAL" in values:
        try:
            recurrence["interval"] = int(values["INTERVAL"])
        except Exception:
            recurrence["interval"] = 1

    byday_raw = values.get("BYDAY")
    if byday_raw:
        weekdays: List[int] = []
        seen_weekdays: set[int] = set()
        setpos_values: set[int] = set()
        for token in byday_raw.split(","):
            match = re.match(r"^([+-]?\d)?(MO|TU|WE|TH|FR|SA|SU)$", token)
            if not match:
                continue
            pos_raw = match.group(1)
            day_code = match.group(2)
            day_index = _RRULE_WEEKDAY_TO_INDEX.get(day_code)
            if day_index is None:
                continue
            if day_index not in seen_weekdays:
                weekdays.append(day_index)
                seen_weekdays.add(day_index)
            if pos_raw is not None:
                try:
                    pos = int(pos_raw)
                except Exception:
                    continue
                setpos_values.add(pos)
        if weekdays:
            recurrence["byweekday"] = weekdays
        if "BYSETPOS" not in values and len(setpos_values) == 1:
            recurrence["bysetpos"] = next(iter(setpos_values))

    bymonthday_raw = values.get("BYMONTHDAY")
    if bymonthday_raw:
        days: List[int] = []
        for token in bymonthday_raw.split(","):
            try:
                days.append(int(token))
            except Exception:
                continue
        if days:
            recurrence["bymonthday"] = days

    bymonth_raw = values.get("BYMONTH")
    if bymonth_raw:
        months: List[int] = []
        for token in bymonth_raw.split(","):
            try:
                months.append(int(token))
            except Exception:
                continue
        if months:
            recurrence["bymonth"] = months

    bysetpos_raw = values.get("BYSETPOS")
    if bysetpos_raw:
        try:
            recurrence["bysetpos"] = int(bysetpos_raw)
        except Exception:
            pass

    end: Dict[str, Any] = {}
    until_raw = values.get("UNTIL")
    if until_raw:
        try:
            until_date = datetime.strptime(until_raw[:8], "%Y%m%d").date()
            end["until"] = until_date.isoformat()
        except Exception:
            pass
    count_raw = values.get("COUNT")
    if count_raw and "until" not in end:
        try:
            count = int(count_raw)
            if count > 0:
                end["count"] = count
        except Exception:
            pass
    if end:
        recurrence["end"] = end

    return _normalize_recurrence_dict(recurrence)


def _build_legacy_weekly_recurrence(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    weekdays = _normalize_int_list(item.get("weekdays"), 0, 6)
    if not weekdays:
        return None
    end_date_str = item.get("end_date")
    until = None
    if isinstance(end_date_str, str) and ISO_DATE_RE.match(end_date_str.strip()):
        until = end_date_str.strip()
    end = {"until": until, "count": None} if until else None
    return {
        "freq": "WEEKLY",
        "interval": 1,
        "byweekday": weekdays or None,
        "bymonthday": None,
        "bysetpos": None,
        "bymonth": None,
        "end": end,
    }


def _resolve_recurrence(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rec_raw = item.get("recurrence")
    if isinstance(rec_raw, dict):
        normalized = _normalize_recurrence_dict(rec_raw)
        if normalized:
            item["recurrence"] = normalized
            return normalized

    rrule_core = _normalize_rrule_core(item.get("rrule"))
    if rrule_core:
        item["rrule"] = rrule_core
        from_rrule = _rrule_to_recurrence(rrule_core)
        if from_rrule:
            item["recurrence"] = from_rrule
            return from_rrule

    legacy = _build_legacy_weekly_recurrence(item)
    if legacy:
        item["recurrence"] = legacy
        return legacy

    return None


def _month_last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _nth_weekday_in_month(year: int,
                          month: int,
                          weekday: int,
                          pos: int) -> Optional[date]:
    if pos == 0:
        return None
    last_day = _month_last_day(year, month)
    if pos > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        day = 1 + offset + (pos - 1) * 7
        if 1 <= day <= last_day:
            return date(year, month, day)
        return None

    last = date(year, month, last_day)
    offset = (last.weekday() - weekday) % 7
    day = last_day - offset + (pos + 1) * 7
    if 1 <= day <= last_day:
        return date(year, month, day)
    return None


def _monthly_candidates(year: int,
                        month: int,
                        recurrence: Dict[str, Any],
                        default_day: int) -> List[date]:
    bymonthday = recurrence.get("bymonthday") or []
    byweekday = recurrence.get("byweekday") or []
    bysetpos = recurrence.get("bysetpos")
    last_day = _month_last_day(year, month)
    results: List[date] = []

    for d in bymonthday:
        if d == -1:
            day = last_day
        elif 1 <= d <= last_day:
            day = d
        else:
            continue
        results.append(date(year, month, day))

    if byweekday:
        if bysetpos is not None:
            for w in byweekday:
                dt = _nth_weekday_in_month(year, month, w, int(bysetpos))
                if dt:
                    results.append(dt)
        else:
            for w in byweekday:
                dt = _nth_weekday_in_month(year, month, w, 1)
                while dt and dt.month == month:
                    results.append(dt)
                    dt = dt + timedelta(days=7)

    if not results:
        day = min(max(default_day, 1), last_day)
        results.append(date(year, month, day))

    dedup = sorted({d: None for d in results}.keys())
    return dedup


def _add_months(year: int, month: int, delta: int) -> Tuple[int, int]:
    total = (year * 12 + (month - 1)) + delta
    new_year = total // 12
    new_month = total % 12 + 1
    return new_year, new_month


def _collect_recurrence_dates(recurrence: Dict[str, Any],
                              start_date: date,
                              scope: Optional[Tuple[date, date]] = None) -> List[date]:
    freq = recurrence.get("freq")
    interval = int(recurrence.get("interval") or 1)
    interval = max(interval, 1)
    byweekday = recurrence.get("byweekday") or []
    bymonthday = recurrence.get("bymonthday") or []
    bysetpos = recurrence.get("bysetpos")
    bymonth = recurrence.get("bymonth") or []
    end = recurrence.get("end") or {}

    until_date: Optional[date] = None
    count: Optional[int] = None
    until_raw = end.get("until")
    if isinstance(until_raw, str) and ISO_DATE_RE.match(until_raw):
        try:
            until_date = datetime.strptime(until_raw, "%Y-%m-%d").date()
        except Exception:
            until_date = None
    count_raw = end.get("count")
    if count_raw is not None:
        try:
            count = int(count_raw)
        except Exception:
            count = None
        if count is not None and count <= 0:
            count = None
    if until_date and count:
        count = None

    scope_start = start_date
    limit_date = start_date + timedelta(days=MAX_RECURRENCE_EXPANSION_DAYS)
    scope_filter: Optional[Tuple[date, date]] = None

    if scope:
        if count is None:
            scope_start = max(scope_start, scope[0])
        else:
            scope_filter = scope
        limit_date = scope[1]

    if count is not None:
        count = min(count, MAX_RECURRENCE_OCCURRENCES)
        if not scope and not until_date:
            limit_days = MAX_RECURRENCE_EXPANSION_DAYS * count
            limit_date = max(limit_date, start_date + timedelta(days=limit_days))

    if until_date and until_date < limit_date:
        limit_date = until_date

    results: List[date] = []

    def push_date(d: date) -> bool:
        if d < scope_start:
            return False
        if d > limit_date:
            return False
        results.append(d)
        if len(results) >= MAX_RECURRENCE_OCCURRENCES:
            return True
        if count is not None and len(results) >= count:
            return True
        return False

    if freq == "DAILY":
        if scope_start <= start_date:
            cur = start_date
        else:
            delta_days = (scope_start - start_date).days
            offset = delta_days % interval
            cur = scope_start if offset == 0 else scope_start + timedelta(
                days=interval - offset)
        while cur <= limit_date:
            if push_date(cur):
                break
            cur += timedelta(days=interval)

    elif freq == "WEEKLY":
        weekdays = sorted({int(w) for w in byweekday
                           if isinstance(w, int) and 0 <= w <= 6})
        if not weekdays:
            weekdays = [start_date.weekday()]

        base = start_date - timedelta(days=start_date.weekday())
        week_index = 0
        while True:
            week_start = base + timedelta(days=week_index * interval * 7)
            if week_start > limit_date:
                break
            for w in weekdays:
                occ = week_start + timedelta(days=w)
                if occ < scope_start:
                    continue
                if occ > limit_date:
                    continue
                if push_date(occ):
                    return results
            week_index += 1

    elif freq == "MONTHLY":
        month_index = 0
        while True:
            year, month = _add_months(start_date.year, start_date.month,
                                      month_index * interval)
            first_day = date(year, month, 1)
            if first_day > limit_date:
                break
            candidates = _monthly_candidates(year, month, {
                "bymonthday": bymonthday,
                "byweekday": byweekday,
                "bysetpos": bysetpos
            }, start_date.day)
            for occ in candidates:
                if occ < scope_start:
                    continue
                if occ > limit_date:
                    continue
                if push_date(occ):
                    return results
            month_index += 1

    elif freq == "YEARLY":
        months = [int(m) for m in bymonth if isinstance(m, int) and 1 <= m <= 12]
        if not months:
            months = [start_date.month]

        year_index = 0
        while True:
            year = start_date.year + year_index * interval
            first_day = date(year, 1, 1)
            if first_day > limit_date:
                break
            for month in months:
                candidates = _monthly_candidates(year, month, {
                    "bymonthday": bymonthday,
                    "byweekday": byweekday,
                    "bysetpos": bysetpos
                }, start_date.day)
                for occ in candidates:
                    if occ < scope_start:
                        continue
                    if occ > limit_date:
                        continue
                    if push_date(occ):
                        return results
            year_index += 1

    results.sort()
    if scope_filter:
        return [d for d in results if scope_filter[0] <= d <= scope_filter[1]]
    return results


def _expand_recurring_item(item: Dict[str, Any],
                           scope: Optional[Tuple[date, date]] = None) -> List[Dict[str, Any]]:
    """
    recurring item -> 여러 개의 단일 일정 dict로 전개
    """
    title = (item.get("title") or "").strip()
    start_date_str = item.get("start_date")
    if not title or not isinstance(start_date_str, str):
        return []

    recurrence = _resolve_recurrence(item)
    if not recurrence:
        return []

    try:
        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
    except Exception:
        return []

    time_str = item.get("time")
    duration_minutes = item.get("duration_minutes")
    location = item.get("location")
    timezone_str = item.get("timezone") or "Asia/Seoul"
    tzinfo = ZoneInfo(timezone_str)

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
    exceptions_raw = item.get("exceptions") or []
    exceptions: set[str] = set()
    if isinstance(exceptions_raw, list):
        for raw in exceptions_raw:
            normalized = _normalize_exception_date(raw)
            if normalized:
                exceptions.add(normalized)

    results: List[Dict[str, Any]] = []

    for cur in _collect_recurrence_dates(recurrence, start_date, scope=scope):
        if cur.strftime("%Y-%m-%d") in exceptions:
            continue
        if time_valid:
            start_dt = datetime(cur.year, cur.month, cur.day, hh, mm, tzinfo=tzinfo)
            start_str = start_dt.strftime("%Y-%m-%dT%H:%M")
            end_str: Optional[str] = None
            if dur is not None:
                end_dt = start_dt + timedelta(minutes=dur)
                end_str = end_dt.strftime("%Y-%m-%dT%H:%M")
        else:
            start_dt = datetime(cur.year, cur.month, cur.day, 0, 0, tzinfo=tzinfo)
            end_dt = datetime(cur.year, cur.month, cur.day, 23, 59, tzinfo=tzinfo)
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

    if scope:
        filtered: List[Dict[str, Any]] = []
        for ev in results:
            start_iso = ev.get("start")
            if not isinstance(start_iso, str):
                continue
            start_date_value = datetime.strptime(start_iso[:10], "%Y-%m-%d").date()
            if scope[0] <= start_date_value <= scope[1]:
                filtered.append(ev)
        return filtered

    return results


def _format_rrule_until(until_date: date,
                        time_str: Optional[str],
                        tz_name: str) -> str:
    """Format UNTIL value for RRULE.
    Google Calendar requires UNTIL in UTC with Z suffix for timed events,
    or YYYYMMDD for all-day events."""
    tzinfo = ZoneInfo(tz_name)
    if isinstance(time_str, str) and re.match(r"^\d{2}:\d{2}$", time_str):
        hh, mm = [int(x) for x in time_str.split(":")]
        local_dt = datetime(until_date.year, until_date.month, until_date.day,
                            hh, mm, 0, tzinfo=tzinfo)
        utc_dt = local_dt.astimezone(ZoneInfo("UTC"))
        return utc_dt.strftime("%Y%m%dT%H%M%SZ")
    # All-day: use date-only format
    return until_date.strftime("%Y%m%d")


def _build_rrule_core(recurrence: Dict[str, Any],
                      start_date_str: str,
                      time_str: Optional[str],
                      tz_name: str) -> Optional[str]:
    freq = recurrence.get("freq")
    if freq not in _RRULE_FREQS:
        return None

    parts = [f"FREQ={freq}"]
    interval = recurrence.get("interval") or 1
    if interval != 1:
        parts.append(f"INTERVAL={interval}")

    byweekday = recurrence.get("byweekday") or []
    if byweekday:
        weekdays = [_RRULE_INDEX_TO_WEEKDAY[int(w)] for w in byweekday if 0 <= int(w) <= 6]
        if weekdays:
            parts.append("BYDAY=" + ",".join(weekdays))

    bymonthday = recurrence.get("bymonthday") or []
    if bymonthday:
        parts.append("BYMONTHDAY=" + ",".join(str(int(d)) for d in bymonthday))

    bymonth = recurrence.get("bymonth") or []
    if bymonth:
        parts.append("BYMONTH=" + ",".join(str(int(m)) for m in bymonth))

    bysetpos = recurrence.get("bysetpos")
    if bysetpos:
        try:
            parts.append(f"BYSETPOS={int(bysetpos)}")
        except Exception:
            pass

    end = recurrence.get("end") or {}
    until_raw = end.get("until")
    count_raw = end.get("count")
    until_date: Optional[date] = None
    if isinstance(until_raw, str) and ISO_DATE_RE.match(until_raw):
        try:
            until_date = datetime.strptime(until_raw, "%Y-%m-%d").date()
        except Exception:
            until_date = None

    count: Optional[int] = None
    if count_raw is not None:
        try:
            count = int(count_raw)
        except Exception:
            count = None
        if count is not None and count <= 0:
            count = None

    if until_date:
        parts.append("UNTIL=" + _format_rrule_until(until_date, time_str, tz_name))
    elif count:
        parts.append(f"COUNT={count}")

    return ";".join(parts)


def recurring_to_rrule(item: Dict[str, Any]) -> Optional[str]:
    """
    recurring item -> RRULE 문자열 (recurrence 기반)
    """
    direct_rrule = _normalize_rrule_core(item.get("rrule"))
    if direct_rrule:
        item["rrule"] = direct_rrule
        return direct_rrule

    recurrence = _resolve_recurrence(item)
    if not recurrence:
        return None

    start_date_str = item.get("start_date")
    if not isinstance(start_date_str, str):
        return None

    return _build_rrule_core(recurrence, start_date_str, item.get("time"),
                             "Asia/Seoul")
