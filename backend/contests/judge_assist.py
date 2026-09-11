"""심사 보조: 제출 저장소를 미리 읽어 심사위원 앞에 정리해 둔다.

심사위원은 팀당 10분 안에 저장소를 다 읽을 수 없어 사실상 README 와 데모 링크만 보고 점수를
매기게 된다. 저장소를 읽어오는 인프라(`github.py` 프록시)는 이미 있으므로, 없는 것은 "읽은
내용을 요약해 심사위원 앞에 놓는 단계"뿐이다.

**점수는 제안하지 않는다.** 제안 점수를 띄우면 심사위원이 거기에 닻을 내려 결국 모델이
채점하는 것과 같아진다. 출력은 "무엇이 있는지"까지고, 근거 파일 경로를 반드시 붙여 심사위원이
직접 열어 확인할 수 있게 한다.

**언제 도나 — 요청 경로가 아니라 백그라운드 스레드에서 돈다.** gunicorn 워커가 1개라
동기로 부르면 수십 초 동안 서비스 전체가 멈춘다(스코어보드 폴링까지). 엔드포인트는 분석
'대기' 행만 만들고 바로 응답하며, 화면은 결과를 폴링해서 받는다. Celery·RQ 를 들이지 않고
지금 인프라에서 워커를 막지 않는 가장 가벼운 방법이다.

그 대신 치르는 비용을 분명히 해 둔다: 프로세스가 재시작되면(배포, Render 무료 인스턴스의
슬립) 진행 중이던 분석은 사라지고 행은 '분석 중'으로 남는다. 다시 실행하면 그만이라
복구 절차가 필요 없는 종류의 실패이고, 대회 당일이 아니라 심사 전에 돌리는 작업이다.
"""

import threading

from django.db import connection
from django.utils import timezone

from .github import GithubUpstreamError, decode_base64_content, github_get, parse_github_repo
from .llm import LlmError, complete
from .llm.base import parse_json_object
from .models import SubmissionReview

# 저장소를 통째로 넣지 않는다. 소스만 골라 상한을 두고, 잘렸으면 결과에 그 사실을 남긴다
# (조용히 자르지 않는다).
MAX_FILES = 30
MAX_FILE_CHARS = 6000
MAX_TOTAL_CHARS = 120_000
MAX_README_CHARS = 8000

# 백그라운드에서 도는 호출이라 요청 경로의 30초 제한을 따르지 않는다. 수만 토큰을 읽는
# 호출은 30초로는 정상 응답도 못 받는다.
ANALYSIS_TIMEOUT_SECONDS = 180
# 출력 상한을 넉넉히 잡는다. 저장소 수만 토큰을 읽고 답하는 모델은 본문을 내기 전에 추론에
# 예산을 먼저 쓰기 때문에, 상한이 빠듯하면 본문이 아예 비어 돌아온다(실제로 3000 에서 겪었다).
MAX_OUTPUT_TOKENS = 8000

SOURCE_SUFFIXES = (
    '.py', '.js', '.jsx', '.ts', '.tsx', '.java', '.kt', '.swift', '.dart', '.go', '.rs',
    '.rb', '.php', '.c', '.h', '.cpp', '.cs', '.m', '.scala', '.ex', '.exs', '.vue',
    '.svelte', '.html', '.css', '.scss', '.sql', '.sh', '.yml', '.yaml', '.toml',
)

# 프로젝트가 무엇으로 만들어졌는지 한 줄로 알려주는 파일들. 크기 대비 정보량이 가장 높아
# 우선 읽는다.
MANIFEST_NAMES = (
    'package.json', 'requirements.txt', 'pyproject.toml', 'build.gradle', 'build.gradle.kts',
    'pom.xml', 'go.mod', 'cargo.toml', 'gemfile', 'composer.json', 'pubspec.yaml',
    'dockerfile', 'docker-compose.yml', 'procfile',
)

# 사람이 쓰지 않은 파일. 토큰만 먹고 판단 근거가 되지 않는다.
EXCLUDE_PARTS = (
    'node_modules/', 'vendor/', 'dist/', 'build/', '.venv/', 'venv/', '__pycache__/',
    'migrations/', '.next/', 'coverage/', 'site-packages/', '.git/',
)
EXCLUDE_NAMES = (
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'poetry.lock', 'gemfile.lock',
    'composer.lock', 'cargo.lock',
)

PROMPT = """너는 해커톤 심사위원을 돕는 분석가다. 아래 제출물의 저장소 내용을 읽고,
심사위원이 10분 안에 파악해야 할 것을 정리하라.

**절대 점수를 매기거나 제안하지 마라.** 잘했다/못했다는 평가도 하지 마라. 네가 하는 일은
"무엇이 있고 무엇이 없는지"를 사실로 적는 것이고, 판단은 심사위원이 한다.

규칙:
- 저장소에 실제로 있는 코드만 근거로 삼는다. README 의 주장과 코드가 다르면 그 차이를 적는다.
- 모든 항목에 근거 파일 경로(paths)를 붙인다. 경로 없이 주장하지 마라.
- 읽은 범위에서 확인할 수 없으면 그렇게 적는다. 추측해서 채우지 마라.

findings 의 kind 는 셋 중 하나다:
- "implemented": 실제로 동작하는 코드가 있는 기능
- "shell": 이름·라우트·함수는 있는데 본문이 비어 있거나 더미 값을 돌려주는 부분
- "note": 심사위원이 알아야 할 나머지 사실 (외부 API 의존, 하드코딩된 키, 참고할 설계 판단 등)

JSON 객체 하나만 출력하고 다른 말은 하지 마라:
{{"summary": "이 프로젝트가 실제로 하는 일 3~5문장",
  "stack": ["확인된 기술 스택"],
  "findings": [{{"kind": "implemented", "title": "짧은 제목", "detail": "설명", "paths": ["경로"]}}]}}

제출물 제목: {title}
제출물 설명: {description}

=== README ===
{readme}

=== 파일 목록 ({file_count}개 중 표시) ===
{file_list}

=== 소스 내용 ===
{sources}
"""


class RepoUnavailable(Exception):
    """저장소를 읽지 못했다. 분석 실패로 남기고 심사는 수동으로 계속 진행한다."""


def _is_source(path):
    lower = path.lower()
    if any(part in lower for part in EXCLUDE_PARTS):
        return False
    name = lower.rsplit('/', 1)[-1]
    if name in EXCLUDE_NAMES:
        return False
    return name in MANIFEST_NAMES or lower.endswith(SOURCE_SUFFIXES)


def _priority(path):
    """먼저 읽을 순서. 매니페스트 → 얕은 경로 → 사전순.

    깊이를 쓰는 이유는 진입점(main/App/settings)이 대개 얕은 곳에 있기 때문이다. 상한에
    걸려 잘리더라도 프로젝트의 뼈대부터 들어가게 된다.
    """
    name = path.lower().rsplit('/', 1)[-1]
    return (0 if name in MANIFEST_NAMES else 1, path.count('/'), path.lower())


def collect_repo_context(repo_url):
    """저장소에서 프롬프트에 넣을 내용을 모은다.

    저장소가 비공개거나 없으면 `RepoUnavailable`. 분석 실패가 심사를 막아서는 안 되므로
    호출부는 이걸 잡아 실패 상태로 기록만 한다.
    """
    ref = parse_github_repo(repo_url)
    if ref is None:
        raise RepoUnavailable('GitHub 저장소 URL이 아닙니다.')
    owner, repo = ref

    try:
        meta = github_get(f'/repos/{owner}/{repo}')
        branch = meta.get('default_branch') or 'main'
        tree = github_get(f'/repos/{owner}/{repo}/git/trees/{branch}?recursive=1')
    except GithubUpstreamError as exc:
        raise RepoUnavailable(str(exc)) from exc

    try:
        readme = decode_base64_content(github_get(f'/repos/{owner}/{repo}/readme'))
    except GithubUpstreamError:
        # README 가 없는 저장소도 있다. 그것만으로 분석을 포기할 이유는 안 된다.
        readme = ''

    all_paths = [
        item['path'] for item in tree.get('tree', [])
        if item.get('type') == 'blob' and item.get('path')
    ]
    candidates = sorted((p for p in all_paths if _is_source(p)), key=_priority)

    sources, read_paths = [], []
    total = 0
    truncated = bool(tree.get('truncated')) or len(candidates) > MAX_FILES
    for path in candidates[:MAX_FILES]:
        if total >= MAX_TOTAL_CHARS:
            truncated = True
            break
        try:
            content = decode_base64_content(
                github_get(f'/repos/{owner}/{repo}/contents/{_quote_path(path)}')
            )
        except GithubUpstreamError:
            continue
        if len(content) > MAX_FILE_CHARS:
            content = content[:MAX_FILE_CHARS] + '\n... (이하 생략)'
            truncated = True
        sources.append(f'--- {path} ---\n{content}')
        read_paths.append(path)
        total += len(content)

    return {
        'readme': readme[:MAX_README_CHARS],
        'file_list': '\n'.join(all_paths[:200]),
        'file_count': len(all_paths),
        'sources': '\n\n'.join(sources),
        'read_paths': read_paths,
        'truncated': truncated,
    }


def _quote_path(path):
    from urllib.parse import quote
    return '/'.join(quote(s, safe='') for s in path.split('/') if s)


def build_prompt(submission, context):
    return PROMPT.format(
        title=submission.title,
        description=(submission.description or '')[:2000],
        readme=context['readme'] or '(README 없음)',
        file_count=context['file_count'],
        file_list=context['file_list'],
        sources=context['sources'] or '(읽을 수 있는 소스 파일이 없음)',
    )


def analyze_submission(submission, provider=None, model=None):
    """제출물 하나를 분석해 `SubmissionReview` 에 저장한다.

    예외를 밖으로 내보내지 않는다 — 한 팀의 분석 실패가 나머지 팀의 분석이나 심사를 막으면
    안 된다. 실패는 그 행의 상태로 남고, 심사 화면은 그대로 수동 심사로 진행한다.

    예상 못 한 예외까지 여기서 잡는 이유는 이 함수가 백그라운드 스레드에서 돌기 때문이다 —
    스레드에서 예외가 나면 아무도 보지 못한 채 행이 '분석 중'으로 영원히 남는다.
    """
    review = ensure_pending_review(submission, provider, model)
    try:
        return _analyze(review, submission)
    except Exception as exc:  # noqa: BLE001 - 스레드 밖으로 새어 나가면 상태가 영원히 pending
        return _fail(review, f'분석 중 예상치 못한 오류: {exc}')


def _analyze(review, submission):
    if not submission.repo_url:
        return _fail(review, '제출물에 GitHub 저장소 주소가 없습니다.')

    try:
        context = collect_repo_context(submission.repo_url)
    except RepoUnavailable as exc:
        return _fail(review, f'저장소를 읽을 수 없었습니다: {exc}')

    try:
        result = complete(
            build_prompt(submission, context),
            provider=review.provider or None,
            model=review.model or None,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            timeout=ANALYSIS_TIMEOUT_SECONDS,
        )
        data = parse_json_object(result.text)
    except LlmError as exc:
        return _fail(review, str(exc))

    findings = _clean_findings(data.get('findings'))
    cited = []
    for item in findings:
        for path in item['paths']:
            if path not in cited:
                cited.append(path)

    review.status = SubmissionReview.Status.DONE
    review.summary = str(data.get('summary') or '').strip()
    review.findings = findings
    review.stack = [str(s).strip() for s in (data.get('stack') or []) if str(s).strip()][:20]
    review.cited_paths = cited
    review.truncated = context['truncated']
    review.files_read = len(context['read_paths'])
    review.input_tokens = result.input_tokens or 0
    review.output_tokens = result.output_tokens or 0
    review.model = result.model or review.model
    review.error = ''
    review.submission_seen_at = submission.submitted_at
    review.save()
    return review


def ensure_pending_review(submission, provider=None, model=None):
    """분석 대기 행을 만들어 둔다. 화면이 "분석 중"을 바로 볼 수 있어야 하기 때문이다."""
    from .llm.base import PROVIDERS, available_models

    if not provider:
        candidates = available_models()
        provider = candidates[0]['provider'] if candidates else ''
    model = model or (PROVIDERS[provider]['default_model'] if provider in PROVIDERS else '')

    review, _ = SubmissionReview.objects.update_or_create(
        submission=submission,
        provider=provider,
        model=model,
        defaults={
            'status': SubmissionReview.Status.PENDING,
            'error': '',
        },
    )
    return review


def _fail(review, message):
    review.status = SubmissionReview.Status.FAILED
    review.error = str(message)[:500]
    review.submission_seen_at = timezone.now()
    review.save(update_fields=['status', 'error', 'submission_seen_at', 'updated_at'])
    return review


def _clean_findings(values, limit=20):
    """모델이 돌려준 항목을 화면이 믿고 쓸 수 있는 모양으로 맞춘다.

    경로 없는 항목도 버리지 않고 남긴다 — 근거가 없다는 사실 자체를 심사위원이 봐야 한다.
    """
    if not isinstance(values, list):
        return []
    kinds = {'implemented', 'shell', 'note'}
    out = []
    for item in values[:limit]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get('kind') or '').strip().lower()
        paths = item.get('paths')
        out.append({
            'kind': kind if kind in kinds else 'note',
            'title': str(item.get('title') or '').strip()[:200],
            'detail': str(item.get('detail') or '').strip()[:2000],
            'paths': [str(p).strip() for p in paths if str(p).strip()][:10]
            if isinstance(paths, list) else [],
        })
    return out


def analyze_contest(contest, provider=None, model=None):
    """대회의 모든 제출물을 순차로 분석한다.

    3사의 Batch·비동기 방식이 제각각이라 1차에서는 Batch 를 쓰지 않는다. 순차 실행이라
    20팀이면 수 분 걸리지만, 심사 전에 운영자가 한 번 돌리는 작업이고 백그라운드 스레드에서
    도는 동안에도 서비스는 그대로 응답한다.
    """
    from .models import Submission

    submissions = Submission.objects.filter(
        team__contest=contest
    ).exclude(repo_url='').select_related('team')
    return [analyze_submission(s, provider, model) for s in submissions]


def run_in_background(func, *args, **kwargs):
    """요청 경로 밖에서 돌린다. 워커 1개짜리 서버에서 LLM 호출이 서비스를 묶지 않게.

    스레드마다 DB 커넥션이 따로 열리므로 끝날 때 반드시 닫는다 — 닫지 않으면 Supabase 무료
    플랜의 연결 수 상한을 분석 실행 횟수만큼 갉아먹는다.
    """
    def runner():
        try:
            func(*args, **kwargs)
        finally:
            connection.close()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread
