import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI
import firebase_admin
from firebase_admin import credentials, firestore

# 1. 환경변수 로드
load_dotenv()

FIREBASE_KEY_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH", "./serviceAccountKey.json")
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-5-mini")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")

# 2. Firebase 초기화
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_KEY_PATH)
    firebase_admin.initialize_app(cred)

db = firestore.client()

# 3. 코디세이 프록시 연동 OpenAI 클라이언트
openai_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL")
)

app = FastAPI(title="Execution Coach API")

# 4. CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 5. Pydantic 스키마
class ExecutionLogCreate(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="기록 날짜 (YYYY-MM-DD)")
    value: int = Field(..., ge=0, le=100, description="실행 점수 (0~100점)")
    memo: str = Field(..., min_length=1, max_length=500, description="실행 내용 및 고민 메모")

class CoachRequest(BaseModel):
    user_query: str = Field(
        default="최근 내 실행 상태를 바탕으로 오늘 집중해야 할 한 가지 피드백을 줘.",
        description="사용자 질문 또는 고민"
    )

# --- 엔드포인트 ---
@app.get("/")
def read_root():
    return {"status": "healthy", "service": "Execution Coach API"}

# [GET] 전체 로그 목록 조회
@app.get("/api/logs")
def get_all_logs():
    try:
        docs = db.collection("execution_logs").order_by("date").stream()
        results = [doc.to_dict() for doc in docs]
        return {"total": len(results), "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# [POST] 신규 실행 로그 등록
@app.post("/api/logs", status_code=201)
def create_log(log: ExecutionLogCreate):
    try:
        doc_ref = db.collection("execution_logs").document(log.date)
        log_data = log.model_dump()
        doc_ref.set(log_data)
        return {"message": "Execution log saved successfully", "data": log_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# [GET] Pandas 시계열 통계 및 추세 분석
@app.get("/api/analytics")
def get_analytics():
    try:
        docs = db.collection("execution_logs").order_by("date").stream()
        records = [doc.to_dict() for doc in docs]
        
        if not records:
            return {"message": "데이터가 충분하지 않습니다."}

        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        overall_mean = round(float(df["value"].mean()), 1)
        recent_7_mean = round(float(df["value"].tail(7).mean()), 1)
        recent_30_mean = round(float(df["value"].tail(30).mean()), 1)

        if len(df) >= 14:
            prev_7_mean = df["value"].iloc[-14:-7].mean()
            diff = recent_7_mean - prev_7_mean
            if diff >= 5:
                trend = "상승세 (실행력 향상 중)"
            elif diff <= -5:
                trend = "하강세 (실행 지연 주의)"
            else:
                trend = "안정적 (유지 중)"
        else:
            trend = "데이터 분석 중"

        lowest_logs = df.nsmallest(3, "value")[["date", "value", "memo"]].to_dict(orient="records")
        highest_logs = df.nlargest(3, "value")[["date", "value", "memo"]].to_dict(orient="records")

        for item in lowest_logs + highest_logs:
            item["date"] = str(item["date"])[:10]

        return {
            "total_count": len(df),
            "overall_mean": overall_mean,
            "recent_7_mean": recent_7_mean,
            "recent_30_mean": recent_30_mean,
            "trend": trend,
            "highlight_lowest": lowest_logs,
            "highlight_highest": highest_logs,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# [POST] 최근 7일 로그 주입 기반 AI 맞춤 코칭
@app.post("/api/coach")
def get_coaching_feedback(request: CoachRequest):
    try:
        docs = db.collection("execution_logs").order_by("date", direction=firestore.Query.DESCENDING).limit(7).stream()
        recent_logs = [doc.to_dict() for doc in docs]
        recent_logs.reverse()

        context_text = "\n".join([f"- {item['date']}: 실행점수 {item['value']}점 / 메모: {item['memo']}" for item in recent_logs])

        system_prompt = f"""
당신은 '생각 vs 실행' 격차를 줄여주는 단호하고 현실적인 AI 실행 코치입니다.
사용자의 최근 실행 기록 데이터를 정밀하게 진단하고 직설적이면서도 구체적인 행동 피드백을 제공하세요.

[사용자의 최근 실행 기록]
{context_text}

[답변 원칙]
1. 단순한 응원이 아닌, 기록에 드러난 생각 과잉/실행 지연 패턴을 정확히 짚을 것.
2. 오늘 당장 실천할 수 있는 15분 단위의 '초소형 액션' 1가지를 명확히 제시할 것.
3. 3~4문장 내외로 간결하고 임팩트 있게 작성할 것.
"""

        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.user_query}
            ],
            temperature=0.7,
        )

        return {
            "query": request.user_query,
            "analyzed_days": len(recent_logs),
            "coaching_feedback": response.choices[0].message.content
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# [GET] 날짜별 단건 조회
@app.get("/api/logs/{date}")
def get_log_by_date(date: str):
    doc = db.collection("execution_logs").document(date).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail=f"No log found for date {date}")
    return doc.to_dict()