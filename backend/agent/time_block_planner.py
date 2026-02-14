from __future__ import annotations

from typing import Any, Dict, List


def keep_plan_as_is(plan: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
  """Return the incoming plan unchanged until time-block optimization is added."""
  return list(plan)
