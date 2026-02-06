"""
Conflict Manager: Calendar ↔ Task 충돌 관리
- Hard/Soft 아이템 분류
- 충돌 감지 및 해결 전략
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from enum import Enum
from pydantic import BaseModel


class ItemStrength(str, Enum):
    """아이템 강도"""
    HARD = "hard"  # 회의, 병원, 약속 (옮기기 어려움)
    SOFT = "soft"  # 집중시간, 운동, 태스크 블록 (옮길 수 있음)
    UNSCHEDULED = "unscheduled"  # 태스크 (블록 없음, 기한만 존재)


class TimeSlot(BaseModel):
    """시간대"""
    start: datetime
    end: datetime
    strength: ItemStrength
    item_type: str  # "event" or "task_block"
    item_id: str
    title: str
    priority: Optional[int] = None


class ConflictResolution(BaseModel):
    """충돌 해결 방안"""
    conflict_type: str  # "hard_hard", "hard_soft", "soft_soft"
    original_slot: TimeSlot
    conflicting_slots: List[TimeSlot]
    suggestions: List[Dict[str, Any]]  # 해결 방안 목록


class ConflictManager:
    """Calendar와 Task 간 충돌 관리"""
    
    def __init__(self):
        pass
    
    async def detect_conflicts(
        self, 
        new_slot: TimeSlot,
        existing_slots: List[TimeSlot]
    ) -> List[TimeSlot]:
        """
        새 슬롯과 기존 슬롯 간 충돌 감지
        
        Args:
            new_slot: 추가하려는 시간대
            existing_slots: 기존 일정/블록들
            
        Returns:
            충돌하는 슬롯 목록
        """
        # TODO: 구현
        pass
    
    async def resolve_conflict(
        self,
        new_slot: TimeSlot,
        conflicts: List[TimeSlot],
        user_id: str
    ) -> ConflictResolution:
        """
        충돌 해결 방안 생성
        
        Returns:
            ConflictResolution with suggestions
        """
        # TODO: 구현
        pass
    
    def _resolve_hard_hard(
        self,
        new_slot: TimeSlot,
        conflict: TimeSlot
    ) -> List[Dict[str, Any]]:
        """Hard vs Hard: 대안 시간 2~3개 제시"""
        # TODO: 구현
        pass
    
    def _resolve_hard_soft(
        self,
        hard_slot: TimeSlot,
        soft_slot: TimeSlot
    ) -> List[Dict[str, Any]]:
        """Hard vs Soft: Soft 자동 이동 후보 생성"""
        # TODO: 구현
        pass
    
    def _resolve_soft_soft(
        self,
        slots: List[TimeSlot]
    ) -> List[Dict[str, Any]]:
        """Soft vs Soft: 우선순위/마감/최근성 기준 재배치"""
        # TODO: 구현
        pass
    
    def can_split_task(self, task_block: TimeSlot) -> bool:
        """태스크 블록 분할 가능 여부"""
        # TODO: 구현
        pass
    
    def suggest_buffer_time(
        self,
        slot: TimeSlot,
        buffer_minutes: int = 15
    ) -> TimeSlot:
        """이동/준비 시간 버퍼 추가"""
        # TODO: 구현
        pass
