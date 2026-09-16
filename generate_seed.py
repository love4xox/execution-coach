import json
import random
from datetime import date, timedelta

# 오늘 기준 최근 100일 역산
end_date = date.today()
start_date = end_date - timedelta(days=99)

memo_pool = {
    "high": [
        "기획 1시간 후 즉시 코드 작성 돌입. 목표 분량 초과 달성.",
        "생각보다 손이 먼저 움직임. 테스트 케이스 작성 완료.",
        "고민 없이 바로 기능 구현 착수. 실행력 최상.",
        "핵심 기능 MVP 프로토타입 완성. 실질적 성과 있음."
    ],
    "medium": [
        "기획 2시간, 개발 2시간. 고민이 조금 길었으나 착수 성공.",
        "아키텍처 구조 생각하느라 초반 지연, 이후 구현 완료.",
        "아이디어 정리 후 기본 CRUD 엔드포인트 작성 완료.",
        "집중력이 약간 분산되었으나 기본 목표치는 달성."
    ],
    "low": [
        "자료 조사와 기획만 4시간 지속. 코드 한 줄도 못 씀.",
        "완벽주의 성향 발동으로 설계만 반복 수정함. 착수 지연.",
        "유튜브 강의만 시청하고 직접 실습하지 못함.",
        "생각이 너무 많아 머리만 아프고 실제 산출물 0건."
    ]
}

data_list = []

for i in range(100):
    curr_date = start_date + timedelta(days=i)
    base_score = 60 + (i * 0.15)
    noise = random.randint(-20, 20)
    score = int(max(15, min(100, base_score + noise)))
    
    if score >= 75:
        memo = random.choice(memo_pool["high"])
    elif score >= 50:
        memo = random.choice(memo_pool["medium"])
    else:
        memo = random.choice(memo_pool["low"])
        
    data_list.append({
        "date": curr_date.isoformat(),
        "value": score,
        "memo": memo
    })

with open("seed_data.json", "w", encoding="utf-8") as f:
    json.dump(data_list, f, ensure_ascii=False, indent=2)

print(f"생성 완료: 총 {len(data_list)}개 데이터가 seed_data.json에 저장되었습니다.")