// 아이디 또는 이메일로 로그인한다. 로그인이 필요 없는 유일한 함수라 serveAuthed 를 안 쓴다.
//
// 아이디→이메일 조회를 클라이언트에 공개 RPC로 주면 그 자체가 "아이디만 대면 이메일을 알려주는"
// 구멍이 된다(계정 존재 여부까지 새 나감). 그래서 조회는 이 함수 안, service_role 로만 하고
// 클라이언트에는 절대 이메일을 돌려주지 않는다 — 로그인 성공/실패 결과만 돌려준다.
// 아이디가 없어도 GoTrue 에 그대로 넘겨 "로그인 정보가 맞지 않습니다"로 실패하게 해서,
// 존재하는 아이디와 존재하지 않는 아이디의 응답을 구분할 수 없게 한다.
import { admin, cors, fail, json } from '../_shared/http.ts';

Deno.serve(async (req) => {
  if (req.method === 'OPTIONS') return new Response('ok', { headers: cors });
  try {
    const { identifier, password } = await req.json();
    if (!identifier || !password) return fail(400, '아이디(또는 이메일)와 비밀번호를 입력하세요.');

    let email: string = identifier.trim();
    if (!email.includes('@')) {
      const { data: profile } = await admin.from('profiles').select('id').eq('username', email).single();
      const found = profile ? (await admin.auth.admin.getUserById(profile.id)).data.user?.email : null;
      email = found ?? `${crypto.randomUUID()}@invalid.local`;
    }

    const res = await fetch(`${Deno.env.get('SUPABASE_URL')}/auth/v1/token?grant_type=password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', apikey: Deno.env.get('SUPABASE_ANON_KEY')! },
      body: JSON.stringify({ email, password }),
    });
    const body = await res.json();
    if (!res.ok) return fail(400, '아이디(이메일) 또는 비밀번호가 맞지 않습니다.');
    return json(body);
  } catch (e) {
    console.error(e);
    return fail(500, (e as Error).message || '서버 오류');
  }
});
