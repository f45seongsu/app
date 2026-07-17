"""
F45 성수 · 오늘 예약 자동 갱신 v2 (검증된 bookings 방식)
────────────────────────────────────────────────────────────
예약(bookings) 전체를 받아 '오늘(KST)' 것만 골라 Supabase attendance 반영.
events 엔드포인트(빈값 문제) 대신, 수동 실행에서 검증된 방식 사용.

환경변수(깃허브 Secrets): GLOFOX_API_KEY, GLOFOX_API_TOKEN, SUPABASE_URL, SUPABASE_KEY
같은 폴더: name_cache.json
"""
import os, re, json, time, urllib.request
from datetime import datetime, timezone, timedelta
from supabase import create_client

BRANCH_ID = "696094f2184b8f3da50206f9"
BASE = "https://gf-api.aws.glofox.com/prod"
KST = timezone(timedelta(hours=9))
H = {"x-glofox-branch-id": BRANCH_ID,
     "x-api-key": os.environ["GLOFOX_API_KEY"],
     "x-glofox-api-token": os.environ["GLOFOX_API_TOKEN"],
     "Accept": "application/json"}
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
NC = json.load(open("name_cache.json", encoding="utf-8")) if os.path.exists("name_cache.json") else {}
SUR = set("김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽성차주우구민류진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제탁국어은편용봉빈사")

def api(url, tries=4):
    for t in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=45) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            if t == tries-1: print("  API err:", str(e)[:70]); return None  # 실패=None
            time.sleep(2)
    return None
def is_h(s): return bool(re.search(r"[가-힣]", str(s)))
def is_r(s): s=str(s).strip(); return bool(s) and bool(re.fullmatch(r"[A-Za-z .'-]+", s))
def clean(s):
    s=str(s).strip()
    if is_h(s):
        t=[x for x in re.split(r"\s+",s) if x]
        if len(t)==2 and len(t[1])==1 and t[1] in SUR: s=t[1]+t[0]
        elif len(t)==2 and len(t[0])==1 and t[0] in SUR: s=t[0]+t[1]
        else: s="".join(t)
        if len(s)>=3 and s[0]==s[1] and s[0] in SUR: s=s[1:]
        return s
    return NC.get(s, s) if is_r(s) else s

# ── 예약 전체 수집 (검증된 방식) ──
today = datetime.now(KST).strftime("%Y-%m-%d")
MAX_PAGES = int(os.environ.get("MAX_PAGES", "140"))   # 빠른 모드에서 낮춰 사용
bookings, p, fails, empty_ok = [], 1, 0, 0
while p <= MAX_PAGES:
    d = api(f"{BASE}/2.2/branches/{BRANCH_ID}/bookings?limit=200&page={p}")
    if d is None:                      # 통신 실패 → 멈추지 말고 재시도 후 다음 페이지
        fails += 1
        print(f"  page {p} 실패 → 재시도")
        time.sleep(3)
        d2 = api(f"{BASE}/2.2/branches/{BRANCH_ID}/bookings?limit=200&page={p}")
        if d2 is None:
            print(f"  page {p} 재실패 → 건너뜀(계속)")
            p += 1
            if fails >= 8: print("  실패 과다 → 중단"); break
            continue
        d = d2
    rows = d.get("data", []) if isinstance(d, dict) else []
    if not rows:                       # 성공했는데 빈 결과 = 진짜 끝
        empty_ok += 1
        if empty_ok >= 2: break        # 두 번 연속 빈 페이지면 확실히 끝
        p += 1; continue
    empty_ok = 0
    bookings.extend(rows)
    if len(rows) < 200: break          # 마지막 페이지
    p += 1
print(f"예약 전체 {len(bookings)}건 수집 (오늘={today}, 실패페이지 {fails}개)")

# ── 오늘 + 미래 예약 전부 (지난 것은 이미 저장돼 있음) ──
att_rows, new_people, seen = [], [], set()
for b in bookings:
    ts = str(b.get("time_start") or "")[:19]   # 이미 KST 벽시계
    if len(ts) < 10: continue
    if ts[:10] < today: continue               # 과거는 건너뜀(이미 반영됨)
    uid = str(b.get("user_id","")); eid = str(b.get("event_id",""))
    if not uid or not eid: continue
    key = (uid, eid)
    if key in seen: continue
    seen.add(key)
    att_rows.append({
        "glofox_user_id": uid, "event_id": eid,
        "class_name": b.get("event_name") or "수업",
        "class_time": ts if len(ts) >= 16 else None,
        "attended": bool(b.get("attended")),
        "status": b.get("status",""),
    })
    new_people.append({"person_id": uid, "glofox_user_id": uid,
                       "name": clean(b.get("user_name","")), "stage": "트라이얼"})

# ── 업서트 ──
if new_people:
    try: sb.table("people").upsert(new_people, on_conflict="person_id", ignore_duplicates=True).execute()
    except Exception as ex: print("  people stub 스킵:", str(ex)[:80])
if att_rows:
    for i in range(0, len(att_rows), 500):
        sb.table("attendance").upsert(att_rows[i:i+500], on_conflict="glofox_user_id,event_id").execute()
print(f"✅ 오늘+예정 예약 {len(att_rows)}건 반영 완료 ({datetime.now(KST).strftime('%H:%M')})")
# 갱신 완료 시각 기록 (앱 새로고침 폴링용)
try:
    sb.table("sync_state").upsert({"id":1,"synced_at":datetime.now(KST).isoformat()}).execute()
except Exception as e:
    print("sync_state 기록 실패:", str(e)[:80])
