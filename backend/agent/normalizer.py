"""
Normalizer: 표현 통일 및 정규화
- "내일 3시" → ISO datetime
- 30분 단위 라운딩
- 상대 시간 → 절대 시간 변환
"""

from typing import Optional, Tuple
from datetime import datetime, timedelta
import re


class Normalizer:
    """날짜/시간 표현 정규화"""
    
    def __init__(self, default_timezone: str = "Asia/Seoul"):
        self.default_timezone = default_timezone
    
    def normalize_datetime(
        self, 
        time_expr: str, 
        reference_time: Optional[datetime] = None,
        round_minutes: int = 30
    ) -> Optional[datetime]:
        """
        자연어 시간 표현을 ISO datetime으로 변환
        
        Args:
            time_expr: "내일 3시", "다음주 월요일", "2시간 후" 등
            reference_time: 기준 시각 (기본: 현재)
            round_minutes: 반올림 분 단위 (기본: 30분)
            
        Returns:
            정규화된 datetime 객체
        """
        # TODO: 구현
        pass
    
    def normalize_duration(self, duration_expr: str) -> Optional[int]:
        """
        기간 표현을 분 단위로 변환
        
        Args:
            duration_expr: "30분", "1시간", "90분" 등
            
        Returns:
            분 단위 정수
        """
        # TODO: 구현
        pass
    
    def normalize_recurrence(self, recurrence_expr: str) -> Optional[dict]:
        """
        반복 표현을 RRULE 형식으로 변환
        
        Args:
            recurrence_expr: "매일", "매주 월요일", "격주" 등
            
        Returns:
            RRULE 딕셔너리
        """
        # TODO: 구현
        pass
    
    def round_time(self, dt: datetime, minutes: int = 30) -> datetime:
        """시간을 지정된 분 단위로 반올림"""
        # TODO: 구현
        pass
    
    def parse_relative_date(self, expr: str, reference: datetime) -> Optional[datetime]:
        """
        상대 날짜 표현 파싱
        "내일", "모레", "다음주", "이번주 금요일" 등
        """
        # TODO: 구현
        pass
