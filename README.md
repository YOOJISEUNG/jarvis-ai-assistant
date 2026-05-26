# J.A.R.V.I.S — Personal AI Assistant

> Anthropic Claude API + 브라우저 자동화 + RAG 패턴 기반의 음성/텍스트 AI 비서.  
> **본질적으로 "자연어 명령 → 자동화 시스템 동작" 프레임워크.**

<!-- 데모 GIF (Day 3에 추가) -->
![JARVIS Demo](docs/demo.gif)

---

## 🏥 자동화 시스템으로서의 의미

이 프로젝트는 개인 비서 형태로 만들었지만, 핵심 아키텍처는 **반복적 업무 자동화에 직접 적용 가능합니다**:

| 자비스 현재 기능 | 자동화 응용 예시 |

| 자연어 → 액션 라우팅 (Claude Tool Use) | 사용자 음성/텍스트 → 시스템 동작 자동화 |
| 브라우저 자동화 (Playwright) | 웹 기반 시스템 자동 입력/조회 |
| RAG 장기 기억 (ChromaDB) | 과거 기록 의미 검색, 컨텍스트 활용 |
| 다층 폴백 시스템 | 자동화 실패 시 안전한 수동 전환 |
| 실시간 모니터링 UI | 상태 시각화, 진행 상황 확인 |

> Claude API의 Tool Use 패턴이 안정적인 자동화 시스템의 핵심임을 검증한 프로토타입입니다.

---

## 주요 기능

### LLM 통합
- **Anthropic Claude Sonnet 4.6** 직접 통합
- **Tool Use 패턴**으로 구조화된 액션 결정 (9가지 액션 enum)
- 단기 컨텍스트 (20턴) + 장기 메모리 (RAG)

### 브라우저 자동화
- **Playwright** 영구 세션 (쿠키/로그인 유지)
- 유튜브 자동 검색 + 영상 클릭 + 재생 보장
- 다층 폴백: Playwright → 재시작 → 외부 브라우저

### RAG 장기 기억
- **ChromaDB** + sentence-transformers
- 의미 검색 ("노래 틀어줘" ↔ "음악 재생해줘")
- 거리 임계값 기반 컨텍스트 필터링

### 실시간 HUD UI
- HTML5 Canvas + SVG로 영화에 나오는 자비스 톤 직접 구현
- Socket.IO 양방향 실시간 통신
- 200개 파티클 시스템, 회전 링, 데이터 노이즈

### 🔊 음성 출력
- **edge-tts** (Microsoft Edge TTS API)
- 한국어 자연스러운 발음
- asyncio 격리로 다른 모듈과 충돌 회피


## 기술 스택

| 영역 | 사용 기술 |

| **LLM** | Anthropic Claude Sonnet 4.6 (Tool Use API) |
| **백엔드** | Python 3.12, Flask, Socket.IO |
| **음성** | edge-tts, pygame |
| **브라우저 자동화** | Playwright (Chromium) |
| **벡터 DB** | ChromaDB + sentence-transformers (all-MiniLM-L6-v2) |
| **프론트엔드** | HTML5, CSS3, Canvas, SVG, Socket.IO 클라이언트 |
| **인프라** | WSL2 (Ubuntu), Python venv |


## 시스템 아키텍처

┌──────────────────────────────────────────────────────────────┐
│  사용자 (텍스트 입력 / 향후: 음성)                              │
└───────────────────────┬──────────────────────────────────────┘
│
▼
┌──────────────────────────────────────────────────────────────┐
│  UI Server (Flask + Socket.IO) — Port 3000                   │
│  - 사용자 입력 수신                                            │
│  - 시스템 상태 브로드캐스트 (CPU/MEM/날씨/위치)                │
└───────────────────────┬──────────────────────────────────────┘
│ (SocketIO)
▼
┌──────────────────────────────────────────────────────────────┐
│  JARVIS Core (main loop)                                     │
│  - 입력 큐 관리                                                │
│  - 상태 전이 (standby → thinking → speaking)                 │
└───────────────────────┬──────────────────────────────────────┘
│
┌────────────────┼────────────────┬────────────────┐
▼                ▼                ▼                ▼
┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌────────┐
│ LLM Engine  │◀▶│ ChromaMemory │  │   Action    │  │  TTS   │
│  (Claude)   │  │  (RAG/벡터)  │  │  Executor   │  │ Engine │
└─────────────┘  └──────────────┘  └──────┬──────┘  └────────┘
│
┌──────────────────┼──────────────────┐
▼                  ▼                  ▼
┌────────────┐    ┌──────────────┐    ┌──────────┐
│  Browser   │    │   System     │    │   Web    │
│  Manager   │    │   Control    │    │ Search   │
│(Playwright)│    │(볼륨/시스템)  │    │ (DDGS)   │
└────────────┘    └──────────────┘    └──────────┘

## 4가지 핵심 구현 포인트

상세 설계는 [ARCHITECTURE.md] 에 나와있습니다.

### 1. Claude Tool Use로 100% 안정적 액션 라우팅

기존 LLM 응답 파싱의 문제(JSON 깨짐, 형식 불일치)를 **Tool Use 강제 호출**로 해결.

```python
tools = [{
    "name": "jarvis_response",
    "input_schema": {
        "properties": {
            "text": {"type": "string"},
            "action": {"type": "string", "enum": [
                "youtube_play", "youtube", "search", "memo",
                "volume_up", "volume_down", "open_browser",
                "exit", "none"
            ]},
            "query": {"type": "string"},
        },
        "required": ["text", "action", "query"],
    },
}]

response = client.messages.create(
    tools=tools,
    tool_choice={"type": "tool", "name": "jarvis_response"},
    ...
)
```

→ JSON 파싱 오류 0%. 안정적인 자동화 시스템의 기반.

### 2. RAG 패턴 직접 구현

ChromaDB + sentence-transformers로 과거 대화를 임베딩 저장 → 의미 검색 → 시스템 프롬프트에 주입.

```python
# 검색 (Retrieval)
memories = self.memory.search(user_text, top_k=3)

# 증강 (Augmentation)
memory_context = self.memory.format_for_prompt(memories)
system_prompt = memory_context + SYSTEM_INSTRUCTION_BASE

# 생성 (Generation)
response = self.client.messages.create(
    system=system_prompt,
    messages=[...]
)
```

→ "노래 틀어줘" 입력 시 과거에 명령했던 "에센셜 뮤직 틀어줘" 자동 매칭.

### 3. asyncio 격리 패턴 (라이브러리 통합 안정성)

Playwright(sync API)와 edge-tts(async)가 같은 메인 스레드에서 충돌하는 문제 해결.

```python
def speak(self, text):
    def _tts_worker():
        # 별도 스레드에서 새 이벤트 루프
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_gen(tmp_path))
        finally:
            loop.close()
    
    # 메인 스레드(Playwright)와 완전 격리
    threading.Thread(target=_tts_worker, daemon=True).start()
```

→ 시스템 통합 시 흔한 함정. 다른 sync+async 통합에도 같은 패턴 적용 가능.

### 4. 다층 폴백 시스템 (실패 안전)

자동화 시스템에서 가장 중요한 건 "실패 시 어떻게 동작하느냐". 자비스는 3단계 폴백.

```python
def _do_youtube_play(self, query):
    # 1차: Playwright 자동 재생
    if self.ba.youtube_play(query):
        return
    
    # 2차: 브라우저 재시작 후 재시도
    self.ba.bm.shutdown()
    self.ba.bm.start()
    if self.ba.youtube_play(query):
        return
    
    # 3차: 외부 브라우저로 폴백 (사용자 수동 클릭)
    SystemControl.open_url(search_url)
```

→ 어떤 단계에서 실패해도 사용자는 결과를 받음.

---

## 개발 일지 (Meta-cognition)

이 프로젝트를 진행하면서 마주친 문제와 해결 과정:

- **asyncio 이벤트 루프 충돌 디버깅** — 3시간의 시행착오, 별도 스레드 패턴으로 해결
- **WSL 메모리 한계 인지** — 코드만으로 해결 불가능한 영역 학습
- **모델 선택 트레이드오프** — multilingual (정확) vs all-MiniLM (가벼움) 결정 과정
- **시스템 통합 설계** — 6개 모듈 SRP 분리, 의존성 주입

→ 자세한 학습 일지: [Notion 포트폴리오](https://...)

## 설치 및 실행

### 1. 사전 요구사항
- Python 3.10+
- Anthropic API Key
- WSL2 (Windows) 또는 Linux/Mac

### 2. 클론 및 설치
```bash
git clone https://github.com/[너ID]/jarvis-ai-assistant.git
cd jarvis-ai-assistant

# 가상환경
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 의존성
pip install -r requirements.txt
playwright install chromium
```

### 3. 환경 변수 설정
```bash
cp .env.example .env
# .env 파일 열어서 ANTHROPIC_API_KEY 입력
```

### 4. 실행
```bash
# 터미널 1: UI 서버
python server.py

# 터미널 2: 자비스 코어
python jarvis.py
```

브라우저: `http://127.0.0.1:3000`

---

## 📂 프로젝트 구조

jarvis-ai-assistant/
├── jarvis.py              # 메인 코어 (LLM, Action, Jarvis 클래스)
├── server.py              # Flask + SocketIO UI 서버
├── memory_manager.py      # ChromaDB RAG 메모리
├── browser_manager.py     # Playwright 브라우저 세션
├── browser_actions.py     # 유튜브 자동 재생 등 액션
├── ui/
│   └── index.html        # 영화 자비스 HUD UI (단일 파일)
├── docs/
│   ├── demo.gif
│   └── architecture.png
├── requirements.txt
├── .env.example
├── .gitignore
├── ARCHITECTURE.md
└── README.md

## 이 프로젝트로 배운 것

### 기술적
- LLM Tool Use 패턴 (구조화 응답)
- RAG 아키텍처 (임베딩 → 벡터 검색 → 컨텍스트 주입)
- Playwright 브라우저 자동화 + 안정성 패턴
- asyncio 이벤트 루프 관리 (다른 async와 공존)
- 실시간 WebSocket 통신 (Socket.IO)
- 다국어 임베딩 모델 비교 분석

### 시스템 사고
- SRP 기반 모듈 설계 (각 클래스 단일 책임)
- 다층 폴백으로 신뢰성 확보
- 환경별 분기 처리 (WSL/Linux/Windows/Mac)
- 점진적 통합 (Phase 1~6 단계적 검증)

### 메타인지
- 시스템 사양과 코드의 경계 인지
- 트레이드오프 명확히 문서화
- 실패 회고를 학습 자산으로 발전

## 📈 향후 개선 계획

- [ ] Phase 6: Whisper STT 통합 (음성 입력)
- [ ] Phase 7: 일정/캘린더 모듈 (Google Calendar API)
- [ ] Phase 8: 다중 사용자 지원
- [ ] Phase 9: 도메인별 특화 (의료/교육 등)

## 👤 작성자

**유지승**  
- 현재 AI관련 취업 준비중이자 Google Associate Cloud Engineer 및 다양한 cloud 자격증 준비중
- Email [sublime7453@gmail.com]
- Link [Notion 포트폴리오](링크)
- Phone [010-9135-8156]

## 라이선스

MIT License (학습/포트폴리오 목적)