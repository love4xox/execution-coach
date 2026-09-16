import json
import firebase_admin
from firebase_admin import credentials, firestore

# 1. Firebase Admin SDK 인증
cred = credentials.Certificate("./serviceAccountKey.json")
firebase_admin.initialize_app(cred)

db = firestore.client()

# 2. 로컬 seed_data.json 읽기
with open("seed_data.json", "r", encoding="utf-8") as f:
    records = json.load(f)

# 3. Firestore execution_logs 컬렉션에 일괄 저장 (Batch)
batch = db.batch()
collection_ref = db.collection("execution_logs")

for item in records:
    # 날짜(date)를 문서 고유 ID로 지정
    doc_ref = collection_ref.document(item["date"])
    batch.set(doc_ref, item)

batch.commit()
print(f"업로드 성공: 총 {len(records)}개 데이터가 execution_logs 컬렉션에 등록되었습니다.")