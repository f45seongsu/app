"""
F45 성수 · 전체 회원 정보 갱신 (하루 1회 / 수동)
────────────────────────────────────────────────────────────
글로폭스 /2.0/members + /2.0/credits 로 전체 회원을 다시 받아
Supabase people 의 '글로폭스 정보'만 최신화한다.
★ 사용자 입력(사진 photo_url·인스타 instagram·메모 care_memo/trial_memo·영상
  video_url·부상 injury_note 등)은 건드리지 않는다(업서트에 미포함).

환경변수: GLOFOX_API_KEY, GLOFOX_API_TOKEN, SUPABASE_URL, SUPABASE_KEY, (선택)ANTHROPIC_API_KEY
같은 폴더: name_cache.json
"""
import os, re, json, time, urllib.request
from datetime import datetime, timezone, timedelta
from supabase import create_client

BRANCH_ID="696094f2184b8f3da50206f9"; BASE="https://gf-api.aws.glofox.com/prod"
KST=timezone(timedelta(hours=9))
H={"x-glofox-branch-id":BRANCH_ID,"x-api-key":os.environ["GLOFOX_API_KEY"],
   "x-glofox-api-token":os.environ["GLOFOX_API_TOKEN"],"Accept":"application/json"}
sb=create_client(os.environ["SUPABASE_URL"],os.environ["SUPABASE_KEY"])
NC=json.load(open("name_cache.json",encoding="utf-8")) if os.path.exists("name_cache.json") else {}
SUR=set("김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽성차주우구민류진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제탁국어은편용봉빈사")

def api(url,tries=4):
    for t in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers=H),timeout=45) as r:
                return json.loads(r.read().decode("utf-8","replace"))
        except Exception as e:
            if t==tries-1: return None
            time.sleep(2)
    return None
def is_h(s): return bool(re.search(r"[가-힣]",str(s)))
def is_r(s): s=str(s).strip(); return bool(s) and bool(re.fullmatch(r"[A-Za-z .'-]+",s))
def clean_h(s):
    t=[x for x in re.split(r"\s+",str(s).strip()) if x]
    if len(t)==1: res=t[0]
    elif len(t)==2:
        a,b=t; both=(len(a)==1 and a in SUR)and(len(b)==1 and b in SUR)
        if len(a)==1 and a in SUR and not both: res=a+b
        elif len(b)==1 and b in SUR: res=b+a
        else: res=a+b
    else: res="".join(t)
    if len(res)>=3 and res[0]==res[1] and res[0] in SUR: res=res[1:]
    return res
def phone(p):
    d=re.sub(r"\D","",str(p or ""))
    if d.startswith("82"): d=d[2:]
    if d.startswith("0"): d=d[1:]
    if len(d)==8: d="10"+d
    if d.startswith("10") and len(d)==10: d="0"+d
    return f"{d[0:3]}-{d[3:7]}-{d[7:]}" if(len(d)==11 and d.startswith("010")) else ""
def edate(v):
    try:
        n=float(v)
        if n<=0: return ""
        if n>1e12: n/=1000
        return datetime.fromtimestamp(n,timezone.utc).astimezone(KST).strftime("%Y-%m-%d")
    except: return ""
def rawname(u):
    fn=str(u.get("first_name")or"").strip(); ln=str(u.get("last_name")or"").strip()
    return (fn+" "+ln).strip() or str(u.get("name")or"").strip()
def is_trial_mem(mn): mn=str(mn or""); return any(t in mn for t in ["Trial","체험","Legacy"])
def is_pass(mn): mn=str(mn or""); return ("횟수권" in mn)or("Class Pass" in mn)

# ── 전체 회원 수집 ──
users,page=[],1
while page<=80:
    r=api(f"{BASE}/2.0/members?active=any&page={page}&limit=100")
    if r is None: time.sleep(2); r=api(f"{BASE}/2.0/members?active=any&page={page}&limit=100")
    data=(r.get("data") if isinstance(r,dict) else r) if r else None
    if not data: break
    users.extend(data)
    if isinstance(r,dict) and r.get("has_more") is False: break
    page+=1
print(f"회원 {len(users)}명 수집")

# ── 병합 없음: 글로폭스 _id 마다 1행 (출석 uid와 정확히 매칭 → 미등록 방지) ──
persons={}
for u in users:
    uid=u.get("_id")
    if not uid: continue
    em=str(u.get("email")or"").strip().lower(); em=em if "@" in em else None
    persons[uid]={"glofox_user_id":uid,"u":u,"email":em,"phone":phone(u.get("phone"))}

today=datetime.now(KST).strftime("%Y-%m-%d")
def stage_of(u,mem):
    ls=(u.get("lead_status")or"").upper()
    mn=(mem.get("membership_name")or mem.get("plan_name")) if mem else None
    exp=edate(mem.get("expiry_date")) if mem else ""
    sub=(mem.get("subscription")or{}).get("auto_renewal") if mem else False
    active=(exp>=today if exp else False) or bool(sub)
    if active and mn and not is_trial_mem(mn): return "회원"
    if ls=="TRIAL": return "트라이얼"
    if ls in ("LEAD","WARM"): return "리드"
    if ls=="MEMBER" or (mem and exp): return "과거회원"
    return "기타"

# ── 횟수권 크레딧 (Class Pass 회원만) ──
def credits(uid):
    d=api(f"{BASE}/2.0/credits?user_id={uid}")
    packs=(d.get("data") if isinstance(d,dict) else d) or []
    total=avail=0; has=False; ends=[]; starts=[]
    for p in packs:
        if not p.get("active",True): continue
        total+=int(p.get("num_sessions")or 0)
        if p.get("available") is not None: has=True; avail+=int(p.get("available")or 0)
        if p.get("end_date"): ends.append(edate(p["end_date"]))
        if p.get("start_date"): starts.append(edate(p["start_date"]))
    remain=avail if has else total
    return {"pass_total":total,"pass_remaining":remain,"pass_used":max(total-remain,0),
            "pass_start":min(starts) if starts else "","pass_expiry":max(ends) if ends else ""}

# ── people 행 만들기 (글로폭스 정보만) ──
rows=[]; passcnt=0
for pid,p in persons.items():
    u=p["u"]; mem=u.get("membership") or {}
    nm=rawname(u); glo=str(u.get("name") or nm).strip()
    if is_h(nm): name=clean_h(nm)
    elif is_r(nm): name=NC.get(nm,nm)   # 캐시 없으면 원문 유지
    else: name=nm
    memname=mem.get("membership_name") or mem.get("plan_name") or ""
    row={"person_id":p["glofox_user_id"],"glofox_user_id":p["glofox_user_id"],"stage":stage_of(u,mem),
         "name":name,"glofox_name":glo,"phone":p["phone"] or "","email":p["email"] or "",
         "source":str((u.get("leads")or{}).get("contact_source") or u.get("source") or ""),
         "membership":memname,"end_date":edate(mem.get("expiry_date")),
         "join_date":edate(mem.get("start_date")),"birth":str(u.get("birth")or""),
         "gender":str(u.get("gender")or""),"glofox_photo":u.get("image_url") or ""}
    if is_pass(memname):
        row.update(credits(p["glofox_user_id"])); passcnt+=1
    rows.append(row)
print(f"정제 {len(rows)}명 · 횟수권 조회 {passcnt}명")

# ── 업서트 (photo_url·instagram·care_memo 등 사용자 입력은 미포함=보존) ──
ok=0
for i in range(0,len(rows),200):
    try:
        sb.table("people").upsert(rows[i:i+200],on_conflict="person_id").execute(); ok+=len(rows[i:i+200])
    except Exception as e:
        print("업서트 오류:",str(e)[:100])
print(f"✅ 전체 회원 갱신 완료: {ok}명 반영 ({datetime.now(KST).strftime('%H:%M')})")
