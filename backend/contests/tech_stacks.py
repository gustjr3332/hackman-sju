"""정규 기술 스택 목록 — 시드 데이터와 자유 문자열 → 정규 태그 매핑.

목록의 출발점은 **GitHub 에 실제로 존재하는 스택 표기**다. 언어는 GitHub 이 저장소 언어
막대에 쓰는 이름(Linguist)을 그대로 따르고, 프레임워크·도구는 GitHub Topics 에서 실제로
쓰이는 이름을 쓴다. 직접 목록을 지어내지 않은 이유는 참가자가 이미 그 표기에 익숙하고,
제출 저장소의 언어 통계와 같은 이름으로 맞춰 두면 나중에 대조하기도 쉽기 때문이다.

여기 있는 것은 **시드일 뿐 정본이 아니다** — 정본은 `TechStack` 테이블이고, 운영자가 대회
중에 Django admin 에서 목록을 고칠 수 있다(모델 docstring 참고).
"""

from django.core.cache import cache

from .models import TechStack

_CACHE_KEY = 'tech-stacks:index'
# 프로필 화면이 그대로 받아 쓰는 직렬화된 목록. 무효화를 한 곳에서 끝내려고 키를 여기 둔다.
LIST_CACHE_KEY = 'tech-stacks:list'
CACHE_SECONDS = 300

L = TechStack.Category.LANGUAGE
F = TechStack.Category.FRAMEWORK
T = TechStack.Category.TOOL

# (slug, 표시 이름, 분류, 별칭). 별칭에는 표기 흔들림(react.js/reactjs)과 한국어 통용 표기를
# 함께 넣는다 — 자기소개 자동 정리가 뽑아 오는 문자열이 바로 그 형태이기 때문이다.
SEED = [
    # ---- 언어 (GitHub Linguist 표기) ----
    ('javascript', 'JavaScript', L, ['js', '자바스크립트']),
    ('typescript', 'TypeScript', L, ['ts', '타입스크립트']),
    ('python', 'Python', L, ['파이썬', 'py']),
    ('java', 'Java', L, ['자바']),
    ('c', 'C', L, []),
    ('cpp', 'C++', L, ['c++', 'cplusplus', '씨쁠쁠']),
    ('csharp', 'C#', L, ['c#', 'c sharp', '씨샵']),
    ('go', 'Go', L, ['golang', '고랭']),
    ('rust', 'Rust', L, ['러스트']),
    ('kotlin', 'Kotlin', L, ['코틀린']),
    ('swift', 'Swift', L, ['스위프트']),
    ('objective-c', 'Objective-C', L, ['objc', 'objective c']),
    ('ruby', 'Ruby', L, ['루비']),
    ('php', 'PHP', L, []),
    ('dart', 'Dart', L, ['다트']),
    ('scala', 'Scala', L, []),
    ('r', 'R', L, []),
    ('matlab', 'MATLAB', L, ['매트랩']),
    ('shell', 'Shell', L, ['bash', 'sh', '셸', '쉘스크립트']),
    ('powershell', 'PowerShell', L, []),
    ('lua', 'Lua', L, []),
    ('perl', 'Perl', L, []),
    ('haskell', 'Haskell', L, []),
    ('elixir', 'Elixir', L, []),
    ('erlang', 'Erlang', L, []),
    ('clojure', 'Clojure', L, []),
    ('julia', 'Julia', L, []),
    ('groovy', 'Groovy', L, []),
    ('solidity', 'Solidity', L, ['솔리디티']),
    ('assembly', 'Assembly', L, ['어셈블리']),
    ('html', 'HTML', L, ['html5']),
    ('css', 'CSS', L, ['css3']),
    ('scss', 'SCSS', L, ['sass']),
    ('sql', 'SQL', L, []),
    ('jupyter-notebook', 'Jupyter Notebook', L, ['jupyter', '주피터']),
    ('zig', 'Zig', L, []),
    ('verilog', 'Verilog', L, []),

    # ---- 프레임워크 · 라이브러리 (GitHub Topics 표기) ----
    ('react', 'React', F, ['react.js', 'reactjs', '리액트']),
    ('nextjs', 'Next.js', F, ['next', 'next.js', '넥스트']),
    ('vue', 'Vue.js', F, ['vue.js', 'vuejs', '뷰']),
    ('nuxt', 'Nuxt', F, ['nuxt.js', 'nuxtjs']),
    ('angular', 'Angular', F, ['angular.js', '앵귤러']),
    ('svelte', 'Svelte', F, ['sveltekit', '스벨트']),
    ('nodejs', 'Node.js', F, ['node', 'node.js', '노드']),
    ('express', 'Express', F, ['express.js', 'expressjs']),
    ('nestjs', 'NestJS', F, ['nest', 'nest.js']),
    ('django', 'Django', F, ['장고']),
    ('drf', 'Django REST Framework', F, ['django rest framework', 'django-rest-framework']),
    ('flask', 'Flask', F, ['플라스크']),
    ('fastapi', 'FastAPI', F, ['fast api']),
    ('spring-boot', 'Spring Boot', F, ['spring', 'springboot', '스프링']),
    ('rails', 'Ruby on Rails', F, ['ruby on rails', 'rails']),
    ('laravel', 'Laravel', F, ['라라벨']),
    ('aspnet', 'ASP.NET', F, ['asp.net', 'dotnet', '.net']),
    ('flutter', 'Flutter', F, ['플러터']),
    ('react-native', 'React Native', F, ['react native', 'rn']),
    ('swiftui', 'SwiftUI', F, ['swift ui']),
    ('jetpack-compose', 'Jetpack Compose', F, ['compose', 'jetpack compose']),
    ('android', 'Android', F, ['안드로이드']),
    ('electron', 'Electron', F, []),
    ('tailwindcss', 'Tailwind CSS', F, ['tailwind', 'tailwind css']),
    ('bootstrap', 'Bootstrap', F, ['부트스트랩']),
    ('jquery', 'jQuery', F, ['제이쿼리']),
    ('threejs', 'Three.js', F, ['three.js', 'three']),
    ('unity', 'Unity', F, ['유니티']),
    ('unreal-engine', 'Unreal Engine', F, ['unreal', '언리얼']),
    ('tensorflow', 'TensorFlow', F, ['tf', '텐서플로우', '텐서플로']),
    ('pytorch', 'PyTorch', F, ['torch', '파이토치']),
    ('scikit-learn', 'scikit-learn', F, ['sklearn', 'scikit learn']),
    ('pandas', 'pandas', F, ['판다스']),
    ('numpy', 'NumPy', F, ['넘파이']),
    ('opencv', 'OpenCV', F, ['cv2', '오픈씨브이']),
    ('langchain', 'LangChain', F, ['랭체인']),
    ('huggingface', 'Hugging Face', F, ['hugging face', 'transformers', '허깅페이스']),
    ('streamlit', 'Streamlit', F, ['스트림릿']),
    ('gradio', 'Gradio', F, []),
    ('openai-api', 'OpenAI API', F, ['openai', 'gpt', 'chatgpt api']),
    ('vite', 'Vite', F, ['비트']),
    ('webpack', 'Webpack', F, []),
    ('redux', 'Redux', F, ['리덕스']),
    ('graphql', 'GraphQL', F, ['그래프큐엘']),
    ('prisma', 'Prisma', F, []),
    ('sqlalchemy', 'SQLAlchemy', F, []),
    ('socketio', 'Socket.IO', F, ['socket.io', 'websocket', '웹소켓']),

    # ---- 도구 · 인프라 ----
    ('git', 'Git', T, ['깃']),
    ('github-actions', 'GitHub Actions', T, ['github action', 'gh actions', '깃허브 액션']),
    ('docker', 'Docker', T, ['도커']),
    ('kubernetes', 'Kubernetes', T, ['k8s', '쿠버네티스']),
    ('aws', 'AWS', T, ['amazon web services', 'ec2', 's3', '아마존']),
    ('gcp', 'Google Cloud', T, ['google cloud platform', 'gcp']),
    ('azure', 'Azure', T, ['microsoft azure']),
    ('firebase', 'Firebase', T, ['파이어베이스']),
    ('supabase', 'Supabase', T, ['수파베이스']),
    ('vercel', 'Vercel', T, ['버셀']),
    ('netlify', 'Netlify', T, []),
    ('render', 'Render', T, []),
    ('postgresql', 'PostgreSQL', T, ['postgres', 'psql', '포스트그레스']),
    ('mysql', 'MySQL', T, ['마리아디비', 'mariadb']),
    ('mongodb', 'MongoDB', T, ['mongo', '몽고디비']),
    ('redis', 'Redis', T, ['레디스']),
    ('sqlite', 'SQLite', T, []),
    ('elasticsearch', 'Elasticsearch', T, ['elastic search']),
    ('nginx', 'Nginx', T, []),
    ('linux', 'Linux', T, ['ubuntu', '리눅스', '우분투']),
    ('figma', 'Figma', T, ['피그마']),
    ('postman', 'Postman', T, ['포스트맨']),
    ('notion', 'Notion', T, ['노션']),
    ('jira', 'Jira', T, ['지라']),
    ('jenkins', 'Jenkins', T, ['젠킨스']),
    ('terraform', 'Terraform', T, []),
    ('kafka', 'Apache Kafka', T, ['apache kafka', '카프카']),
    ('selenium', 'Selenium', T, ['셀레니움']),
    ('jest', 'Jest', T, []),
    ('pytest', 'pytest', T, []),
    ('cypress', 'Cypress', T, []),
    ('arduino', 'Arduino', T, ['아두이노']),
    ('raspberry-pi', 'Raspberry Pi', T, ['raspberry pi', '라즈베리파이']),
    ('blender', 'Blender', T, ['블렌더']),
    ('photoshop', 'Photoshop', T, ['포토샵', 'adobe photoshop']),
    ('illustrator', 'Illustrator', T, ['일러스트레이터']),
]


def _fold(value):
    """비교용 표기 정규화. 공백·점·하이픈·언더스코어 차이를 지운다.

    `React.js` / `react-js` / `react js` 를 같은 것으로 보기 위한 최소한의 처리다. 별칭
    목록이 이 차이까지 전부 담을 필요가 없어진다.
    """
    folded = str(value or '').strip().lower()
    for ch in (' ', '.', '-', '_', '/'):
        folded = folded.replace(ch, '')
    return folded


def _build_index():
    index = {}
    for stack in TechStack.objects.filter(is_active=True):
        index[_fold(stack.slug)] = stack.slug
        index[_fold(stack.name)] = stack.slug
        for alias in stack.aliases or []:
            index.setdefault(_fold(alias), stack.slug)
    return index


def alias_index():
    """{정규화 표기: slug}. 프로필 화면이 열릴 때마다 읽히므로 캐시한다.

    `LocMemCache`(프로세스 메모리)로 충분하다 — 워커를 늘렸을 때 무효화가 한 워커에만 닿는
    문제는 남지만, **낡은 스택 목록의 최악은 방금 추가한 태그가 몇 분간 안 보이는 것**이다.
    권한 판정 캐시를 보류한 이유(심사위원에서 뺀 사람이 결선 점수를 계속 봄)와는 위험의
    성격이 다르다.
    """
    index = cache.get(_CACHE_KEY)
    if index is None:
        index = _build_index()
        cache.set(_CACHE_KEY, index, CACHE_SECONDS)
    return index


def invalidate():
    cache.delete_many([_CACHE_KEY, LIST_CACHE_KEY])


def resolve(values):
    """자유 문자열 목록 → (정규 slug 목록, 매핑 못 한 원문 목록).

    매핑 못 한 값을 버리지 않고 돌려주는 것이 핵심이다 — 참가자가 실제로 쓴 기술이 사라지면
    안 되고, 운영자가 목록에 무엇을 추가해야 하는지도 이 값으로만 알 수 있다.
    """
    index = alias_index()
    known, unknown = [], []
    for raw in values or []:
        text = str(raw).strip()
        if not text:
            continue
        slug = index.get(_fold(text))
        if slug:
            if slug not in known:
                known.append(slug)
        elif text.lower() not in [u.lower() for u in unknown]:
            unknown.append(text)
    return known, unknown


def sync_seed():
    """시드 목록을 DB 에 반영한다(데이터 마이그레이션과 테스트가 함께 쓴다).

    이미 있는 행의 이름·분류는 덮어쓰지 않는다 — 운영자가 admin 에서 고쳐 둔 것을 배포가
    되돌리면 안 된다. 별칭만 빠진 것을 채운다.
    """
    existing = {s.slug: s for s in TechStack.objects.all()}
    created = 0
    for slug, name, category, aliases in SEED:
        stack = existing.get(slug)
        if stack is None:
            TechStack.objects.create(
                slug=slug, name=name, category=category, aliases=list(aliases)
            )
            created += 1
            continue
        merged = list(stack.aliases or [])
        for alias in aliases:
            if alias not in merged:
                merged.append(alias)
        if merged != (stack.aliases or []):
            stack.aliases = merged
            stack.save(update_fields=['aliases'])
    invalidate()
    return created
