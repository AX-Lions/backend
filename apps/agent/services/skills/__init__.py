"""
스킬 모음.

import 시점에 전역 레지스트리에 등록합니다. 등록을 호출부에서 하면 어떤 스킬이
살아 있는지가 흩어져, 프롬프트에는 있는데 실행이 안 되는 상황이 생깁니다.
"""
from .base import SkillBase, SkillContext, SkillKind, SkillResult
from .registry import SkillRegistry, registry
from .search_meeting import SearchMeetingSkill
from .search_records import SearchRecordsSkill
from .think import ThinkSkill

for _skill in (ThinkSkill(), SearchRecordsSkill(), SearchMeetingSkill()):
    registry.register(_skill)

__all__ = ["SkillBase", "SkillContext", "SkillKind", "SkillResult",
           "SkillRegistry", "registry",
           "ThinkSkill", "SearchRecordsSkill", "SearchMeetingSkill"]
