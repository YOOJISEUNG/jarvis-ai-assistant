JARVIS - Personal AI Assistant Core
Anthropic Claude API + Playwright 브라우저 자동화 + ChromaDB 장기 기억

마지막 수정 이력:
- Phase 4: Playwright 브라우저 자동화 통합
- TTS asyncio 격리 (별도 스레드)
- 브라우저 자동 복구 로직
- Phase 5: ChromaDB 장기 기억 통합 (RAG 패턴)

from __future__ import annotations

import os
import sys
import time
import json
import logging
import tempfile
import threading
import subprocess
import platform
import shutil
import queue
from pathlib import Path
from collections import deque
from typing import Any, Optional
from urllib.parse import quote

import pygame
import asyncio
import edge_tts
import socketio as sio_module
import anthropic
from dotenv import load_dotenv
from duckduckgo_search import DDGS

# Phase 4: 브라우저 자동화
from browser_manager import get_browser
from browser_actions import BrowserActions

# Phase 5: 장기 기억
from memory_manager import ChromaMemory

환경 변수

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
TTS_VOICE = os.getenv("TTS_VOICE", "ko-KR-HyunsuNeural")
USER_NAME = os.getenv("USER_NAME", "지승")
SERVER_PORT = int(os.getenv("SERVER_PORT", "3000"))
SERVER_URL = f"http://127.0.0.1:{SERVER_PORT}"

BROWSER_HEADLESS = os.getenv("BROWSER_HEADLESS", "false").lower() == "true"

# Phase 5 옵션
MEMORY_ENABLED = os.getenv("MEMORY_ENABLED", "true").lower() == "true"
MEMORY_TOP_K = int(os.getenv("MEMORY_TOP_K", "3"))

HISTORY_MAX_TURNS = 20
LLM_TIMEOUT = 30  # 초
TTS_TIMEOUT = 15  # 초

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
MEMO_FILE = DATA_DIR / "memos.txt"
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ═══════════════════════════════════════════════════════════
# 로거
# ═══════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOGS_DIR / "jarvis.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("jarvis")


# ═══════════════════════════════════════════════════════════
# UI 클라이언트
# ═══════════════════════════════════════════════════════════
class UIClient:
    def __init__(self, url: str = SERVER_URL):
        self.sio = sio_module.Client(reconnection=True, reconnection_delay=2)
        self.url = url
        self.connected = False
        self.text_queue: queue.Queue[str] = queue.Queue()
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        @self.sio.event
        def connect():
            self.connected = True
            log.info(f"UI 서버 연결됨 ({self.url})")

        @self.sio.event
        def disconnect():
            self.connected = False
            log.warning("UI 서버 연결 끊김")

        @self.sio.on("text_command")
        def on_text(data):
            text = data.get("text", "").strip()
            if text:
                log.info(f"UI에서 텍스트 수신: {text}")
                self.text_queue.put(text)

    def connect(self) -> None:
        try:
            self.sio.connect(self.url, wait=False, wait_timeout=5)
        except Exception as e:
            log.warning(f"UI 서버 연결 실패: {e}")

    def get_text_input(self, timeout: float = None) -> Optional[str]:
        try:
            return self.text_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def status(self, status: str) -> None:
        if self.connected:
            try:
                self.sio.emit("jarvis_status", {"status": status})
            except Exception as e:
                log.warning(f"status 전송 실패: {e}")

    def log_message(self, speaker: str, text: str) -> None:
        if self.connected:
            try:
                self.sio.emit("jarvis_log", {"speaker": speaker, "text": text})
            except Exception as e:
                log.warning(f"log 전송 실패: {e}")

    def disconnect(self) -> None:
        if self.connected:
            try:
                self.sio.disconnect()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════
# 시스템 제어 (WSL/Linux/Windows/Mac)
# ═══════════════════════════════════════════════════════════
class SystemControl:
    _is_wsl_cache: Optional[bool] = None

    @classmethod
    def is_wsl(cls) -> bool:
        if cls._is_wsl_cache is not None:
            return cls._is_wsl_cache
        try:
            with open("/proc/version", "r") as f:
                cls._is_wsl_cache = "microsoft" in f.read().lower()
        except FileNotFoundError:
            cls._is_wsl_cache = False
        return cls._is_wsl_cache

    @staticmethod
    def volume_up(step: int = 10) -> bool:
        try:
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{step}%"],
                check=True, capture_output=True
            )
            log.info(f"[VOLUME] +{step}%")
            return True
        except Exception as e:
            log.error(f"[VOLUME] 업 실패: {e}")
            return False

    @staticmethod
    def volume_down(step: int = 10) -> bool:
        try:
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{step}%"],
                check=True, capture_output=True
            )
            log.info(f"[VOLUME] -{step}%")
            return True
        except Exception as e:
            log.error(f"[VOLUME] 다운 실패: {e}")
            return False

    @classmethod
    def open_url(cls, url: str) -> bool:
        """외부 브라우저 폴백용 URL 오픈."""
        if not url:
            return False

        try:
            if cls.is_wsl():
                if shutil.which("wslview"):
                    subprocess.Popen(
                        ["wslview", url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    log.info(f"[BROWSER-EXT] wslview로 열기: {url}")
                else:
                    subprocess.Popen(
                        ["cmd.exe", "/c", "start", "", url],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    log.info(f"[BROWSER-EXT] cmd.exe로 열기: {url}")
                return True

            system = platform.system()
            if system == "Linux":
                subprocess.Popen(["xdg-open", url],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            if system == "Windows":
                os.startfile(url)
                return True
            if system == "Darwin":
                subprocess.Popen(["open", url])
                return True
            return False

        except Exception as e:
            log.error(f"[BROWSER-EXT] 실패: {e}")
            return False


# ═══════════════════════════════════════════════════════════
# TTS — 별도 스레드에서 asyncio (Playwright와 격리)
# ═══════════════════════════════════════════════════════════
class TTSEngine:
    def __init__(self, voice: str = TTS_VOICE):
        self.voice = voice
        self.available = False
        self._lock = threading.Lock()

        configs = [
            {"frequency": 22050, "size": -16, "channels": 2, "buffer": 4096},
            {"frequency": 44100, "size": -16, "channels": 2, "buffer": 4096},
            {"frequency": 24000, "size": -16, "channels": 1, "buffer": 2048},
            {"frequency": 16000, "size": -16, "channels": 1, "buffer": 1024},
        ]

        for cfg in configs:
            try:
                if pygame.mixer.get_init():
                    pygame.mixer.quit()
                pygame.mixer.init(**cfg)
                self.available = True
                log.info(f"TTS 오디오 초기화 성공: {cfg}")
                break
            except Exception as e:
                log.warning(f"TTS 설정 실패 {cfg}: {e}")
                continue

        if not self.available:
            log.error("모든 TTS 오디오 설정 실패 — 텍스트만 출력됩니다")

    def speak(self, text: str) -> None:
        """
        TTS 음성 출력. 별도 스레드에서 asyncio 실행 → Playwright와 격리.
        """
        if not text or not text.strip():
            return
        if not self.available:
            log.info(f"[TTS 비활성] {text}")
            return

        async def _gen(out_path: str) -> None:
            communicate = edge_tts.Communicate(text, self.voice)
            await communicate.save(out_path)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
            tmp_path = f.name

        def _tts_worker():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_gen(tmp_path))
            finally:
                loop.close()

        try:
            tts_thread = threading.Thread(target=_tts_worker, daemon=True)
            tts_thread.start()
            tts_thread.join(timeout=TTS_TIMEOUT)

            if tts_thread.is_alive():
                log.error("[TTS] 생성 타임아웃")
                return

            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                log.error("[TTS] 생성 실패 (파일 없음/빈 파일)")
                return

            with self._lock:
                pygame.mixer.music.load(tmp_path)
                pygame.mixer.music.play()
            elapsed = 0
            while pygame.mixer.music.get_busy() and elapsed < 30:
                time.sleep(0.05)
                elapsed += 0.05

        except Exception as e:
            log.error(f"[TTS] 재생 실패: {e}", exc_info=True)
        finally:
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass
            try:
                os.remove(tmp_path)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════
# Claude LLM + 장기 기억 통합 (RAG)
# ═══════════════════════════════════════════════════════════
SYSTEM_INSTRUCTION_BASE = f"""너는 JARVIS, {USER_NAME}님의 개인 AI 비서야.

[정체성]
- 영화 아이언맨의 자비스처럼 차분하고 똑똑하며 약간의 위트가 있다.
- {USER_NAME}님을 항상 "{USER_NAME}님"으로 부른다.
- 한국어로만 응답한다.

[응답 규칙]
- 답변은 1~3문장으로 간결하게.
- 사용자가 행동을 요청하면 적절한 action을 선택하고, 그렇지 않으면 action을 "none"으로.

[action 종류와 선택 기준]
- youtube_play: 영상을 직접 재생할 때. "틀어줘", "재생해줘", "들려줘", "보여줘" 의도.
                예: "에센셜 뮤직 틀어줘" → youtube_play, query="에센셜 뮤직"
- youtube: 검색 페이지만 열어서 사용자가 직접 고를 때. "검색해줘", "찾아줘" 의도.
           예: "유튜브에서 BTS 영상 검색해줘" → youtube, query="BTS"
- search: 웹 검색 후 자비스가 결과를 음성으로 답할 때.
          예: "파이썬 데코레이터 검색해줘" → search, query="파이썬 데코레이터"
- memo: 메모 저장. 예: "메모해줘 회의 4시" → memo, query="회의 4시"
- volume_up / volume_down: 시스템 볼륨 조절.
- open_browser: 일반 URL 또는 사이트 열기. 예: "네이버 열어줘" → open_browser, query="네이버"
- exit: 자비스 종료.
- none: 그냥 대화/응답.

[중요]
- "틀어줘", "재생해줘", "들려줘"는 youtube_play (직접 재생)
- "검색해줘", "찾아줘"는 youtube (검색 페이지만)
- 둘 다 query에는 검색어만 (URL 만들지 말 것)

[기억 활용]
- 메시지 앞에 [과거에 나눈 대화]가 있으면 참고해라.
- 사용자가 "아까", "방금", "전에" 같은 표현을 쓰면 과거 대화에서 찾아라.
- 사용자가 명시적으로 묻지 않으면 굳이 과거 대화를 언급하지 마라.
"""


class LLMEngine:
    """Anthropic Claude — tool use + ChromaDB 메모리 통합."""

    def __init__(self, memory: Optional[ChromaMemory] = None):
        print("[INIT-LLM] Claude 클라이언트 생성 시도...", flush=True)

        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY가 .env에 없습니다")
        if not ANTHROPIC_API_KEY.startswith("sk-ant-"):
            raise RuntimeError(f"키 형식 오류 (sk-ant-로 시작해야 함, 현재: {ANTHROPIC_API_KEY[:10]})")

        self.client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model_name = ANTHROPIC_MODEL
        self.history: deque[dict] = deque(maxlen=HISTORY_MAX_TURNS * 2)

        # Phase 5: 장기 기억
        self.memory = memory

        log.info(f"Claude 초기화 완료 (모델: {self.model_name})")
        log.info(f"장기 기억: {'활성 (기억: ' + str(memory.count()) + '개)' if memory and memory.ready else '비활성'}")
        print(f"[INIT-LLM] OK (모델: {self.model_name})", flush=True)

    def ask(self, user_text: str) -> dict[str, str]:
        log.info(f"Claude 호출 시작: {user_text[:30]}...")

        # 1단계: 장기 기억 검색 (RAG retrieval)
        memory_context = ""
        if self.memory and self.memory.ready:
            try:
                memories = self.memory.search(user_text, top_k=MEMORY_TOP_K)
                memory_context = self.memory.format_for_prompt(memories)
                if memory_context:
                    log.info(f"[MEMORY] 컨텍스트 주입: {len(memories)}건")
            except Exception as e:
                log.warning(f"[MEMORY] 검색 실패 (무시하고 계속): {e}")

        # 2단계: 시스템 프롬프트에 컨텍스트 추가 (RAG augmentation)
        system_prompt = memory_context + SYSTEM_INSTRUCTION_BASE

        self.history.append({"role": "user", "content": user_text})

        tools = [{
            "name": "jarvis_response",
            "description": "자비스의 응답과 액션을 결정한다.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "사용자에게 음성으로 전달할 응답 (한국어, 1~3문장)",
                    },
                    "action": {
                        "type": "string",
                        "enum": [
                            "youtube_play",
                            "youtube",
                            "search",
                            "memo",
                            "volume_up",
                            "volume_down",
                            "open_browser",
                            "exit",
                            "none",
                        ],
                    },
                    "query": {
                        "type": "string",
                        "description": "액션에 필요한 검색어/내용/URL. 없으면 빈 문자열.",
                    },
                },
                "required": ["text", "action", "query"],
            },
        }]

        result_container = {"data": None, "error": None}

        def _call():
            try:
                response = self.client.messages.create(
                    model=self.model_name,
                    max_tokens=512,
                    system=system_prompt,  # 메모리 주입된 프롬프트 사용
                    tools=tools,
                    tool_choice={"type": "tool", "name": "jarvis_response"},
                    messages=list(self.history),
                )
                for block in response.content:
                    if block.type == "tool_use" and block.name == "jarvis_response":
                        result_container["data"] = block.input
                        return
                result_container["error"] = ValueError("tool_use 블록 없음")
            except Exception as e:
                result_container["error"] = e

        t = threading.Thread(target=_call, daemon=True)
        t.start()
        t.join(timeout=LLM_TIMEOUT)

        if t.is_alive():
            log.error("Claude 호출 타임아웃")
            return {"text": "응답 시간이 너무 깁니다.", "action": "none", "query": ""}

        if result_container["error"]:
            log.error(f"Claude 에러: {result_container['error']}")
            return {"text": "오류가 발생했습니다.", "action": "none", "query": ""}

        data = result_container["data"]
        if not data:
            return {"text": "응답이 비어있습니다.", "action": "none", "query": ""}

        log.info(f"Claude 응답: {data}")

        # 단기 히스토리 추가 (현재 세션)
        self.history.append({
            "role": "assistant",
            "content": json.dumps(data, ensure_ascii=False),
        })

        # 3단계: 장기 기억에 저장 (다음 세션에서도 활용)
        if self.memory and self.memory.ready:
            try:
                self.memory.add_turn(
                    user_text=user_text,
                    jarvis_response=data.get("text", ""),
                    action=data.get("action", "none"),
                    query=data.get("query", ""),
                )
            except Exception as e:
                log.warning(f"[MEMORY] 저장 실패 (무시하고 계속): {e}")

        return {
            "text": data.get("text", "").strip(),
            "action": data.get("action", "none"),
            "query": data.get("query", "").strip(),
        }


# ═══════════════════════════════════════════════════════════
# 액션 실행기
# ═══════════════════════════════════════════════════════════
class ActionExecutor:
    def __init__(self, tts: TTSEngine, ui: UIClient,
                 browser_actions: Optional[BrowserActions] = None):
        self.tts = tts
        self.ui = ui
        self.ba = browser_actions

    def execute(self, action: str, query: str, response_text: str) -> bool:
        log.info(f"액션 실행: action={action}, query={query}")

        # 자비스 응답 먼저
        if response_text:
            self.ui.status("speaking")
            self.ui.log_message("jarvis", response_text)
            self.tts.speak(response_text)

        # 액션 디스패치
        if action == "youtube_play":
            self._do_youtube_play(query)
        elif action == "youtube":
            self._do_youtube_search(query)
        elif action == "search":
            self._do_search(query)
        elif action == "memo":
            self._do_memo(query)
        elif action == "volume_up":
            SystemControl.volume_up()
        elif action == "volume_down":
            SystemControl.volume_down()
        elif action == "open_browser":
            self._do_open_browser(query)
        elif action == "exit":
            return False

        return True

    def _do_youtube_play(self, query: str) -> None:
        """유튜브 자동 재생 (자동 복구 포함)."""
        if not query:
            log.warning("[YT_PLAY] 검색어 없음")
            return

        # 1차 시도
        if self.ba and self.ba.bm.is_ready:
            try:
                if self.ba.youtube_play(query):
                    return
            except Exception as e:
                log.error(f"[YT_PLAY] 예외 발생: {e}")

            log.warning("[YT_PLAY] Playwright 재생 실패 → 브라우저 복구 시도")

            # 2차: 브라우저 재시작
            try:
                self.ba.bm.shutdown()
                time.sleep(1)
                started = self.ba.bm.start(headless=False) or self.ba.bm.start(headless=True)
                if started:
                    log.info("[YT_PLAY] 브라우저 복구 성공 — 재시도")
                    if self.ba.youtube_play(query):
                        return
            except Exception as e:
                log.error(f"[YT_PLAY] 복구 실패: {e}")

        # 최종 폴백
        log.info("[YT_PLAY] 외부 브라우저로 폴백")
        url = f"https://www.youtube.com/results?search_query={quote(query)}"
        SystemControl.open_url(url)

    def _do_youtube_search(self, query: str) -> None:
        if query:
            url = f"https://www.youtube.com/results?search_query={quote(query)}"
        else:
            url = "https://www.youtube.com"

        if self.ba and self.ba.bm.is_ready:
            try:
                if self.ba.open_url(url):
                    return
            except Exception as e:
                log.error(f"[YT_SEARCH] 예외: {e}")

        SystemControl.open_url(url)

    def _do_open_browser(self, query: str) -> None:
        q = (query or "").strip()
        if q.startswith(("http://", "https://")):
            url = q
        elif q:
            url = f"https://www.google.com/search?q={quote(q)}"
        else:
            url = "https://www.google.com"

        if self.ba and self.ba.bm.is_ready:
            try:
                if self.ba.open_url(url):
                    return
            except Exception as e:
                log.error(f"[OPEN_BROWSER] 예외: {e}")

        SystemControl.open_url(url)

    def _do_search(self, query: str) -> None:
        if not query:
            return
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=3))
            if results:
                summary = results[0].get("body", "")[:250]
                if summary:
                    self.ui.status("speaking")
                    self.ui.log_message("jarvis", summary)
                    self.tts.speak(summary)
        except Exception as e:
            log.error(f"검색 실패: {e}")

    def _do_memo(self, content: str) -> None:
        if not content:
            return
        try:
            with open(MEMO_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] {content}\n")
            log.info(f"메모 저장: {content}")
        except Exception as e:
            log.error(f"메모 실패: {e}")


# ═══════════════════════════════════════════════════════════
# Jarvis 메인
# ═══════════════════════════════════════════════════════════
class Jarvis:
    def __init__(self):
        print("[INIT] Jarvis 시작", flush=True)
        log.info("Jarvis 인스턴스 생성")

        print("[INIT] UIClient 생성 중...", flush=True)
        self.ui = UIClient()
        print("[INIT] UIClient OK", flush=True)

        print("[INIT] TTSEngine 생성 중...", flush=True)
        self.tts = TTSEngine()
        print("[INIT] TTSEngine OK", flush=True)

        # Phase 5: 장기 기억 초기화 (LLMEngine보다 먼저!)
        self.memory: Optional[ChromaMemory] = None
        if MEMORY_ENABLED:
            print("[INIT] ChromaMemory 생성 중... (첫 실행 시 모델 다운로드 ~5분)", flush=True)
            try:
                self.memory = ChromaMemory()
                if self.memory.ready:
                    print(f"[INIT] ChromaMemory OK (기억: {self.memory.count()}개)", flush=True)
                else:
                    print("[INIT] ChromaMemory 초기화 실패 — 기억 없이 작동", flush=True)
                    self.memory = None
            except Exception as e:
                log.error(f"ChromaMemory 예외: {e}")
                print(f"[INIT] ChromaMemory 예외 — 기억 없이 작동: {e}", flush=True)
                self.memory = None
        else:
            print("[INIT] ChromaMemory 비활성화 (.env)", flush=True)

        print("[INIT] LLMEngine 생성 중...", flush=True)
        self.llm = LLMEngine(memory=self.memory)
        print("[INIT] LLMEngine OK", flush=True)

        # Phase 4: 브라우저 매니저
        self.browser = get_browser()
        self.browser_actions: Optional[BrowserActions] = None
        print("[INIT] BrowserManager 인스턴스 생성", flush=True)

        print("[INIT] ActionExecutor 생성 중...", flush=True)
        self.executor = ActionExecutor(self.tts, self.ui, browser_actions=None)
        print("[INIT] ActionExecutor OK", flush=True)

        self.running = True
        print("[INIT] Jarvis 완성", flush=True)

    def _start_browser(self) -> bool:
        log.info("[BROWSER] 시작 시도...")

        if not BROWSER_HEADLESS:
            if self.browser.start(headless=False):
                log.info("[BROWSER] ✅ headed 모드 시작 성공")
                self.browser_actions = BrowserActions()
                self.executor.ba = self.browser_actions
                return True
            log.warning("[BROWSER] headed 실패 → headless 재시도")

        if self.browser.start(headless=True):
            log.info("[BROWSER] ✅ headless 모드 시작 성공")
            self.browser_actions = BrowserActions()
            self.executor.ba = self.browser_actions
            return True

        log.error("[BROWSER] ❌ 시작 실패 — 외부 브라우저로 폴백 작동")
        return False

    def boot(self) -> None:
        log.info("─" * 50)
        log.info(f"JARVIS 부팅 (사용자: {USER_NAME})")
        log.info(f"환경: {'WSL' if SystemControl.is_wsl() else platform.system()}")
        log.info(f"TTS 오디오: {'사용 가능' if self.tts.available else '비활성'}")
        log.info(f"LLM 모델: {self.llm.model_name}")
        log.info(f"브라우저 모드: {'headless' if BROWSER_HEADLESS else 'headed (우선)'}")
        #
        if self.memory and self.memory.ready:
            log.info(f"장기 기억: 활성 ({self.memory.count()}개 저장됨)")
        else:
            log.info("장기 기억: 비활성")
        log.info("─" * 50)

        self.ui.connect()
        time.sleep(1.0)

        browser_ok = self._start_browser()

        # 인사말 — 첫 부팅 vs 재시작 구분
        memory_count = self.memory.count() if (self.memory and self.memory.ready) else 0

        greeting = f"자비스 시스템 온라인. 안녕하세요, {USER_NAME}님."

        # 첫 부팅
        if memory_count == 0:
            if browser_ok and self.memory and self.memory.ready:
                greeting += " 브라우저 자동화 모듈과 장기 기억 시스템이 활성화되었습니다."
            elif browser_ok:
                greeting += " 브라우저 자동화 모듈이 활성화되었습니다."

        # 재시작 (기억 있음)
        else:
            greeting += f" 다시 뵙게 되어 반갑습니다. 총 {memory_count}개의 기억을 보유하고 있습니다."

        self.ui.status("speaking")
        self.ui.log_message("jarvis", greeting)
        self.tts.speak(greeting)
        self.ui.status("standby")
        log.info("부팅 완료, 입력 대기 중")

    def shutdown(self) -> None:
        log.info("종료 처리 시작")
        farewell = f"자비스 종료합니다. 좋은 하루 되십시오, {USER_NAME}님."
        self.ui.status("speaking")
        self.ui.log_message("jarvis", farewell)
        self.tts.speak(farewell)
        self.ui.status("standby")
        time.sleep(0.5)

        try:
            self.browser.shutdown()
        except Exception as e:
            log.warning(f"브라우저 종료 중 에러: {e}")

        self.ui.disconnect()
        try:
            pygame.mixer.quit()
        except Exception:
            pass

    def run(self) -> None:
        self.boot()

        try:
            while self.running:
                self.ui.status("standby")
                user_text = self.ui.get_text_input(timeout=1.0)
                if not user_text:
                    continue

                log.info(f"[USER] {user_text}")
                self.ui.log_message("user", user_text)

                if self._is_exit_command(user_text):
                    break

                self.ui.status("thinking")

                try:
                    result = self.llm.ask(user_text)
                except Exception as e:
                    log.error(f"LLM 호출 중 예외: {e}", exc_info=True)
                    self.ui.log_message("jarvis", f"내부 오류: {e}")
                    self.ui.status("standby")
                    continue

                log.info(f"[JARVIS] action={result['action']} text={result['text']}")

                try:
                    keep = self.executor.execute(
                        result["action"], result["query"], result["text"]
                    )
                except Exception as e:
                    log.error(f"액션 실행 중 예외: {e}", exc_info=True)
                    self.ui.status("standby")
                    continue

                if not keep:
                    break

        except KeyboardInterrupt:
            log.info("Ctrl+C 중단")
        except Exception as e:
            log.error(f"메인 루프 예외: {e}", exc_info=True)
        finally:
            self.shutdown()

    @staticmethod
    def _is_exit_command(text: str) -> bool:
        exits = ["자비스 종료", "시스템 종료", "꺼줘", "shutdown"]
        return any(e in text.lower() for e in exits)


# ═══════════════════════════════════════════════════════════
# Entry
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=== JARVIS PY 시작 ===", flush=True)
    try:
        print("Jarvis 인스턴스 생성 시도...", flush=True)
        j = Jarvis()
        print("생성 완료, run 시작", flush=True)
        j.run()
    except Exception as e:
        print(f"❌ 예외 발생: {type(e).__name__}: {e}", flush=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)
