from __future__ import annotations

import copy
from typing import Any, Dict

_session_preferences: Dict[str, Dict[str, Any]] = {}
_pending_clarifications: Dict[str, Dict[str, Any]] = {}


def get_preferences(session_id: str) -> Dict[str, Any]:
  stored = _session_preferences.get(session_id)
  if not isinstance(stored, dict):
    return {}
  return dict(stored)


def get_pending_clarification(session_id: str) -> Dict[str, Any]:
  stored = _pending_clarifications.get(session_id)
  if not isinstance(stored, dict):
    return {}
  return copy.deepcopy(stored)


def set_pending_clarification(session_id: str, payload: Dict[str, Any]) -> None:
  if not session_id:
    return
  if not isinstance(payload, dict):
    return
  _pending_clarifications[session_id] = copy.deepcopy(payload)


def clear_pending_clarification(session_id: str) -> None:
  if not session_id:
    return
  _pending_clarifications.pop(session_id, None)
