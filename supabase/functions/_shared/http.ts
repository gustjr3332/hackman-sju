import { createClient, type SupabaseClient } from 'npm:@supabase/supabase-js@2';

export const cors = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
};

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { ...cors, 'Content-Type': 'application/json' } });
}

/** 화면이 그대로 띄우는 오류. kind 는 GitHub 안내 문구처럼 화면이 분기할 때만 쓴다. */
export function fail(status: number, detail: string, kind?: string): Response {
  return json({ detail, ...(kind ? { kind } : {}) }, status);
}

/** RLS 를 넘는 서버 전용 클라이언트. 요청자 판별은 반드시 caller() 로 따로 한다. */
export const admin: SupabaseClient = createClient(
  Deno.env.get('SUPABASE_URL')!,
  Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!,
  { auth: { persistSession: false } },
);

export interface Caller {
  id: string;
  username: string;
  isStaff: boolean;
}

/** Authorization 헤더의 사용자. anon 키만 온 경우(비로그인)는 null. */
export async function caller(req: Request): Promise<Caller | null> {
  const token = (req.headers.get('Authorization') ?? '').replace(/^Bearer\s+/i, '');
  if (!token) return null;
  const { data } = await admin.auth.getUser(token);
  if (!data.user) return null;
  const { data: p } = await admin.from('profiles').select('username, is_staff').eq('id', data.user.id).single();
  return p ? { id: data.user.id, username: p.username, isStaff: p.is_staff } : null;
}

/** OPTIONS 는 CORS 로 끝내고, 나머지는 로그인한 사용자만 handler 로 넘긴다. */
export function serveAuthed(handler: (req: Request, me: Caller) => Promise<Response>) {
  Deno.serve(async (req) => {
    if (req.method === 'OPTIONS') return new Response('ok', { headers: cors });
    try {
      const me = await caller(req);
      if (!me) return fail(401, '로그인이 필요합니다.');
      return await handler(req, me);
    } catch (e) {
      console.error(e);
      return fail(500, (e as Error).message || '서버 오류');
    }
  });
}
