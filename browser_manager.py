"""
BrowserManager — Playwright 브라우저 영구 세션 관리자.

설계 원칙:
- 싱글톤: 자비스 전체에서 브라우저 1개만 유지
- 영구 세션: 사용자 데이터 (쿠키/로그인) 디스크에 저장
- 안전: 시작/종료 시 정리, 예외 시 복구

WHY 영구 세션:
- 매 액션마다 브라우저 띄우면 3~5초 낭비
- 로그인 상태 유지 (유튜브/구글 알고리즘 활용)
- 브라우저 시작 비용 = 큰 비용 (메모리, 디스크 I/O)
"""

import os
import logging
import threading
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Playwright

log = logging.getLogger("jarvis.browser")

# 사용자 데이터 저장 경로 (쿠키, 로그인, 캐시 등)
BROWSER_DATA_DIR = Path.home() / "jarvis" / "data" / "browser_profile"
BROWSER_DATA_DIR.mkdir(parents=True, exist_ok=True)


class BrowserManager:
    """
    영구 Playwright 브라우저 세션.

    싱글톤 패턴 — 자비스 전체에서 단 하나의 인스턴스.

    Usage:
        bm = BrowserManager()
        bm.start()                      # 시작 시 1회
        page = bm.new_page()            # 새 탭
        page.goto("https://...")
        bm.shutdown()                   # 종료 시 1회
    """

    _instance: Optional["BrowserManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        # 싱글톤 보장 (멀티스레드 환경 대응)
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # __init__는 여러 번 호출될 수 있음 → 한 번만 초기화
        if hasattr(self, "_initialized"):
            return
        self._initialized = True

        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._headless: bool = False
        self._ready: bool = False

    def start(self, headless: bool = False) -> bool:
        """
        브라우저 세션 시작.

        Args:
            headless: True면 창 안 보임, False면 보임

        Returns:
            성공 여부
        """
        if self._ready:
            log.info("[BROWSER] 이미 실행 중")
            return True

        self._headless = headless

        try:
            log.info(f"[BROWSER] 시작 중 (headless={headless})")
            self._playwright = sync_playwright().start()

            # launch_persistent_context로 사용자 데이터 영구 저장
            # → 로그인/쿠키 다음 실행에도 유지
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DATA_DIR),
                headless=headless,
                args=["--start-maximized"] if not headless else [],
                viewport=None if not headless else {"width": 1920, "height": 1080},
                locale="ko-KR",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=True,
            )

            self._ready = True
            log.info(f"[BROWSER] 시작 완료. 프로필: {BROWSER_DATA_DIR}")
            return True

        except Exception as e:
            log.error(f"[BROWSER] 시작 실패: {e}", exc_info=True)
            self._cleanup()
            return False

    def new_page(self) -> Optional[Page]:
        """
        새 탭 (페이지) 열기.

        Returns:
            Page 객체. 실패 시 None.
        """
        if not self._ready or not self._context:
            log.error("[BROWSER] 시작되지 않음 — new_page 불가")
            return None

        try:
            page = self._context.new_page()
            log.info("[BROWSER] 새 탭 생성")
            return page
        except Exception as e:
            log.error(f"[BROWSER] 새 탭 생성 실패: {e}")
            return None

    def get_or_create_page(self) -> Optional[Page]:
        """
        기존 탭 재사용 또는 새로 생성.
        탭이 있으면 첫 번째 탭, 없으면 새로 생성.
        """
        if not self._ready or not self._context:
            return None

        pages = self._context.pages
        if pages:
            return pages[0]
        return self.new_page()

    @property
    def is_ready(self) -> bool:
        return self._ready

    def shutdown(self) -> None:
        """브라우저 세션 종료 + 리소스 정리."""
        log.info("[BROWSER] 종료 처리 시작")
        self._cleanup()
        log.info("[BROWSER] 종료 완료")

    def _cleanup(self) -> None:
        """내부: 리소스 정리 (예외 시에도 안전)."""
        try:
            if self._context:
                self._context.close()
        except Exception as e:
            log.warning(f"[BROWSER] context 종료 중 에러: {e}")
        finally:
            self._context = None

        try:
            if self._playwright:
                self._playwright.stop()
        except Exception as e:
            log.warning(f"[BROWSER] playwright 종료 중 에러: {e}")
        finally:
            self._playwright = None

        self._ready = False


# 모듈 레벨 헬퍼 — 간편 접근용
def get_browser() -> BrowserManager:
    """전역 브라우저 매니저 가져오기."""
    return BrowserManager()


if __name__ == "__main__":
    # 독립 테스트
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

    bm = get_browser()
    print("─" * 50)
    print("BrowserManager 테스트")
    print("─" * 50)

    if not bm.start(headless=False):
        print("시작 실패. headless 재시도")
        if not bm.start(headless=True):
            print("최종 실패")
            exit(1)

    page = bm.new_page()
    if page:
        page.goto("https://www.youtube.com", wait_until="domcontentloaded")
        print(f"제목: {page.title()}")
        print("5초 대기...")
        import time
        time.sleep(5)

    bm.shutdown()
    print("완료")