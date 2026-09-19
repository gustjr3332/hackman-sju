// LLM 4사 호출부. backend/contests/llm/base.py 를 옮겼다. SDK 없이 HTTP 로 부른다.
// 제공사별로 다른 것은 요청·응답 모양뿐이고, 프롬프트와 파싱은 호출하는 쪽이 공용으로 갖는다.

export const PROVIDERS: Record<string, { label: string; key: string; defaultModel: string }> = {
  anthropic: { label: 'Anthropic', key: 'ANTHROPIC_API_KEY', defaultModel: 'claude-opus-5' },
  openai: { label: 'OpenAI', key: 'OPENAI_API_KEY', defaultModel: 'gpt-5.6-luna' },
  // 무료 등급 키는 pro 계열 quota 가 0 이라 flash 를 기본으로 둔다.
  google: { label: 'Google', key: 'GOOGLE_API_KEY', defaultModel: 'gemini-3.5-flash' },
  // 무료 티어 오픈소스 모델(Llama 등). OpenAI 호환 chat-completions 형식.
  groq: { label: 'Groq', key: 'GROQ_API_KEY', defaultModel: 'llama-3.3-70b-versatile' },
};

const keyOf = (p: string) => Deno.env.get(PROVIDERS[p]?.key ?? '') ?? '';

/** 키가 설정된 제공사만. 화면의 모델 선택기가 그대로 쓴다. */
export function availableModels() {
  return Object.entries(PROVIDERS)
    .filter(([name]) => keyOf(name))
    .map(([provider, m]) => ({ provider, label: m.label, default_model: m.defaultModel }));
}

export interface LlmResult {
  text: string;
  inputTokens: number;
  outputTokens: number;
  model: string;
}

export async function complete(
  prompt: string,
  opts: { provider?: string | null; model?: string | null; maxTokens?: number; timeoutMs?: number } = {},
): Promise<LlmResult> {
  const provider = opts.provider || availableModels()[0]?.provider;
  if (!provider) throw new Error('설정된 LLM API 키가 없습니다.');
  if (!PROVIDERS[provider]) throw new Error(`알 수 없는 제공사: ${provider}`);
  const key = keyOf(provider);
  if (!key) throw new Error(`${PROVIDERS[provider].label} API 키가 설정되지 않았습니다.`);
  const model = opts.model || PROVIDERS[provider].defaultModel;
  const maxTokens = opts.maxTokens ?? 2048;
  // 요청 안에서 부르는 호출은 30초. 제공사가 응답하지 않으면 그대로 기다리게 두지 않는다.
  const signal = AbortSignal.timeout(opts.timeoutMs ?? 30_000);

  const post = async (url: string, headers: Record<string, string>, body: unknown) => {
    const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', ...headers }, body: JSON.stringify(body), signal });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data?.error?.message ?? `${PROVIDERS[provider].label} 요청 실패 (${res.status})`);
    return data;
  };

  let text = '';
  let inputTokens = 0;
  let outputTokens = 0;
  if (provider === 'anthropic') {
    const d = await post('https://api.anthropic.com/v1/messages', { 'x-api-key': key, 'anthropic-version': '2023-06-01' },
      { model, max_tokens: maxTokens, messages: [{ role: 'user', content: prompt }] });
    text = (d.content ?? []).filter((b: { type: string }) => b.type === 'text').map((b: { text: string }) => b.text).join('');
    inputTokens = d.usage?.input_tokens ?? 0;
    outputTokens = d.usage?.output_tokens ?? 0;
  } else if (provider === 'openai') {
    const d = await post('https://api.openai.com/v1/responses', { Authorization: `Bearer ${key}` },
      { model, input: prompt, max_output_tokens: maxTokens });
    text = (d.output ?? []).flatMap((o: { content?: { type: string; text?: string }[] }) => o.content ?? [])
      .filter((c: { type: string }) => c.type === 'output_text').map((c: { text?: string }) => c.text ?? '').join('');
    inputTokens = d.usage?.input_tokens ?? 0;
    outputTokens = d.usage?.output_tokens ?? 0;
  } else if (provider === 'groq') {
    const d = await post('https://api.groq.com/openai/v1/chat/completions', { Authorization: `Bearer ${key}` },
      { model, max_tokens: maxTokens, messages: [{ role: 'user', content: prompt }] });
    text = d.choices?.[0]?.message?.content ?? '';
    inputTokens = d.usage?.prompt_tokens ?? 0;
    outputTokens = d.usage?.completion_tokens ?? 0;
  } else {
    const d = await post(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`,
      { 'x-goog-api-key': key },
      { contents: [{ parts: [{ text: prompt }] }], generationConfig: { maxOutputTokens: maxTokens } });
    const cand = d.candidates?.[0];
    text = (cand?.content?.parts ?? []).map((p: { text?: string }) => p.text ?? '').join('');
    inputTokens = d.usageMetadata?.promptTokenCount ?? 0;
    outputTokens = d.usageMetadata?.candidatesTokenCount ?? 0;
    // 출력 상한이 모자라면 본문 없이 끝난다. 엉뚱한 "JSON 없음" 대신 원인을 알린다.
    if (!text.trim()) throw new Error(`빈 응답 (finish_reason: ${cand?.finishReason ?? '알 수 없음'}). 출력 상한이 모자라면 늘려야 한다.`);
  }
  return { text, inputTokens, outputTokens, model };
}
