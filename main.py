import os
import uuid
from datetime import datetime
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import firebase_admin
from firebase_admin import credentials, firestore
from openai import OpenAI
import pandas as pd
from pydantic import BaseModel, Field

# 1. 환경변수 로드
load_dotenv()

FIREBASE_KEY_PATH = os.getenv("FIREBASE_CREDENTIALS_PATH", "./serviceAccountKey.json")
OPENAI_MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-5-mini")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# 2. Firebase 초기화
if not firebase_admin._apps:
    cred = credentials.Certificate(FIREBASE_KEY_PATH)
    firebase_admin.initialize_app(cred)

db = firestore.client()
# 시드 데이터가 들어있는 컬렉션 이름으로 통일
DATA_COLLECTION = "execution_logs"
CONV_COLLECTION = "conversations"

# 3. 코디세이 프록시 연동 OpenAI 클라이언트
openai_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL")
)

app = FastAPI(title="Execution Coach & Time-Series Data API")

# 4. CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in CORS_ORIGINS] if CORS_ORIGINS != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 5. Pydantic 스키마 정의
class ExecutionLogCreate(BaseModel):
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="기록 날짜 (YYYY-MM-DD)")
    value: int = Field(..., ge=0, le=100, description="실행 점수 (0~100점)")
    memo: str = Field(..., min_length=1, max_length=500, description="실행 내용 및 고민 메모")

class ExecutionLogUpdate(BaseModel):
    value: Optional[int] = Field(None, ge=0, le=100, description="수정할 실행 점수 (0~100점)")
    memo: Optional[str] = Field(None, min_length=1, max_length=500, description="수정할 실행 내용 및 메모")

class ChatRequest(BaseModel):
    user_query: str = Field(
        default="최근 내 실행 상태를 바탕으로 오늘 집중해야 할 한 가지 피드백을 줘.",
        description="사용자 질문 또는 고민"
    )
    conversation_id: Optional[str] = Field(None, description="기존 대화 세션 ID (없으면 자동 생성)")

class MessageItem(BaseModel):
    role: str = Field(..., description="user 또는 assistant")
    content: str = Field(..., description="메시지 본문")
    timestamp: Optional[str] = None

class ConversationCreate(BaseModel):
    title: Optional[str] = "새로운 코칭 대화"
    messages: List[MessageItem] = []

# --- 프론트엔드 화면 서빙 (루트 경로) ---
@app.get("/")
def read_root():
    return FileResponse("index.html")


# ==========================================
# 1. 데이터 목록 및 등록 API (/api/data)
# ==========================================

# [GET] 데이터 목록 전체 조회
@app.get("/api/data")
@app.get("/api/logs")
def get_all_data():
    try:
        docs = db.collection(DATA_COLLECTION).order_by("date").stream()
        results = []
        for doc in docs:
            item = doc.to_dict()
            item["id"] = doc.id
            results.append(item)
        return {"total": len(results), "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# [POST] 새 데이터 등록
@app.post("/api/data", status_code=201)
@app.post("/api/logs", status_code=201)
def create_data(log: ExecutionLogCreate):
    try:
        doc_id = log.date
        doc_ref = db.collection(DATA_COLLECTION).document(doc_id)
        log_data = log.model_dump()
        doc_ref.set(log_data)
        return {"message": "Data saved successfully", "id": doc_id, "data": log_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 2. 데이터 통계 및 요약 API (/api/data/summary)
# ==========================================

@app.get("/api/data/summary")
@app.get("/api/analytics")
def get_data_summary():
    try:
        docs = db.collection(DATA_COLLECTION).order_by("date").stream()
        records = [doc.to_dict() for doc in docs]

        if not records:
            return {"message": "데이터가 충분하지 않습니다.", "total_count": 0}

        df = pd.DataFrame(records)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        overall_mean = round(float(df["value"].mean()), 1)
        recent_7_mean = round(float(df["value"].tail(7).mean()), 1)
        recent_30_mean = round(float(df["value"].tail(30).mean()), 1)
        max_value = int(df["value"].max())
        min_value = int(df["value"].min())

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
            trend = "데이터 수집 중"

        start_date = str(df["date"].min())[:10]
        end_date = str(df["date"].max())[:10]

        return {
            "period": f"{start_date} ~ {end_date}",
            "total_count": len(df),
            "overall_mean": overall_mean,
            "recent_7_mean": recent_7_mean,
            "recent_30_mean": recent_30_mean,
            "max_value": max_value,
            "min_value": min_value,
            "trend": trend
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 3. 데이터 단건 조회, 수정, 삭제 (/api/data/{id})
# ==========================================

# [GET] 특정 날짜/ID 단건 조회
@app.get("/api/data/{id}")
@app.get("/api/logs/{id}")
def get_data_by_id(id: str):
    doc = db.collection(DATA_COLLECTION).document(id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail=f"No data found for ID {id}")
    data = doc.to_dict()
    data["id"] = doc.id
    return data

# [PUT] 데이터 수정
@app.put("/api/data/{id}")
def update_data(id: str, update_req: ExecutionLogUpdate):
    try:
        doc_ref = db.collection(DATA_COLLECTION).document(id)
        doc = doc_ref.get()
        if not doc.exists:
            raise HTTPException(status_code=404, detail=f"No data found for ID {id}")

        update_fields = {k: v for k, v in update_req.model_dump().items() if v is not None}
        if not update_fields:
            raise HTTPException(status_code=400, detail="No fields to update")

        doc_ref.update(update_fields)
        updated_data = doc_ref.get().to_dict()
        updated_data["id"] = id
        return {"message": "Data updated successfully", "data": updated_data}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# [DELETE] 데이터 삭제
@app.delete("/api/data/{id}")
def delete_data(id: str):
    try:
        doc_ref = db.collection(DATA_COLLECTION).document(id)
        if not doc_ref.get().exists:
            raise HTTPException(status_code=404, detail=f"No data found for ID {id}")

        doc_ref.delete()
        return {"message": f"Data with ID {id} deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 4. AI 챗봇 API (컨텍스트 주입 + 자동 저장)
# ==========================================

@app.post("/api/chat")
@app.post("/api/coach")
def chat_with_coach(request: ChatRequest):
    try:
        summary = get_data_summary()
        summary_text = (
            f"- 분석 기간: {summary.get('period', 'N/A')}\n"
            f"- 총 데이터 수: {summary.get('total_count', 0)}개\n"
            f"- 전체 평균: {summary.get('overall_mean', 0)}점 (최대: {summary.get('max_value', 0)}점, 최저: {summary.get('min_value', 0)}점)\n"
            f"- 최근 7일 평균: {summary.get('recent_7_mean', 0)}점 / 최근 30일 평균: {summary.get('recent_30_mean', 0)}점\n"
            f"- 최근 실행 추세: {summary.get('trend', '분석 불가')}"
        )

        docs = db.collection(DATA_COLLECTION).order_by("date", direction=firestore.Query.DESCENDING).limit(7).stream()
        recent_logs = [doc.to_dict() for doc in docs]
        recent_logs.reverse()

        logs_text = "\n".join([f"- {item['date']}: {item['value']}점 ({item['memo']})" for item in recent_logs])

        system_prompt = f"""
당신은 '생각 vs 실행' 격차를 줄여주는 단호하고 현실적인 전문 AI 실행 코치입니다.
사용자의 전체 데이터 통계 요약과 최근 7일 실행 기록을 분석하여 통찰력 있는 맞춤 피드백을 제공하세요.

[시계열 데이터 통계 요약]
{summary_text}

[최근 7일 상세 기록]
{logs_text}

[답변 원칙]
1. 데이터 요약 수치(평균, 추세)를 근거로 들어 구체적으로 상태를 진단할 것.
2. 생각 과잉/실행 지연 패턴을 직설적으로 짚고, 오늘 즉시 실천할 15분 단위 초소형 액션 1가지를 제시할 것.
3. 3~4문장 내외로 간결하고 임팩트 있게 답변할 것.
"""

        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": request.user_query}
            ],
            temperature=0.7,
        )
        ai_reply = response.choices[0].message.content

        conv_id = request.conversation_id or str(uuid.uuid4())[:8]
        now_str = datetime.now().isoformat()

        user_msg = {"role": "user", "content": request.user_query, "timestamp": now_str}
        assistant_msg = {"role": "assistant", "content": ai_reply, "timestamp": now_str}

        conv_ref = db.collection(CONV_COLLECTION).document(conv_id)
        conv_doc = conv_ref.get()

        if conv_doc.exists:
            current_messages = conv_doc.to_dict().get("messages", [])
            current_messages.extend([user_msg, assistant_msg])
            conv_ref.update({
                "messages": current_messages,
                "updated_at": now_str
            })
        else:
            first_title = request.user_query[:25] + ("..." if len(request.user_query) > 25 else "")
            conv_ref.set({
                "id": conv_id,
                "title": first_title,
                "created_at": now_str,
                "updated_at": now_str,
                "messages": [user_msg, assistant_msg]
            })

        return {
            "conversation_id": conv_id,
            "query": request.user_query,
            "response": ai_reply,
            "coaching_feedback": ai_reply
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ==========================================
# 5. 대화 기록 관리 API (/api/conversations)
# ==========================================

@app.get("/api/conversations")
def get_conversations():
    try:
        docs = db.collection(CONV_COLLECTION).order_by("updated_at", direction=firestore.Query.DESCENDING).stream()
        results = []
        for doc in docs:
            item = doc.to_dict()
            item["id"] = doc.id
            summary_item = {
                "id": item["id"],
                "title": item.get("title", "대화 세션"),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "message_count": len(item.get("messages", []))
            }
            results.append(summary_item)
        return {"total": len(results), "conversations": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/conversations", status_code=201)
def create_conversation(conv: ConversationCreate):
    try:
        conv_id = str(uuid.uuid4())[:8]
        now_str = datetime.now().isoformat()
        conv_data = {
            "id": conv_id,
            "title": conv.title,
            "created_at": now_str,
            "updated_at": now_str,
            "messages": [m.model_dump() for m in conv.messages]
        }
        db.collection(CONV_COLLECTION).document(conv_id).set(conv_data)
        return {"message": "Conversation created", "conversation": conv_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/conversations/{id}")
def get_conversation_detail(id: str):
    doc = db.collection(CONV_COLLECTION).document(id).get()
    if not doc.exists:
        raise HTTPException(status_code=404, detail=f"No conversation found with ID {id}")
    data = doc.to_dict()
    data["id"] = doc.id
    return data

@app.delete("/api/conversations/{id}")
def delete_conversation(id: str):
    doc_ref = db.collection(CONV_COLLECTION).document(id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail=f"No conversation found with ID {id}")
    doc_ref.delete()
    return {"message": f"Conversation {id} deleted successfully"}