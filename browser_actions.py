"""
BrowserActions — 자비스의 실제 웹 조작 액션들.

설계:
- BrowserManager에서 페이지 받아서 구체적 작업 수행
- 각 메서드는 독립적, 에러 시 False 반환 (자비스 계속 작동)
- 유튜브 자동 재생, 검색 등 사용자 의도에 맞는 액션

WHY 분리:
- BrowserManager = 인프라 (브라우저 자체 관리)
- BrowserActions = 비즈니스 로직 (실제 사용자 의도)
- SRP (단일 책임 원칙) 준수
"""

import logging
import time
from urllib.parse import quote
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from browser_manager import get_browser

log = logging.getLogger("jarvis.actions")


class BrowserActions:
    """자비스가 사용할 웹 조작 액션들."""

    def __init__(self):
        self.bm = get_browser()

    # ─────────────────────────────────────────────────────────
    # 유튜브 자동 재생
    # ─────────────────────────────────────────────────────────
    def youtube_play(self, query: str) -> bool:
        """
        유튜브에서 검색 → 첫 일반 영상 클릭 → 재생 시작.

        Args:
            query: 검색어 (예: "에센셜 뮤직")

        Returns:
            성공 여부
        """
        if not query or not query.strip():
            log.warning("[YT_PLAY] 빈 쿼리")
            return False

        if not self.bm.is_ready:
            log.error("[YT_PLAY] 브라우저 미시작")
            return False

        page = self.bm.get_or_create_page()
        if not page:
            log.error("[YT_PLAY] 페이지 생성 실패")
            return False

        try:
            # 1. 검색 결과 페이지 이동
            search_url = f"https://www.youtube.com/results?search_query={quote(query)}"
            log.info(f"[YT_PLAY] 검색 페이지 이동: {query}")
            page.goto(search_url, wait_until="domcontentloaded", timeout=20000)

            # 2. 첫 일반 영상 찾기 (Shorts 제외)
            #    ytd-video-renderer = 일반 영상 컨테이너
            #    a#video-title = 영상 제목 링크 (클릭 가능)
            log.info("[YT_PLAY] 영상 목록 로딩 대기...")
            video_selector = "ytd-video-renderer a#video-title"
            try:
                page.wait_for_selector(video_selector, timeout=10000)
            except PWTimeoutError:
                log.error("[YT_PLAY] 영상 목록 로딩 타임아웃")
                return False

            # 3. 첫 영상 정보 추출 (로깅용)
            first_video = page.locator(video_selector).first
            video_title = first_video.get_attribute("title") or "(제목 없음)"
            video_href = first_video.get_attribute("href") or ""
            log.info(f"[YT_PLAY] 선택 영상: {video_title[:50]}")
            log.info(f"[YT_PLAY] URL: {video_href}")

            # 4. 클릭 → 재생 페이지로 이동
            first_video.click()
            log.info("[YT_PLAY] 영상 클릭 완료, 재생 페이지 로딩 대기...")

            # 5. 비디오 플레이어 로딩 대기
            try:
                page.wait_for_selector("video.html5-main-video", timeout=15000)
                log.info("[YT_PLAY] 비디오 플레이어 로드됨")
            except PWTimeoutError:
                log.warning("[YT_PLAY] 비디오 플레이어 로드 타임아웃 — 그래도 진행")

            # 6. 자동 재생 보장
            #    유튜브가 일시정지 상태로 시작하는 경우 있음 → JS로 강제 재생
            time.sleep(1)  # JS 초기화 대기
            try:
                # video 요소 직접 제어
                is_playing = page.evaluate("""
                    () => {
                        const v = document.querySelector('video.html5-main-video');
                        if (!v) return false;
                        if (v.paused) {
                            v.play().catch(e => console.log('play blocked:', e));
                        }
                        return !v.paused;
                    }
                """)
                log.info(f"[YT_PLAY] 재생 상태: {'재생 중' if is_playing else '일시정지 (브라우저 제한 가능)'}")
            except Exception as e:
                log.warning(f"[YT_PLAY] 재생 확인 실패: {e}")

            log.info(f"[YT_PLAY] ✅ 성공: {video_title[:30]}")
            return True

        except Exception as e:
            log.error(f"[YT_PLAY] 실패: {e}", exc_info=True)
            return False

    # ─────────────────────────────────────────────────────────
    # 유튜브 검색만 (재생 X, 검색 페이지에 머무름)
    # ─────────────────────────────────────────────────────────
    def youtube_search(self, query: str) -> bool:
        """검색 페이지만 열기 (사용자가 직접 선택하게)."""
        if not query:
            return False

        page = self.bm.get_or_create_page()
        if not page:
            return False

        try:
            url = f"https://www.youtube.com/results?search_query={quote(query)}"
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            log.info(f"[YT_SEARCH] 검색 페이지: {query}")
            return True
        except Exception as e:
            log.error(f"[YT_SEARCH] 실패: {e}")
            return False

    # ─────────────────────────────────────────────────────────
    # 일반 URL 열기 (브라우저에서)
    # ─────────────────────────────────────────────────────────
    def open_url(self, url: str) -> bool:
        """임의 URL을 브라우저에서 열기."""
        if not url:
            return False

        page = self.bm.get_or_create_page()
        if not page:
            return False

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            log.info(f"[OPEN] {url}")
            return True
        except Exception as e:
            log.error(f"[OPEN] 실패: {e}")
            return False

    # ─────────────────────────────────────────────────────────
    # 구글 검색
    # ─────────────────────────────────────────────────────────
    def google_search(self, query: str) -> bool:
        """구글 검색 결과 페이지 열기."""
        if not query:
            return False
        url = f"https://www.google.com/search?q={quote(query)}"
        return self.open_url(url)


# ─────────────────────────────────────────────────────────
# 독립 테스트
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print("─" * 50)
    print("BrowserActions 통합 테스트")
    print("─" * 50)

    # 브라우저 시작
    bm = get_browser()
    if not bm.start(headless=False):
        print("headed 실패, headless 재시도")
        if not bm.start(headless=True):
            print("브라우저 시작 실패")
            sys.exit(1)

    actions = BrowserActions()

    # 테스트 1: 유튜브 자동 재생
    print("\n[TEST 1] 유튜브 자동 재생")
    test_query = input("검색어 입력 (Enter면 '에센셜 뮤직'): ").strip() or "에센셜 뮤직"
    success = actions.youtube_play(test_query)
    print(f"결과: {'✅ 성공' if success else '❌ 실패'}")

    if success:
        print("20초 동안 재생 확인...")
        time.sleep(20)

    bm.shutdown()
    print("\n완료")