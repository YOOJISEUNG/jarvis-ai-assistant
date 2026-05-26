"""
Playwright 첫 테스트 — WSL에서 Windows 브라우저 띄우기.

WHY 이 스크립트:
- Playwright가 WSL 환경에서 잘 작동하는지 검증
- 실제 사이트 (유튜브) 접속 가능한지 확인
- 자비스 코드에 통합하기 전 격리 테스트

⚠️ 파일명 주의: 'playwright.py' 로 저장하지 말 것!
   파이썬이 자기 자신을 import 하려고 시도해서 ModuleNotFoundError 발생.
   반드시 'test_playwright.py' 또는 다른 이름 사용.
"""

import sys
import time

# 진단용 — 진짜 playwright 패키지가 import 되는지 먼저 확인
try:
    from playwright.sync_api import sync_playwright
    print("[OK] playwright 패키지 import 성공")
except ModuleNotFoundError as e:
    print(f"[FAIL] playwright import 실패: {e}")
    print("→ 다음 중 하나 시도:")
    print("  1. pip install playwright")
    print("  2. 이 파일 이름이 'playwright.py'면 다른 이름으로 변경")
    print("  3. __pycache__ 폴더 삭제 후 재시도")
    sys.exit(1)


def test_basic_browser():
    """가장 단순한 케이스: 브라우저 열고, 유튜브 가고, 닫기."""
    print("\n" + "=" * 50)
    print("[TEST] Playwright 브라우저 테스트 시작")
    print("=" * 50)

    with sync_playwright() as p:
        # headless=False: 실제 브라우저 창 보임 (자비스 답게)
        # headless=True: 백그라운드 (눈에 안 보임 - WSL에서 GUI 없을 때 안전)
        print("[1/5] Chromium 실행 중...")
        try:
            browser = p.chromium.launch(
                headless=False,
                args=["--start-maximized"],
            )
        except Exception as e:
            print(f"[FAIL] 브라우저 실행 실패: {e}")
            print("→ WSL GUI 환경 문제일 가능성. headless=True 로 재시도 권장")
            return False

        print("[2/5] 컨텍스트 생성 중...")
        # 컨텍스트 = 독립된 브라우저 프로필 (쿠키/세션 분리)
        context = browser.new_context(
            viewport=None,
            locale="ko-KR",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36",
        )

        page = context.new_page()
        print("[3/5] 유튜브 접속 중...")
        try:
            page.goto("https://www.youtube.com", wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            print(f"[FAIL] 유튜브 접속 실패: {e}")
            browser.close()
            return False

        title = page.title()
        url = page.url
        print(f"[4/5] 페이지 정보:")
        print(f"      - 제목: {title}")
        print(f"      - URL:  {url}")

        print("[5/5] 10초 대기 (브라우저 확인 시간)...")
        for i in range(10, 0, -1):
            print(f"      {i}초 남음...", end="\r")
            time.sleep(1)
        print(" " * 30, end="\r")

        print("[CLEANUP] 브라우저 종료 중...")
        browser.close()

    print("=" * 50)
    print("[SUCCESS] 테스트 완료!")
    print("=" * 50)
    return True


if __name__ == "__main__":
    success = test_basic_browser()
    sys.exit(0 if success else 1)