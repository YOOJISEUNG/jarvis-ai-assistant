"""
JARVIS UI Server
─────────────────────────────────────
역할:
  1. Flask + SocketIO 서버 (포트 3000)
  2. UI (HTML)에 정적 파일 서빙
  3. 자비스 코어 (jarvis.py) ↔ UI (브라우저) 중계
  4. 시스템 정보 (CPU/MEM), 날씨, 위치 실시간 전송

아키텍처:
  jarvis.py ──socketio.Client──▶ server.py ──websocket──▶ index.html
                                     │
                                     ├── /api/weather  (날씨 갱신)
                                     └── /api/location (위치 갱신)
"""

from __future__ import annotations

import os
import time
import threading
import logging
from pathlib import Path
from typing import Any

import psutil
import requests
from dotenv import load_dotenv
from flask import Flask, send_from_directory, jsonify
from flask_socketio import SocketIO

# ─────────────────────────────────────
# 환경 변수 로딩
# ─────────────────────────────────────
load_dotenv()

WEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
SERVER_PORT = int(os.getenv("SERVER_PORT", "3000"))
USER_NAME = os.getenv("USER_NAME", "지승")

# ─────────────────────────────────────
# 로거 설정
# ─────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [SERVER] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("jarvis-server")

# Flask 자체 로그는 조용히
logging.getLogger("werkzeug").setLevel(logging.WARNING)

# ─────────────────────────────────────
# Flask + SocketIO 초기화
# ─────────────────────────────────────
BASE_DIR = Path(__file__).parent
UI_DIR = BASE_DIR / "ui"

app = Flask(__name__, static_folder=str(UI_DIR))
app.config["SECRET_KEY"] = "jarvis-internal-only"

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode="threading",
    logger=False,
    engineio_logger=False,
)

# ─────────────────────────────────────
# 외부 상태 (캐시)
# ─────────────────────────────────────
class State:
    """서버 전역 상태 — 마지막 알려진 값 캐싱."""
    location: dict[str, Any] = {
        "city": "감지 중...",
        "region": "",
        "lat": 37.5665,   # 서울 기본
        "lon": 126.9780,
    }
    weather: dict[str, Any] = {
        "temp": "--",
        "feels": "--",
        "desc": "로딩 중...",
        "humidity": "--",
        "icon": "01d",
    }
    last_status: str = "standby"


state = State()

# ─────────────────────────────────────
# 위치/날씨 헬퍼
# ─────────────────────────────────────
def fetch_ip_location() -> dict[str, Any]:
    """IP 기반 대략적 위치 — 첫 진입 시 폴백용."""
    try:
        res = requests.get("http://ip-api.com/json/?lang=ko", timeout=5)
        data = res.json()
        return {
            "city": data.get("city", "알 수 없음"),
            "region": data.get("regionName", ""),
            "lat": float(data.get("lat", 37.5665)),
            "lon": float(data.get("lon", 126.9780)),
        }
    except Exception as e:
        log.warning(f"IP 위치 조회 실패: {e}")
        return state.location


def reverse_geocode(lat: float, lon: float) -> dict[str, Any]:
    """좌표 → 도시명 (브라우저 GPS 결과 받았을 때 사용)."""
    try:
        res = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json", "accept-language": "ko"},
            headers={"User-Agent": "JarvisApp/1.0"},
            timeout=5,
        )
        addr = res.json().get("address", {})
        city = (
            addr.get("city")
            or addr.get("town")
            or addr.get("county")
            or addr.get("suburb")
            or "알 수 없음"
        )
        return {
            "city": city,
            "region": addr.get("state", ""),
            "lat": lat,
            "lon": lon,
        }
    except Exception as e:
        log.warning(f"역지오코딩 실패: {e}")
        return {"city": "알 수 없음", "region": "", "lat": lat, "lon": lon}


def fetch_weather(lat: float, lon: float) -> dict[str, Any]:
    """OpenWeather 현재 날씨."""
    if not WEATHER_API_KEY or WEATHER_API_KEY.startswith("여기에"):
        return {
            "temp": "--",
            "feels": "--",
            "desc": "API 키 없음",
            "humidity": "--",
            "icon": "01d",
        }
    try:
        res = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={
                "lat": lat,
                "lon": lon,
                "appid": WEATHER_API_KEY,
                "units": "metric",
                "lang": "kr",
            },
            timeout=5,
        )
        data = res.json()
        return {
            "temp": round(data["main"]["temp"]),
            "feels": round(data["main"]["feels_like"]),
            "desc": data["weather"][0]["description"],
            "humidity": data["main"]["humidity"],
            "icon": data["weather"][0]["icon"],
        }
    except Exception as e:
        log.warning(f"날씨 조회 실패: {e}")
        return {
            "temp": "--",
            "feels": "--",
            "desc": "조회 실패",
            "humidity": "--",
            "icon": "01d",
        }


# ─────────────────────────────────────
# 백그라운드 모니터
# ─────────────────────────────────────
def background_monitor() -> None:
    """CPU/MEM 2초마다, 날씨 5분마다 브로드캐스트."""
    # 초기 위치/날씨
    state.location = fetch_ip_location()
    state.weather = fetch_weather(state.location["lat"], state.location["lon"])
    socketio.emit("location_update", state.location)
    socketio.emit("weather_update", state.weather)

    weather_tick = 0
    while True:
        try:
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory().percent
            disk = psutil.disk_usage("/").percent
            socketio.emit("system_update", {"cpu": cpu, "mem": mem, "disk": disk})

            weather_tick += 1
            # 5분마다 날씨 갱신 (1초 sleep × 약 300번)
            if weather_tick >= 150:
                state.weather = fetch_weather(
                    state.location["lat"], state.location["lon"]
                )
                socketio.emit("weather_update", state.weather)
                weather_tick = 0

            time.sleep(1)
        except Exception as e:
            log.error(f"모니터 루프 에러: {e}")
            time.sleep(2)


# ─────────────────────────────────────
# 소켓 이벤트 — UI(브라우저) 측
# ─────────────────────────────────────
@socketio.on("connect")
def on_connect() -> None:
    """UI 접속 시 현재 상태 전송."""
    log.info("UI 클라이언트 접속")
    socketio.emit("location_update", state.location)
    socketio.emit("weather_update", state.weather)
    socketio.emit("status_update", {"status": state.last_status})
    socketio.emit("user_info", {"name": USER_NAME})


@socketio.on("disconnect")
def on_disconnect() -> None:
    log.info("UI 클라이언트 끊김")


@socketio.on("update_location")
def on_update_location(data: dict[str, Any]) -> None:
    """브라우저 GPS 좌표 받으면 위치+날씨 갱신."""
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is None or lon is None:
        return
    state.location = reverse_geocode(lat, lon)
    state.weather = fetch_weather(lat, lon)
    socketio.emit("location_update", state.location)
    socketio.emit("weather_update", state.weather)
    log.info(f"위치 갱신: {state.location['city']}")


# ─────────────────────────────────────
# 소켓 이벤트 — JARVIS 코어 측
# (jarvis.py가 socketio.Client로 접속해서 보냄)
# ─────────────────────────────────────
@socketio.on("jarvis_status")
def on_jarvis_status(data: dict[str, Any]) -> None:
    """자비스 상태 변경 → 모든 UI에 브로드캐스트."""
    status = data.get("status", "standby")
    state.last_status = status
    socketio.emit("status_update", {"status": status})


@socketio.on("jarvis_log")
def on_jarvis_log(data: dict[str, Any]) -> None:
    """대화 로그 → UI에 브로드캐스트."""
    socketio.emit("log_update", data)


@socketio.on("jarvis_waveform")
def on_jarvis_waveform(data: dict[str, Any]) -> None:
    """실시간 음성 파형 데이터 → UI에 브로드캐스트."""
    socketio.emit("waveform_update", data)

# ─────────────────────────────────────
# 소켓 이벤트 — UI → 자비스 (텍스트 입력)
# ─────────────────────────────────────
@socketio.on("user_text_input")
def on_user_text(data: dict[str, Any]) -> None:
    """UI에서 입력한 텍스트를 자비스 코어로 전달."""
    text = data.get("text", "").strip()
    if not text:
        return
    log.info(f"텍스트 입력 수신: {text}")
    socketio.emit("text_command", {"text": text})

# ─────────────────────────────────────
# REST API (옵션)
# ─────────────────────────────────────
@app.route("/api/health")
def health() -> Any:
    return jsonify(
        {
            "status": "ok",
            "user": USER_NAME,
            "location": state.location,
            "weather": state.weather,
            "jarvis_status": state.last_status,
        }
    )


# ─────────────────────────────────────
# 정적 파일
# ─────────────────────────────────────
@app.route("/")
def index() -> Any:
    return send_from_directory(str(UI_DIR), "index.html")


@app.route("/<path:filename>")
def static_files(filename: str) -> Any:
    return send_from_directory(str(UI_DIR), filename)


# ─────────────────────────────────────
# 실행
# ─────────────────────────────────────
def start_server() -> None:
    """jarvis.py에서 import해서 호출하는 함수 (사용 안 해도 됨)."""
    monitor = threading.Thread(target=background_monitor, daemon=True)
    monitor.start()
    socketio.run(
        app,
        host="127.0.0.1",
        port=SERVER_PORT,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    log.info(f"JARVIS UI 서버 시작 → http://127.0.0.1:{SERVER_PORT}")
    log.info(f"사용자: {USER_NAME}")
    monitor = threading.Thread(target=background_monitor, daemon=True)
    monitor.start()
    socketio.run(
        app,
        host="127.0.0.1",
        port=SERVER_PORT,
        debug=False,
        use_reloader=False,
        allow_unsafe_werkzeug=True,
    )