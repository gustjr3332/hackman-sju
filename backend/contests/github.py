"""심사 도구용 GitHub 읽기 프록시.

프론트가 `api.github.com` 을 직접 부르면 브라우저 IP 기준 비인증 한도(시간당 60회)에
묶인다. 심사 중에는 같은 저장소를 심사위원 여러 명이 반복해서 열기 때문에 이 한도가
실사용에서 먼저 바닥난다. 여기서 서버가 대신 호출하면서 두 가지를 얻는다:

1. `GITHUB_TOKEN` 이 설정돼 있으면 한도가 시간당 5000회로 올라간다 (토큰은 서버에만 있고
   클라이언트로 나가지 않는다).
2. 응답을 캐시해 같은 저장소·같은 파일 요청이 GitHub 까지 가지 않는다.

주의: 토큰이 없으면 한도가 "브라우저마다 60회"에서 "서버 전체가 60회"로 좁아진다. 캐시가
대부분을 흡수하지만, 심사 도구를 실제로 쓰는 대회에서는 토큰을 넣어 두는 편이 안전하다.

프록시가 임의 URL 을 대신 열어주는 통로가 되지 않도록, 받는 것은 저장소 URL 뿐이고
owner/repo 를 뽑아 서버가 직접 `api.github.com` URL 을 조립한다. 로그인한 사용자만 쓸 수
있게 막아 공개 오픈 프록시가 되는 것도 피한다.
"""
import base64
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

GITHUB_API = 'https://api.github.com'
TIMEOUT_SECONDS = 10
# 파일이 아주 많은 저장소에서 트리 렌더링이 느려지지 않도록 상한을 둔다.
MAX_FILES = 500
# owner/repo 는 URL 경로에 그대로 들어가므로 GitHub 이 실제로 허용하는 문자만 통과시킨다.
NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')
# 페이지네이션 `Link` 헤더에서 마지막 페이지 번호만 뽑는다. URL 을 그대로 따라가지 않고 번호만
# 가져와 경로를 서버가 다시 조립한다 — 이 모듈이 임의 URL 을 여는 통로가 되지 않게 하는 원칙.
LAST_PAGE_RE = re.compile(r'[?&]page=(\d+)>;\s*rel="last"')


ERROR_DETAIL = {
    'not-found': '저장소를 찾을 수 없습니다 (비공개이거나 삭제된 저장소).',
    'rate-limit': 'GitHub 요청 한도에 걸렸습니다. 잠시 뒤 다시 시도해 주세요.',
    'error': 'GitHub에서 정보를 가져오지 못했습니다.',
}


class GithubUpstreamError(Exception):
    """GitHub 이 돌려준(또는 아예 닿지 못한) 실패. kind 는 프론트 안내 문구와 1:1이다."""

    def __init__(self, kind, http_status):
        super().__init__(ERROR_DETAIL[kind])
        self.kind = kind
        self.http_status = http_status


def parse_github_repo(url):
    """저장소 URL 에서 (owner, repo) 를 뽑는다. GitHub 저장소가 아니면 None."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ('http', 'https'):
        return None
    host = (parsed.hostname or '').lower()
    if host != 'github.com' and not host.endswith('.github.com'):
        return None
    parts = [p for p in parsed.path.split('/') if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], re.sub(r'\.git$', '', parts[1], flags=re.IGNORECASE)
    if not NAME_RE.match(owner) or not NAME_RE.match(repo):
        return None
    return owner, repo


def github_fetch(path):
    """`api.github.com{path}` 를 GET 해 `{'payload', 'link'}` 를 돌려준다. 결과는 캐시된다.

    `link` 는 페이지네이션 헤더 원문이다. 커밋 목록에서 **첫 커밋**을 찾으려면 마지막 페이지가
    몇 번인지 알아야 하는데, GitHub 은 그 정보를 본문이 아니라 `Link` 헤더로만 준다.
    """
    cache_key = f'github:{path}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'hackman-judging-tool',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    if settings.GITHUB_TOKEN:
        headers['Authorization'] = f'Bearer {settings.GITHUB_TOKEN}'

    request = urllib.request.Request(f'{GITHUB_API}{path}', headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode('utf-8'))
            link = (getattr(response, 'headers', None) or {}).get('Link') or ''
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise GithubUpstreamError('not-found', status.HTTP_404_NOT_FOUND) from exc
        if exc.code in (403, 429):
            raise GithubUpstreamError('rate-limit', status.HTTP_429_TOO_MANY_REQUESTS) from exc
        raise GithubUpstreamError('error', status.HTTP_502_BAD_GATEWAY) from exc
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise GithubUpstreamError('error', status.HTTP_502_BAD_GATEWAY) from exc

    entry = {'payload': payload, 'link': link}
    cache.set(cache_key, entry, settings.GITHUB_CACHE_SECONDS)
    return entry


def github_get(path):
    """본문만 필요한 대부분의 호출이 쓰는 얇은 래퍼."""
    return github_fetch(path)['payload']


def decode_base64_content(payload):
    content = payload.get('content')
    if not content or payload.get('encoding') != 'base64':
        raise GithubUpstreamError('error', status.HTTP_502_BAD_GATEWAY)
    try:
        return base64.b64decode(content).decode('utf-8', errors='replace')
    except (ValueError, TypeError) as exc:
        raise GithubUpstreamError('error', status.HTTP_502_BAD_GATEWAY) from exc


class GithubProxyView(APIView):
    """`GET /api/github/<resource>/?repo=<저장소 URL>` — resource 는 아래 네 가지.

    - `repo`   → `{"default_branch": ...}`
    - `readme` → `{"content": "..."}`  (base64 는 서버가 푼다)
    - `tree`   → `{"files": [{"path": ...}], "truncated": bool}`  (`branch` 필요)
    - `file`   → `{"content": "..."}`  (`path` 필요)
    - `commits`→ `{"first_commit_at": ..., "latest_commit_at": ..., "total_commits": N}`
    """

    # 공개 오픈 프록시가 되지 않도록 로그인한 사용자로 제한한다. 심사 도구는 어차피
    # 심사위원·운영자만 연다.
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, resource):
        ref = parse_github_repo(request.query_params.get('repo', ''))
        if ref is None:
            return Response(
                {'kind': 'error', 'detail': 'GitHub 저장소 URL이 아닙니다.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        owner, repo = ref
        try:
            handler = getattr(self, f'_get_{resource}')
        except AttributeError:
            return Response(
                {'kind': 'error', 'detail': '지원하지 않는 요청입니다.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        try:
            return Response(handler(request, owner, repo))
        except GithubUpstreamError as exc:
            return Response({'kind': exc.kind, 'detail': str(exc)}, status=exc.http_status)

    def _get_repo(self, request, owner, repo):
        data = github_get(f'/repos/{owner}/{repo}')
        return {'default_branch': data.get('default_branch', 'main')}

    def _get_readme(self, request, owner, repo):
        return {'content': decode_base64_content(github_get(f'/repos/{owner}/{repo}/readme'))}

    def _get_tree(self, request, owner, repo):
        branch = request.query_params.get('branch', '').strip()
        if not branch:
            raise GithubUpstreamError('error', status.HTTP_400_BAD_REQUEST)
        data = github_get(
            f'/repos/{owner}/{repo}/git/trees/{urllib.parse.quote(branch, safe="")}?recursive=1'
        )
        files = [item for item in data.get('tree', []) if item.get('type') == 'blob']
        return {
            'files': [{'path': f['path'], 'type': f['type']} for f in files[:MAX_FILES]],
            'truncated': bool(data.get('truncated')) or len(files) > MAX_FILES,
        }

    def _get_commits(self, request, owner, repo):
        """첫 커밋·마지막 커밋 시각과 커밋 수.

        표절 탐지가 아니다. 교내 대회에서 실제로 확인하고 싶은 것은 **"대회 시작 전에 이미
        만들어 둔 프로젝트를 냈는가"**이고, 그건 첫 커밋 시각 하나로 드러난다. 판정은 하지
        않는다 — 심사위원이 직접 보고 판단할 사실만 돌려준다(포크·이관·squash 처럼 시각이
        실제와 달라지는 경우가 있어 자동 판정은 위험하다).

        `per_page=1` 로 한 건만 받고 `Link` 헤더의 마지막 페이지 번호를 커밋 수로 쓴다.
        커밋이 수천 개인 저장소에서도 왕복 두 번이면 끝난다.
        """
        path = f'/repos/{owner}/{repo}/commits?per_page=1'
        entry = github_fetch(path)
        commits = entry['payload']
        if not isinstance(commits, list) or not commits:
            return {'first_commit_at': None, 'latest_commit_at': None, 'total_commits': 0}

        latest = commits[0]
        first = latest
        total = 1
        match = LAST_PAGE_RE.search(entry['link'] or '')
        if match:
            total = int(match.group(1))
            last_page = github_get(f'{path}&page={total}')
            if isinstance(last_page, list) and last_page:
                first = last_page[-1]

        return {
            'first_commit_at': _commit_date(first),
            'latest_commit_at': _commit_date(latest),
            'total_commits': total,
        }

    def _get_file(self, request, owner, repo):
        path = request.query_params.get('path', '')
        segments = [s for s in path.split('/') if s]
        # `..` 이 섞여 들어와 저장소 밖(다른 API 경로)을 가리키지 않게 막는다.
        if not segments or any(s == '..' for s in segments):
            raise GithubUpstreamError('error', status.HTTP_400_BAD_REQUEST)
        quoted = '/'.join(urllib.parse.quote(s, safe='') for s in segments)
        return {'content': decode_base64_content(
            github_get(f'/repos/{owner}/{repo}/contents/{quoted}')
        )}


def _commit_date(commit):
    """커밋이 작성된 시각. 작성자 시각이 없으면 커미터 시각으로 떨어진다."""
    if not isinstance(commit, dict):
        return None
    detail = commit.get('commit') or {}
    for who in ('author', 'committer'):
        date = (detail.get(who) or {}).get('date')
        if date:
            return date
    return None
