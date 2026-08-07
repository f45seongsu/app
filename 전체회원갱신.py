"""
F45 성수 · 전체 회원 정보 갱신 (하루 1회 / 수동)
────────────────────────────────────────────────────────────
글로폭스 /2.0/members + /2.0/credits 로 전체 회원을 다시 받아
Supabase people 의 '글로폭스 정보'만 최신화한다.
★ 사용자 입력(사진 photo_url·인스타 instagram·메모 care_memo/trial_memo·영상
  video_url·부상 injury_note 등)은 건드리지 않는다(업서트에 미포함).

[2026.08 수정 1차] 멤버십 오브젝트 없이 크레딧(횟수권)만 "Manually added"로
독립 구매한 회원의 경우, 기존 로직은 memname이 비어있어서(is_pass가 False)
credits() 조회 자체를 건너뛰어 pass_remaining/pass_total이 영구히 비어있는
버그가 있었음 (예: 박상준님).

[2026.08 수정 2차] 1차 수정만으로는 두 가지 문제가 더 있었음:
  (a) 성능 문제: memname이 빈 사람 = 대부분 "방문 이력 없는 순수 리드"인데
      이 사람들까지 전부 credits() API를 추가 호출하게 되면서 글로폭스
      초당 10건 제한에 걸려 실행시간이 10분+ 로 폭증함.
      → lead_status가 LEAD/WARM(순수 리드)인 사람은 애초에 크레딧을 살
        가능성이 극히 낮으므로 이 케이스는 제외하고, 그 외의 경우만
        (예: TRIAL 상태인데 실제로는 크레딧 구매한 이원용님 케이스) 조회함.
  (b) stage(회원단계) 문제: 크레딧을 채워넣어도 stage 값 자체는 그대로라
      "트라이얼 끝난 지 오래됐는데 계속 트라이얼로 표시" 되는 문제가 있었음
      (이원용님: 실제로는 유효한 횟수권 보유 중인데 stage='트라이얼'로 표시됨).
      → 유효한(만료 안 된) 크레딧이 발견되면 stage를 "회원"으로 강제 보정함.

환경변수: GLOFOX_API_KEY, GLOFOX_API_TOKEN, SUPABASE_URL, SUPABASE_KEY, (선택)ANTHROPIC_API_KEY
같은 폴더: name_cache.json
"""
import os, re, json, time, urllib.request
from datetime import datetime, timezone, timedelta
from supabase import create_client

BRANCH_ID = "696094f2184b8f3da50206f9"
BASE = "https://gf-api.aws.glofox.com/prod"
KST = timezone(timedelta(hours=9))
H = {
    "x-glofox-branch-id": BRANCH_ID,
    "x-api-key": os.environ["GLOFOX_API_KEY"],
    "x-glofox-api-token": os.environ["GLOFOX_API_TOKEN"],
    "Accept": "application/json",
}
sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
NC = json.load(open("name_cache.json", encoding="utf-8")) if os.path.exists("name_cache.json") else {}
SUR = set("김이박최정강조윤장임한오서신권황안송전홍유고문양손배백허남심노하곽성차주우구민류진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제탁국어은편용봉빈사")


def api(url, tries=4):
    for t in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=45) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except Exception:
            if t == tries - 1:
                return None
            time.sleep(2)
    return None


def is_h(s):
    return bool(re.search(r"[가-힣]", str(s)))


def is_r(s):
    s = str(s).strip()
    return bool(s) and bool(re.fullmatch(r"[A-Za-z .'-]+", s))


def clean_h(s):
    t = [x for x in re.split(r"\s+", str(s).strip()) if x]
    if len(t) == 1:
        res = t[0]
    elif len(t) == 2:
        a, b = t
        both = (len(a) == 1 and a in SUR) and (len(b) == 1 and b in SUR)
        if len(a) == 1 and a in SUR and not both:
            res = a + b
        elif len(b) == 1 and b in SUR:
            res = b + a
        else:
            res = a + b
    else:
        res = "".join(t)
    if len(res) >= 3 and res[0] == res[1] and res[0] in SUR:
        res = res[1:]
    return res


def phone(p):
    d = re.sub(r"\D", "", str(p or ""))
    if d.startswith("82"):
        d = d[2:]
    if d.startswith("0"):
        d = d[1:]
    if len(d) == 8:
        d = "10" + d
    if d.startswith("10") and len(d) == 10:
        d = "0" + d
    return f"{d[0:3]}-{d[3:7]}-{d[7:]}" if (len(d) == 11 and d.startswith("010")) else ""


def edate(v):
    try:
        n = float(v)
        if n <= 0:
            return ""
        if n > 1e12:
            n /= 1000
        return datetime.fromtimestamp(n, timezone.utc).astimezone(KST).strftime("%Y-%m-%d")
    except Exception:
        return ""


def rawname(u):
    fn = str(u.get("first_name") or "").strip()
    ln = str(u.get("last_name") or "").strip()
    return (fn + " " + ln).strip() or str(u.get("name") or "").strip()


def is_trial_mem(mn):
    mn = str(mn or "")
    return any(t in mn for t in ["Trial", "체험", "Legacy"])


def is_pass(mn):
    mn = str(mn or "")
    return ("횟수권" in mn) or ("Class Pass" in mn)


today = datetime.now(KST).strftime("%Y-%m-%d")


def stage_of(u, mem):
    ls = (u.get("lead_status") or "").upper()
    mn = (mem.get("membership_name") or mem.get("plan_name")) if mem else None
    st = (mem.get("status") or "").upper() if mem else ""
    if st in ("ACTIVE", "LOCKED", "PAUSED") and mn and not is_trial_mem(mn):
        return "회원"
    if st in ("EXPIRED", "CANCELLED"):
        return "과거회원"
    if st == "FUTURE":
        return "과거회원"
    exp = edate(mem.get("expiry_date")) if mem else ""
    sub = (mem.get("subscription") or {}).get("auto_renewal") if mem else False
    active = (exp >= today if exp else False) or bool(sub)
    if active and mn and not is_trial_mem(mn):
        return "회원"
    if ls == "TRIAL":
        return "트라이얼"
    if ls in ("LEAD", "WARM"):
        return "리드"
    if ls == "MEMBER" or (mem and exp):
        return "과거회원"
    return "기타"


def credits(uid):
    d = api(f"{BASE}/2.0/credits?user_id={uid}")
    packs = (d.get("data") if isinstance(d, dict) else d) or []
    total = avail = 0
    has = False
    ends = []
    starts = []
    for p in packs:
        if not p.get("active", True):
            continue
        total += int(p.get("num_sessions") or 0)
        if p.get("available") is not None:
            has = True
            avail += int(p.get("available") or 0)
        if p.get("end_date"):
            ends.append(edate(p["end_date"]))
        if p.get("start_date"):
            starts.append(edate(p["start_date"]))
    remain = avail if has else total
    return {
        "pass_total": total,
        "pass_remaining": remain,
        "pass_used": max(total - remain, 0),
        "pass_start": min(starts) if starts else "",
        "pass_expiry": max(ends) if ends else "",
    }


def apply_credit_fallback(row, u, memname, uid):
    """멤버십명이 '횟수권'류면 항상 조회하고, 멤버십 자체가 없는 경우엔
    '순수 리드(LEAD/WARM)'만 아니면(=혹시 크레딧이 있을 가능성이 있으면) 조회한다.
    조회해서 실제 유효 크레딧이 있으면 membership/stage까지 정확히 보정한다."""
    ls = (u.get("lead_status") or "").upper()
    should_check = is_pass(memname) or (not memname and ls not in ("LEAD", "WARM"))
    if not should_check:
        return False
    c = credits(uid)
    if c["pass_total"] <= 0:
        return False
    row.update(c)
    if not memname:
        row["membership"] = "횟수권"
    # 유효한(만료 안 지난) 크레딧이 있으면, 실제로는 활성회원인데 멤버십 객체가
    # 없어서 리드/트라이얼로 잘못 분류됐던 stage를 "회원"으로 바로잡음
    if not c["pass_expiry"] or c["pass_expiry"] >= today:
        row["stage"] = "회원"
    return True


# ── 전체 회원 수집 ──
users, page = [], 1
while page <= 80:
    r = api(f"{BASE}/2.0/members?active=any&page={page}&limit=100")
    if r is None:
        time.sleep(2)
        r = api(f"{BASE}/2.0/members?active=any&page={page}&limit=100")
    data = (r.get("data") if isinstance(r, dict) else r) if r else None
    if not data:
        break
    users.extend(data)
    if isinstance(r, dict) and r.get("has_more") is False:
        break
    page += 1
print(f"회원 {len(users)}명 수집")

persons = {}
for u in users:
    uid = u.get("_id")
    if not uid:
        continue
    em = str(u.get("email") or "").strip().lower()
    em = em if "@" in em else None
    persons[uid] = {"glofox_user_id": uid, "u": u, "email": em, "phone": phone(u.get("phone"))}

rows = []
passcnt = 0
for pid, p in persons.items():
    u = p["u"]
    mem = u.get("membership") or {}
    nm = rawname(u)
    glo = str(u.get("name") or nm).strip()
    if is_h(nm):
        name = clean_h(nm)
    elif is_r(nm):
        name = NC.get(nm, nm)
    else:
        name = nm
    memname = mem.get("membership_name") or mem.get("plan_name") or ""
    row = {
        "person_id": p["glofox_user_id"],
        "glofox_user_id": p["glofox_user_id"],
        "stage": stage_of(u, mem),
        "name": name,
        "glofox_name": glo,
        "phone": p["phone"] or "",
        "email": p["email"] or "",
        "source": str((u.get("leads") or {}).get("contact_source") or u.get("source") or ""),
        "membership": memname,
        "end_date": edate(mem.get("expiry_date")),
        "join_date": edate(mem.get("start_date")),
        "birth": str(u.get("birth") or ""),
        "gender": str(u.get("gender") or ""),
        "glofox_photo": u.get("image_url") or "",
    }
    if apply_credit_fallback(row, u, memname, p["glofox_user_id"]):
        passcnt += 1
    rows.append(row)
print(f"정제 {len(rows)}명 · 횟수권 조회 {passcnt}명")

try:
    ov = {}
    frm = 0
    while True:
        r = sb.table("people").select("person_id,name_override").range(frm, frm + 999).execute()
        d = r.data or []
        for x in d:
            if x.get("name_override"):
                ov[x["person_id"]] = x["name_override"]
        if len(d) < 1000:
            break
        frm += 1000
    for row in rows:
        if row["person_id"] in ov:
            row["name"] = ov[row["person_id"]]
    print(f"수동 이름 보존: {len(ov)}명")
except Exception as e:
    print("name_override 조회 실패(무시):", str(e)[:80])

ok = 0
for i in range(0, len(rows), 200):
    try:
        sb.table("people").upsert(rows[i:i + 200], on_conflict="person_id").execute()
        ok += len(rows[i:i + 200])
    except Exception as e:
        print("업서트 오류:", str(e)[:100])
print(f"✅ 전체 회원 갱신 완료: {ok}명 반영 ({datetime.now(KST).strftime('%H:%M')})")

present = set(persons.keys())
att_uids = set()
frm = 0
while frm <= 6000:
    r = sb.table("attendance").select("glofox_user_id").range(frm, frm + 999).execute()
    d = r.data or []
    for a in d:
        u = a.get("glofox_user_id")
        if u:
            att_uids.add(u)
    if len(d) < 1000:
        break
    frm += 1000
missing = [u for u in att_uids if u not in present]
print(f"출석 있는데 회원목록에 없는 uid: {len(missing)}명 → 개별 보충")

stub = []
for uid in missing:
    u = api(f"{BASE}/2.0/members/{uid}")
    if isinstance(u, dict) and "data" in u:
        u = u["data"]
    if not isinstance(u, dict) or not u.get("_id"):
        continue
    mem = u.get("membership") or {}
    nm = rawname(u)
    glo = str(u.get("name") or nm).strip()
    if is_h(nm):
        name = clean_h(nm)
    elif is_r(nm):
        name = NC.get(nm, nm)
    else:
        name = nm
    memname = mem.get("membership_name") or mem.get("plan_name") or ""
    row = {
        "person_id": uid,
        "glofox_user_id": uid,
        "stage": stage_of(u, mem),
        "name": name or glo,
        "glofox_name": glo,
        "phone": phone(u.get("phone")),
        "email": str(u.get("email") or "").lower(),
        "source": str((u.get("leads") or {}).get("contact_source") or u.get("source") or ""),
        "membership": memname,
        "end_date": edate(mem.get("expiry_date")),
        "join_date": edate(mem.get("start_date")),
        "birth": str(u.get("birth") or ""),
        "gender": str(u.get("gender") or ""),
        "glofox_photo": u.get("image_url") or "",
    }
    apply_credit_fallback(row, u, memname, uid)
    stub.append(row)

if stub:
    for i in range(0, len(stub), 200):
        try:
            sb.table("people").upsert(stub[i:i + 200], on_conflict="person_id").execute()
        except Exception as e:
            print("보충 업서트 오류:", str(e)[:80])
    print(f"✅ 미등록 보충 완료: {len(stub)}명 추가")
