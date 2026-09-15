// 키가 설정된 LLM 제공사 목록. 하나도 없으면 빈 배열이고, 화면은 자동 정리 버튼을 숨긴다.
import { json, serveAuthed } from '../_shared/http.ts';
import { availableModels } from '../_shared/llm.ts';

serveAuthed(async () => json({ providers: availableModels() }));
