"""LLM 제공사 어댑터.

의도적으로 얇다. 프레임워크를 만들지 않는다 — 제공사별 코드는 "프롬프트를 보내고 텍스트와
토큰 수를 돌려주는" 부분에 한정하고, 프롬프트 구성·결과 파싱은 호출하는 쪽이 공용으로 갖는다.
그래야 모델을 바꿔 비교했을 때 그것이 모델 비교이지 프롬프트 비교가 되지 않는다.

키가 설정된 제공사만 `available_models()` 에 뜬다. 하나도 없으면 LLM 기능 전체가 비활성이고,
그 경우에도 서비스의 나머지(규칙 기반 매칭 포함)는 그대로 동작해야 한다.
"""

from .base import LlmError, LlmResult, available_models, complete

__all__ = ['LlmError', 'LlmResult', 'available_models', 'complete']
