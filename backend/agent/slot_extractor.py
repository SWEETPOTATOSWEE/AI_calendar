"""
Slot Extractor + Validator: 엔티티 추출 및 검증
- 날짜/시간/기간/제목/장소/기한/소요시간 등 추출
- 누락 시 질문 생성
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
from pydantic import BaseModel


class Slot(BaseModel):
    """추출된 슬롯"""
    name: str
    value: Any
    confidence: float
    source: str  # "user_input", "context", "default"


class SlotRequirements(BaseModel):
    """의도별 필수/선택 슬롯"""
    required: List[str]
    optional: List[str]


class SlotExtractor:
    """사용자 입력에서 필요한 정보 추출"""
    
    # 의도별 필수 슬롯 정의
    SLOT_REQUIREMENTS = {
        "calendar_create": SlotRequirements(
            required=["title", "start_time"],
            optional=["end_time", "location", "attendees", "recurrence"]
        ),
        "task_create": SlotRequirements(
            required=["title"],
            optional=["due", "priority", "estimate", "tags"]
        ),
        # ... 다른 의도들
    }
    
    def __init__(self):
        pass
    
    async def extract(
        self, 
        user_input: str, 
        intent: str,
        context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        사용자 입력에서 슬롯 추출
        
        Args:
            user_input: 사용자 입력
            intent: 분류된 의도
            context: 대화 컨텍스트
            
        Returns:
            {
                "slots": Dict[str, Slot],
                "missing": List[str],  # 누락된 필수 슬롯
                "confidence": float
            }
        """
        # TODO: LLM 또는 NER로 구현
        pass
    
    def validate(self, slots: Dict[str, Slot], intent: str) -> Dict[str, Any]:
        """
        추출된 슬롯 검증
        
        Returns:
            {
                "valid": bool,
                "missing": List[str],
                "questions": List[str]  # 사용자에게 물어볼 질문들
            }
        """
        # TODO: 구현
        pass
    
    def generate_questions(self, missing: List[str], intent: str) -> List[str]:
        """누락된 슬롯에 대한 질문 생성"""
        # TODO: 구현
        pass
