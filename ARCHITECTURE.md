# JARVIS Architecture

## 설계 원칙

1. **SRP (Single Responsibility Principle)** — 각 클래스는 하나의 책임만 있게 제작
2. **의존성 주입** — 테스트/교체 가능한 구조
3. **실패 안전** — 어떤 모듈이 죽어도 다른 부분은 작동
4. **점진적 통합** — Phase 단위 검증 후 결합

## 모듈별 책임

### UIClient (`jarvis.py`)
- 책임: WebSocket 통신만
- 의존: socketio
- 인터페이스: connect(), get_text_input(), status(), log_message()

### LLMEngine (`jarvis.py`)
- 책임: Claude API 호출만
- 의존: anthropic, ChromaMemory (선택)
- 인터페이스: ask(user_text) → {text, action, query}

### ChromaMemory (`memory_manager.py`)
- 책임: 장기 기억 저장/검색만
- 의존: chromadb, sentence-transformers
- 인터페이스: add_turn(), search(), format_for_prompt()

### ActionExecutor (`jarvis.py`)
- 책임: 액션 디스패치 + 폴백 관리
- 의존: TTS, UI, BrowserActions (선택)
- 9가지 액션: youtube_play, youtube, search, memo, volume_up, volume_down, open_browser, exit, none

### BrowserManager (`browser_manager.py`)
- 책임: Playwright 브라우저 라이프사이클
- 싱글톤 패턴 (자비스 전체에 하나만)
- 영구 세션 (~/jarvis/data/browser_profile)

### BrowserActions (`browser_actions.py`)
- 책임: 구체적 웹 조작 (유튜브 재생, URL 열기)
- 의존: BrowserManager

### TTSEngine (`jarvis.py`)
- 책임: 음성 합성/재생
- 의존: edge-tts, pygame
- 특수: asyncio 격리 패턴 적용

### SystemControl (`jarvis.py`)
- 책임: OS 시스템 명령 (볼륨, URL 열기)
- WSL/Linux/Windows/Mac 통합 분기

## 데이터 흐름 (한 턴의 라이프사이클)
[1] 사용자 입력
"에센셜 뮤직 틀어줘"
│
▼
[2] UIClient.text_queue.put()
│
▼
[3] Jarvis main loop
self.ui.get_text_input() → text 수신
self.ui.status("thinking")
│
▼
[4] LLMEngine.ask(text)
├─ ChromaMemory.search(text) → 관련 기억 검색
├─ 시스템 프롬프트에 메모리 컨텍스트 주입 (RAG)
├─ Claude API 호출 (Tool Use 강제)
└─ {text, action, query} 반환
│
▼
[5] ChromaMemory.add_turn() → 이번 대화 저장
│
▼
[6] ActionExecutor.execute(action, query, text)
├─ TTSEngine.speak(text) → 음성 출력
├─ action 분기:
│   ├─ youtube_play → BrowserActions.youtube_play(query)
│   ├─ memo → 파일 저장
│   ├─ volume_up → SystemControl
│   └─ ...
└─ 폴백 체인 적용
│
▼
[7] UIClient.status("standby") → 다음 입력 대기

## 의도한 설계 결정 (Trade-offs)

### Q. 왜 sync API + 스레드 격리?
**A.** 자비스 전체를 async로 재설계하면 큰 변경이 발생함.
TTS 모듈만 스레드 격리하면 충돌 회피 가능 → 변경 범위 최소화.

### Q. 왜 Pinecone/Weaviate 대신에 ChromaDB를 사용했는가.
**A.** 
- 로컬 SQLite 기반 → 외부 서비스 의존성 X
- 무료 (Pinecone은 일정 이상 유료)
- 자비스 규모 (수천 turn)에는 충분

### Q. 왜 multilingual 대신 all-MiniLM-L6-v2인가요?
**A.** WSL RAM 한계로 인한 트레이드오프.
- multilingual (470MB): 한국어 정확도 ↑
- all-MiniLM (80MB): 한국어 OK + 메모리 6배 절약
- 자비스 사용엔 후자가 충분, 시스템 안정성 우선.

### Q. Open AI 대신 Anthropic Claude를 사용한 이유.
**A.**
- Tool Use 안정성 (`tool_choice` 강제 시 100%)
- 한국어 자연스러움
- 비용 효율 (Sonnet 4.6 기준)

## 확장 가능성

### 새 액션 추가 절차
1. `SYSTEM_INSTRUCTION_BASE`에 액션 설명 추가
2. `LLMEngine.ask()` tools enum에 추가
3. `ActionExecutor.execute()` 분기에 처리 추가

→ 약 10줄 변경으로 새 액션 통합 가능.

### 새 LLM으로 교체
`LLMEngine`만 교체. 다른 모듈에는 영향 없음.

### 도메인 특화 (예: 의료 자동화)
- `SYSTEM_INSTRUCTION_BASE`를 도메인 프롬프트로 교체
- 액션을 도메인 액션으로 확장 (예: `appointment_create`)
- 메모리 검색을 도메인 컨텍스트로 활용
