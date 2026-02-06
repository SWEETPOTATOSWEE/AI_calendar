"""
Time Block Planner: 태스크 자동 시간 블록 배치
- 태스크(estimate + due)를 캘린더에 배치
- 사용자 선호 규칙 적용
- 가능한 분량 산출
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel


class Task(BaseModel):
    """태스크 모델"""
    id: str
    title: str
    estimate: int  # 예상 소요시간 (분)
    due: Optional[datetime]
    priority: int
    tags: List[str] = []


class UserPreferences(BaseModel):
    """사용자 선호 설정"""
    work_hours_start: int = 9  # 9시
    work_hours_end: int = 18  # 18시
    lunch_start: int = 12
    lunch_end: int = 13
    focus_time_preferred: str = "morning"  # morning, afternoon, evening
    min_block_size: int = 30  # 최소 블록 크기 (분)
    max_block_size: int = 120  # 최대 블록 크기 (분)
    break_between_blocks: int = 10  # 블록 간 휴식 (분)


class TimeBlock(BaseModel):
    """시간 블록"""
    task_id: str
    start: datetime
    end: datetime
    duration: int  # 분
    is_split: bool = False  # 분할된 블록인지
    split_index: Optional[int] = None


class TimeBlockPlanner:
    """태스크를 캘린더에 자동 배치"""
    
    def __init__(self, preferences: UserPreferences = None):
        self.preferences = preferences or UserPreferences()
    
    async def plan_tasks(
        self,
        tasks: List[Task],
        existing_events: List[Dict[str, Any]],
        date_range: tuple[datetime, datetime]
    ) -> Dict[str, Any]:
        """
        태스크들을 시간 블록으로 자동 배치
        
        Args:
            tasks: 배치할 태스크 목록
            existing_events: 기존 일정들
            date_range: (시작일, 종료일)
            
        Returns:
            {
                "blocks": List[TimeBlock],
                "unscheduled": List[Task],  # 배치 못한 태스크
                "warnings": List[str]  # 경고 메시지
            }
        """
        # TODO: 구현
        pass
    
    def find_available_slots(
        self,
        date_range: tuple[datetime, datetime],
        existing_events: List[Dict[str, Any]]
    ) -> List[tuple[datetime, datetime]]:
        """
        사용 가능한 시간대 찾기
        
        Returns:
            [(시작시간, 종료시간), ...]
        """
        # TODO: 구현
        pass
    
    def prioritize_tasks(self, tasks: List[Task]) -> List[Task]:
        """
        태스크 우선순위 정렬
        - 마감 임박
        - 우선순위 높음
        - 예상 소요시간 고려
        """
        # TODO: 구현
        pass
    
    def split_task_if_needed(
        self,
        task: Task,
        available_slots: List[tuple[datetime, datetime]]
    ) -> List[TimeBlock]:
        """
        필요시 태스크를 여러 블록으로 분할
        예: 90분 필요 → 45+45로 분할
        """
        # TODO: 구현
        pass
    
    def calculate_daily_capacity(
        self,
        date: datetime,
        existing_events: List[Dict[str, Any]]
    ) -> int:
        """
        특정 날짜의 남은 작업 가능 시간 계산 (분)
        """
        # TODO: 구현
        pass
    
    def suggest_adjustments(
        self,
        unscheduled: List[Task],
        date_range: tuple[datetime, datetime]
    ) -> List[str]:
        """
        배치 못한 태스크에 대한 조정 제안
        - 기한 조정
        - 우선순위 재정렬
        - 예상 시간 재평가
        """
        # TODO: 구현
        pass
