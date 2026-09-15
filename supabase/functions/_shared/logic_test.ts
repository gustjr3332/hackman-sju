// 실행: npx deno test supabase/functions/_shared/logic_test.ts
import { assertEquals, assertThrows } from 'jsr:@std/assert@1';
import {
  byPriority, cleanFindings, cleanTags, isSource, lastPage, parseGithubRepo, parseJsonObject,
  resolveStacks, scoreFit, snapshot,
} from './logic.ts';

const stacks = [
  { slug: 'react', name: 'React', aliases: ['react.js', 'reactjs', '리액트'] },
  { slug: 'python', name: 'Python', aliases: ['파이썬'] },
];

Deno.test('별칭은 한 스택으로 접히고 목록 밖 값은 버리지 않는다', () => {
  assertEquals(resolveStacks(['React.js', '리액트', 'react-js', '파이썬', '사내프레임워크'], stacks),
    [['react', 'python'], ['사내프레임워크']]);
});

Deno.test('태그 정리: 소문자·중복 제거·상한', () => {
  assertEquals(cleanTags(['React', 'react', ' Python ', '']), ['react', 'python']);
  assertEquals(cleanTags('not-a-list'), []);
});

Deno.test('JSON 펜스와 앞뒤 설명을 견딘다', () => {
  assertEquals(parseJsonObject('설명\n```json\n{"a": 1}\n```'), { a: 1 });
  assertThrows(() => parseJsonObject(''), Error, '빈 응답');
  assertThrows(() => parseJsonObject('없음'), Error, 'JSON 을 찾지 못했습니다');
});

Deno.test('팀 추천 점수는 matching.py 와 같다', () => {
  const team = snapshot([{ roles: ['backend'], skills: ['python'], interests: ['교육'] }]);
  const r = scoreFit({ roles: ['frontend'], skills: ['react'], interests: ['교육'] }, team);
  // 역할 45 + 관심사 25 + 자리 20*3/4 + 스택 10 = 95
  assertEquals(r.score, 95);
  assertEquals(r.reasons, ['팀에 없는 역할: frontend', '관심사가 겹침: 교육', '1명 — 3자리 남음']);
  // 아무것도 안 적은 사람은 절반씩: 22.5 + 12.5 + 20 + 5
  assertEquals(scoreFit({ roles: [], skills: [], interests: [] }, snapshot([])).score, 60);
});

Deno.test('GitHub 저장소 URL 만 받는다', () => {
  assertEquals(parseGithubRepo('https://github.com/pallets/flask.git'), ['pallets', 'flask']);
  assertEquals(parseGithubRepo('https://gitlab.com/a/b'), null);
  assertEquals(parseGithubRepo('https://github.com/only-owner'), null);
  assertEquals(parseGithubRepo('https://github.com/../etc'), null);
});

Deno.test('Link 헤더에서 마지막 페이지 번호', () => {
  assertEquals(lastPage('<https://api.github.com/x?per_page=1&page=2>; rel="next", <https://api.github.com/x?per_page=1&page=42>; rel="last"'), 42);
  assertEquals(lastPage(''), null);
});

Deno.test('락파일·의존성 폴더는 빼고 매니페스트를 먼저 읽는다', () => {
  assertEquals(isSource('src/App.tsx'), true);
  assertEquals(isSource('package-lock.json'), false);
  assertEquals(isSource('node_modules/react/index.js'), false);
  assertEquals(isSource('contests/migrations/0001_initial.py'), false);
  assertEquals(isSource('docs/logo.png'), false);
  assertEquals(['deep/nested/module.py', 'requirements.txt', 'app.py'].sort(byPriority)[0], 'requirements.txt');
});

Deno.test('분석 항목 정리: 모르는 kind 는 note, 경로 없는 항목도 남긴다', () => {
  assertEquals(cleanFindings([{ kind: 'IMPLEMENTED', title: 't', paths: ['a', ''] }, { kind: '?', title: 'x' }, 'bad']), [
    { kind: 'implemented', title: 't', detail: '', paths: ['a'] },
    { kind: 'note', title: 'x', detail: '', paths: [] },
  ]);
});
