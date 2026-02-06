"""
Intent Router: 사용자 의도 분류
- 일정 추가/수정/조회 vs 할 일 추가/완료/조회 vs 계획(시간블록)
"""

from typing import Dict, Any
from enum import Enum


class Intent(str, Enum):
    """사용자 의도 타입"""
    # Calendar 관련
    CALENDAR_CREATE = "calendar_create"
    CALENDAR_UPDATE = "calendar_update"
    CALENDAR_MOVE = "calendar_move"
    CALENDAR_CANCEL = "calendar_cancel"
    CALENDAR_QUERY = "calendar_query"
    CALENDAR_FREEBUSY = "calendar_freebusy"
    
    # Task 관련
    TASK_CREATE = "task_create"
    TASK_UPDATE = "task_update"
    TASK_COMPLETE = "task_complete"
    TASK_QUERY = "task_query"
    
    # 계획/시간블록
    PLAN_TIMEBLOCK = "plan_timeblock"
    PLAN_OPTIMIZE = "plan_optimize"
    
    # 기타
    UNKNOWN = "unknown"


class IntentRouter:
    """사용자 입력에서 의도를 추출"""
    
    def __init__(self):
        pass
    
    async def classify(self, user_input: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        사용자 입력에서 의도 분류
        
        Args:
            user_input: 사용자의 자연어 입력
            context: 대화 컨텍스트
            
        Returns:
            {
                "intent": Intent,
                "confidence": float,  # 0-1
                "alternatives": List[Dict]  # 다른 가능한 의도들
            }
        """
        # TODO: LLM 또는 규칙 기반으로 구현
        pass
