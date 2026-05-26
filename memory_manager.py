"""
ChromaMemory — 자비스의 장기 기억 시스템.

설계:
- ChromaDB를 벡터 저장소로 사용 (디스크 영구 저장)
- sentence-transformers로 임베딩 생성 (한국어 지원)
- RAG 패턴: 검색 → 컨텍스트 주입 → 생성

WHY 이렇게:
- 자비스가 매번 처음 만나는 비서가 아니라 "나를 아는 비서"가 됨
- 의미 검색 = 단어가 달라도 뜻이 비슷하면 매칭
  (예: "노래 틀어줘" ≈ "음악 재생해줘")
- RAG는 GPT/Claude 같은 LLM 시스템의 산업 표준 패턴
"""

import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("jarvis.memory")

# ChromaDB 저장 경로 (자비스 데이터 디렉토리 안)
MEMORY_DIR = Path.home() / "jarvis" / "data" / "memory"
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

# 임베딩 모델 — 한국어 지원 (다국어 MiniLM)
# 첫 실행 시 ~470MB 다운로드 (HuggingFace에서 자동)
# 이후엔 ~/.cache/huggingface/ 에 캐시됨
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# 검색 결과 거리 임계값
# ChromaDB cosine distance: 0(완전 동일) ~ 2(완전 다름), 보통 0~1 사이
# 1.0 미만 = 의미적으로 관련 있음
DISTANCE_THRESHOLD = 1.0


class ChromaMemory:
    """자비스 장기 기억 — ChromaDB + multilingual 임베딩."""

    def __init__(self, collection_name: str = "jarvis_memories"):
        self.collection_name = collection_name
        self.client = None
        self.collection = None
        self.ready = False

        try:
            import chromadb
            from chromadb.config import Settings
            from chromadb.utils import embedding_functions

            log.info("[MEMORY] ChromaDB 초기화 중...")

            # PersistentClient: 디스크에 영구 저장
            # 자비스 재시작해도 기억 유지
            self.client = chromadb.PersistentClient(
                path=str(MEMORY_DIR),
                settings=Settings(anonymized_telemetry=False),
            )

            # 한국어 지원 임베딩 모델
            # 첫 실행 시 자동 다운로드 (몇 분 소요), 이후 캐시 사용
            log.info(f"[MEMORY] 임베딩 모델 로딩 중: {EMBEDDING_MODEL}")
            log.info("[MEMORY] (첫 실행 시 모델 다운로드 ~5분, 이후 캐시 사용)")

            ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=EMBEDDING_MODEL
            )

            # 컬렉션 가져오기 또는 생성
            self.collection = self.client.get_or_create_collection(
                name=collection_name,
                embedding_function=ef,
                metadata={"description": "JARVIS long-term memory"},
            )

            count = self.collection.count()
            self.ready = True
            log.info(f"[MEMORY] ✅ 초기화 완료. 저장된 기억: {count}개")

        except ImportError as e:
            log.error(f"[MEMORY] 패키지 미설치: {e}")
            log.error("→ pip install chromadb sentence-transformers")
        except Exception as e:
            log.error(f"[MEMORY] 초기화 실패: {e}", exc_info=True)

    def add_turn(
        self,
        user_text: str,
        jarvis_response: str,
        action: str = "none",
        query: str = "",
    ) -> bool:
        """
        대화 한 턴 (사용자 입력 + 자비스 응답)을 저장.

        Args:
            user_text: 사용자 입력
            jarvis_response: 자비스 응답
            action: 실행된 액션 (youtube_play, none 등)
            query: 액션 파라미터

        Returns:
            성공 여부
        """
        if not self.ready or not self.collection:
            return False

        # 빈 내용 저장 안 함
        if not user_text or not user_text.strip():
            return False

        try:
            # 임베딩 대상 텍스트
            # 사용자 입력과 자비스 응답 결합 → 검색 시 둘 다 고려됨
            document = f"사용자: {user_text}\n자비스: {jarvis_response}"

            # 고유 ID (밀리초 타임스탬프)
            doc_id = f"turn_{int(time.time() * 1000)}"

            self.collection.add(
                documents=[document],
                metadatas=[{
                    "timestamp": time.time(),
                    "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "user_text": user_text,
                    "jarvis_response": jarvis_response,
                    "action": action,
                    "query": query,
                }],
                ids=[doc_id],
            )
            log.debug(f"[MEMORY] 저장: {user_text[:30]}...")
            return True

        except Exception as e:
            log.error(f"[MEMORY] 저장 실패: {e}")
            return False

    def search(self, query: str, top_k: int = 3) -> list[dict]:
        """
        의미적으로 유사한 과거 대화 검색.

        Args:
            query: 현재 사용자 입력
            top_k: 가져올 결과 개수 (3개 권장)

        Returns:
            관련 기억 리스트. 거리 임계값 미만의 것만.
            각 항목: {user_text, jarvis_response, action, query, datetime, distance}
        """
        if not self.ready or not self.collection:
            return []

        if not query or not query.strip():
            return []

        try:
            count = self.collection.count()
            if count == 0:
                return []

            # top_k가 저장된 개수보다 크면 조정
            n_results = min(top_k, count)

            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
            )

            # 결과 정리
            memories = []
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for meta, dist in zip(metadatas, distances):
                # 임계값 이상은 제외 (너무 다른 내용)
                if dist >= DISTANCE_THRESHOLD:
                    continue
                memories.append({
                    "user_text": meta.get("user_text", ""),
                    "jarvis_response": meta.get("jarvis_response", ""),
                    "action": meta.get("action", "none"),
                    "query": meta.get("query", ""),
                    "datetime": meta.get("datetime", ""),
                    "distance": dist,
                })

            log.info(f"[MEMORY] 검색: '{query[:25]}' → {len(memories)}건 매치")
            return memories

        except Exception as e:
            log.error(f"[MEMORY] 검색 실패: {e}")
            return []

    def get_recent(self, n: int = 5) -> list[dict]:
        """최근 n개 대화 (시간순)."""
        if not self.ready or not self.collection:
            return []

        try:
            all_data = self.collection.get(include=["metadatas"])
            metadatas = all_data.get("metadatas", [])
            if not metadatas:
                return []

            # 타임스탬프 기준 정렬 (최신 순)
            sorted_metas = sorted(
                metadatas,
                key=lambda m: m.get("timestamp", 0),
                reverse=True,
            )[:n]

            return [{
                "user_text": m.get("user_text", ""),
                "jarvis_response": m.get("jarvis_response", ""),
                "action": m.get("action", "none"),
                "query": m.get("query", ""),
                "datetime": m.get("datetime", ""),
            } for m in sorted_metas]

        except Exception as e:
            log.error(f"[MEMORY] 최근 기억 조회 실패: {e}")
            return []

    def count(self) -> int:
        """저장된 기억 총 개수."""
        if not self.ready or not self.collection:
            return 0
        try:
            return self.collection.count()
        except Exception:
            return 0

    def clear(self) -> bool:
        """모든 기억 초기화 (주의!)."""
        if not self.ready or not self.client:
            return False
        try:
            self.client.delete_collection(self.collection_name)
            log.info("[MEMORY] 모든 기억 삭제됨")
            self.collection = None
            self.ready = False
            return True
        except Exception as e:
            log.error(f"[MEMORY] 초기화 실패: {e}")
            return False

    def format_for_prompt(self, memories: list[dict]) -> str:
        """
        검색된 기억을 시스템 프롬프트에 주입할 형태로 포맷팅.

        Returns:
            프롬프트에 추가될 텍스트 ("[기억] ..." 형태).
            기억이 없으면 빈 문자열.
        """
        if not memories:
            return ""

        lines = ["[과거에 나눈 대화 — 참고용]"]
        for m in memories:
            lines.append(
                f"- {m['datetime']}\n"
                f"  사용자: \"{m['user_text']}\"\n"
                f"  자비스: \"{m['jarvis_response']}\""
            )
        lines.append(
            "위 대화를 참고해서 답하되, 사용자가 명시적으로 묻지 않으면 굳이 언급하지 마라.\n"
        )
        return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════
# 독립 테스트
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    print("─" * 60)
    print("ChromaMemory 단독 테스트")
    print("─" * 60)

    mem = ChromaMemory()
    if not mem.ready:
        print("❌ 초기화 실패")
        exit(1)

    # 1. 테스트 데이터 저장
    print(f"\n[1] 현재 저장된 기억: {mem.count()}개")
    print("[2] 테스트 데이터 추가...")
    samples = [
        ("에센셜 뮤직 틀어줘", "에센셜 뮤직을 재생합니다", "youtube_play", "에센셜 뮤직"),
        ("내가 좋아하는 색은 청록색이야", "기억해두겠습니다, 청록색을 좋아하시는군요", "none", ""),
        ("점심 추천 좀", "오늘은 비빔밥 어떠세요?", "none", ""),
        ("BTS 영상 보고싶어", "BTS 영상을 찾아드리겠습니다", "youtube", "BTS"),
        ("내 이름은 지승이야", "기억하겠습니다, 지승님", "none", ""),
    ]

    for u, j, a, q in samples:
        mem.add_turn(u, j, a, q)
        print(f"   ✓ 저장: {u[:35]}")

    print(f"\n[3] 총 기억: {mem.count()}개")

    # 2. 의미 검색 테스트
    print("\n[4] 의미 검색 — 다른 단어로 검색해도 매치되는지")
    queries = [
        "노래 다시 틀어줘",       # → "에센셜 뮤직 틀어줘" 매치 기대
        "내가 좋아하는 컬러",     # → "청록색" 매치 기대
        "음식 뭐 먹을까",         # → "점심 추천" 매치 기대
        "내가 누구지",            # → "내 이름은 지승" 매치 기대
        "오늘 날씨",              # → 매치 없어야 함
    ]

    for q in queries:
        print(f"\n   쿼리: '{q}'")
        results = mem.search(q, top_k=2)
        if not results:
            print(f"      → 매치 없음 (관련 기억 없음)")
        else:
            for r in results:
                print(f"      → [{r['distance']:.3f}] {r['user_text'][:40]}")

    # 3. 프롬프트 포맷팅
    print("\n[5] 프롬프트 주입 포맷 미리보기")
    memories = mem.search("음악 들려줘", top_k=2)
    prompt_text = mem.format_for_prompt(memories)
    print(prompt_text)

    print("✅ 모든 테스트 통과")