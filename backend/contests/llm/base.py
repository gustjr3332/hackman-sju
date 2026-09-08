"""제공사 공용 인터페이스.

SDK 는 지연 임포트한다 — 세 제공사 SDK 를 모두 설치하지 않아도 나머지가 동작해야 하고,
Render 무료 플랜의 콜드 스타트에 쓰지도 않을 임포트를 얹지 않기 위해서다.
"""

import json
import re

from django.conf import settings


class LlmError(Exception):
    """제공사 호출 실패. 호출부는 이걸 잡아 기능을 끄지 말고 그 항목만 실패로 남긴다."""


class LlmResult:
    def __init__(self, text, input_tokens=0, output_tokens=0, model=''):
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.model = model


# 제공사별 기본 모델. 어느 것이 실제로 쓸 만한지는 실제 데이터로 재봐야 알 수 있으므로,
# 여기 값은 출발점일 뿐이고 호출부에서 모델을 지정해 바꿀 수 있다.
PROVIDERS = {
    'anthropic': {
        'label': 'Anthropic',
        'key_setting': 'ANTHROPIC_API_KEY',
        'default_model': 'claude-opus-5',
    },
    'openai': {
        'label': 'OpenAI',
        'key_setting': 'OPENAI_API_KEY',
        'default_model': 'gpt-5.6-luna',
    },
    'google': {
        'label': 'Google',
        'key_setting': 'GOOGLE_API_KEY',
        # 무료 등급 키는 pro 계열 quota 가 0 이라 429 가 난다. 프로필 추출은 짧은 글에서
        # 태그를 뽑는 일이라 flash 로 충분하고, 무료 등급에서도 실제로 동작한다.
        'default_model': 'gemini-3.5-flash',
    },
}

# 호출 제한 시간(초). 제공사가 응답하지 않으면 워커가 그동안 통째로 묶인다 — gunicorn 워커가
# 1개(`Procfile` 에 `-w` 없음)라 한 요청이 멈추면 스코어보드 폴링까지 전부 멈춘다.
# 프로필 추출은 짧은 글에서 태그를 뽑는 일이라 정상이면 수 초면 끝난다.
REQUEST_TIMEOUT_SECONDS = 30


def _key(provider):
    return getattr(settings, PROVIDERS[provider]['key_setting'], '') or ''


def available_models():
    """키가 설정된 제공사만. 프론트의 모델 선택기가 이걸 그대로 쓴다."""
    return [
        {
            'provider': name,
            'label': meta['label'],
            'default_model': meta['default_model'],
        }
        for name, meta in PROVIDERS.items()
        if _key(name)
    ]


def complete(prompt, provider=None, model=None, max_output_tokens=2048):
    """프롬프트 하나를 보내고 텍스트를 받는다. 제공사별로 다른 것은 여기 아래뿐이다."""
    if provider is None:
        candidates = available_models()
        if not candidates:
            raise LlmError('설정된 LLM API 키가 없습니다.')
        provider = candidates[0]['provider']
    if provider not in PROVIDERS:
        raise LlmError(f'알 수 없는 제공사: {provider}')
    api_key = _key(provider)
    if not api_key:
        raise LlmError(f'{PROVIDERS[provider]["label"]} API 키가 설정되지 않았습니다.')
    model = model or PROVIDERS[provider]['default_model']

    if provider == 'anthropic':
        return _anthropic(prompt, api_key, model, max_output_tokens)
    if provider == 'openai':
        return _openai(prompt, api_key, model, max_output_tokens)
    return _google(prompt, api_key, model, max_output_tokens)


def _anthropic(prompt, api_key, model, max_output_tokens):
    try:
        import anthropic
    except ImportError as exc:
        raise LlmError('anthropic SDK 가 설치되지 않았습니다.') from exc
    try:
        client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
        res = client.messages.create(
            model=model,
            max_tokens=max_output_tokens,
            messages=[{'role': 'user', 'content': prompt}],
        )
        text = ''.join(b.text for b in res.content if getattr(b, 'type', '') == 'text')
        return LlmResult(text, res.usage.input_tokens, res.usage.output_tokens, model)
    except Exception as exc:  # SDK 예외 계층이 제공사마다 달라 여기서 통일한다
        raise LlmError(str(exc)) from exc


def _openai(prompt, api_key, model, max_output_tokens):
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise LlmError('openai SDK 가 설치되지 않았습니다.') from exc
    try:
        client = OpenAI(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)
        res = client.responses.create(
            model=model, input=prompt, max_output_tokens=max_output_tokens
        )
        usage = getattr(res, 'usage', None)
        return LlmResult(
            res.output_text,
            getattr(usage, 'input_tokens', 0) or 0,
            getattr(usage, 'output_tokens', 0) or 0,
            model,
        )
    except Exception as exc:
        raise LlmError(str(exc)) from exc


def _google(prompt, api_key, model, max_output_tokens):
    try:
        from google import genai
    except ImportError as exc:
        raise LlmError('google-genai SDK 가 설치되지 않았습니다.') from exc
    try:
        client = genai.Client(
            api_key=api_key,
            # google-genai 는 밀리초 단위로 받는다.
            http_options={'timeout': REQUEST_TIMEOUT_SECONDS * 1000},
        )
        res = client.models.generate_content(
            model=model,
            contents=prompt,
            config={'max_output_tokens': max_output_tokens},
        )
        usage = getattr(res, 'usage_metadata', None)
        return LlmResult(
            res.text or '',
            getattr(usage, 'prompt_token_count', 0) or 0,
            getattr(usage, 'candidates_token_count', 0) or 0,
            model,
        )
    except Exception as exc:
        raise LlmError(str(exc)) from exc


def parse_json_object(text):
    """응답에서 JSON 객체를 꺼낸다.

    모델이 ```json 펜스나 앞뒤 설명을 붙이는 일이 흔하다. 제공사마다 그 습관이 달라서,
    비교가 성립하려면 파싱이 공용이어야 한다.
    """
    if not text:
        raise LlmError('빈 응답')
    fenced = re.search(r'```(?:json)?\s*(.*?)```', text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find('{'), candidate.rfind('}')
    if start == -1 or end <= start:
        raise LlmError('응답에서 JSON 을 찾지 못했습니다.')
    try:
        return json.loads(candidate[start:end + 1])
    except json.JSONDecodeError as exc:
        raise LlmError(f'JSON 파싱 실패: {exc}') from exc
