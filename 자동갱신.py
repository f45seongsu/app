"""
F45 성수 · 오늘 예약 자동 갱신 (가벼움, 5분마다)
────────────────────────────────────────────────────────────
오늘 수업의 예약/출석만 글로폭스에서 받아 Supabase attendance에 반영.
글로폭스 호출 ~15회 (가벼움). 전체 회원/횟수권은 별도 하루 1회 갱신.

환경변수(깃허브 Secrets): GLOFOX_API_KEY, GLOFOX_API_TOKEN, SUPABASE_URL, SUPABASE_KEY
같은 폴더: name_cache.json
"""
import os, re, json, urllib.request, urllib.error
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

def api(url, tries=3):
    for t in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:
            if t == tries-1: print("API err:", str(e)[:80]); return {}
    return {}
def items(d):
    if isinstance(d, list): return d
    if isinstance(d, dict):
        for k in ("data","events","bookings"):
            if isinstance(d.get(k), list): return d[k]
    return []
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
def epoch_kst(v):
    try:
        n=float(v)
        if n>1e12: n/=1000
        return datetime.fromtimestamp(n, timezone.utc).astimezone(KST)
    except: return None

now = datetime.now(KST); today = now.date()
d0 = datetime(today.year, today.month, today.day, tzinfo=KST)
start = (d0 - timedelta(hours=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
finish = (d0 + timedelta(days=1)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

evs = items(api(f"{BASE}/2.0/branches/{BRANCH_ID}/events?start-time={start}&finish-time={finish}"))
evs = [e for e in evs if (epoch_kst(e.get("time_start")) or now).date() == today]
print(f"오늘 수업 {len(evs)}개")

att_rows = []; new_people = []
for e in evs:
    eid = e.get("_id")
    bs = items(api(f"{BASE}/2.2/branches/{BRANCH_ID}/bookings?event_id={eid}&limit=200"))
    for b in bs:
        if str(b.get("event_id")) != eid: continue
        uid = str(b.get("user_id","")); 
        if not uid: continue
        ts = str(b.get("time_start") or "")[:19]   # 이미 KST 벽시계
        att_rows.append({
            "glofox_user_id": uid, "event_id": eid,
            "class_name": b.get("event_name") or "수업",
            "class_time": ts if len(ts) >= 16 else None,
            "attended": bool(b.get("attended")),
            "status": b.get("status",""),
        })
        # 명단에 없을 수 있는 신규 예약자 → 최소 정보만 (기존은 안 건드림)
        new_people.append({"person_id": uid, "glofox_user_id": uid,
                           "name": clean(b.get("user_name","")), "stage": "트라이얼"})

# 업서트
if new_people:
    # 이미 있으면 무시(덮어쓰지 않음), 없으면 추가
    try: sb.table("people").upsert(new_people, on_conflict="person_id", ignore_duplicates=True).execute()
    except Exception as ex: print("people stub 스킵:", str(ex)[:80])
if att_rows:
    for i in range(0, len(att_rows), 500):
        sb.table("attendance").upsert(att_rows[i:i+500], on_conflict="glofox_user_id,event_id").execute()
print(f"✅ 오늘 예약 {len(att_rows)}건 반영 완료 ({now.strftime('%H:%M')})")
