# -*- coding: utf-8 -*-
"""
F45 성수 회원을 구글 연락처에 등급별로 동기화 — 이름 뒤에 상태 태그를 붙여서
연락처 목록만 스크롤해도 상태가 바로 보이게 만든다.

이름 형식 (재실행해도 태그가 계속 안 늘어나게, 매번 이전 태그를 지우고 새로 붙임):
  리드:        홍길동 [리드·가입7/24]
  트라이얼:    홍길동 [트라이얼·최근7/20]
  활성-기간권: 홍길동 [기간권3개월,7/29·최근7/20]   (7/29=만료일)
  활성-횟수권: 홍길동 [횟수권15회,8/15·최근7/20]     (8/15=만료일)
  활성-구독:   홍길동 [구독·최근7/20]

분류 기준:
  🟢 활성회원 그룹 (F45 활성회원) — stage=='회원' & payMember 통과
     - 유효 횟수권(pass_remaining 있음) → 횟수권 서브타입
     - membership에 '구독'/'Subscription' 포함 → 구독 서브타입
     - 그 외 → 기간권 서브타입
  🟡 리드/트라이얼 그룹 (F45 리드-트라이얼) — stage in ('리드','트라이얼') & 최근 LEAD_TRIAL_DAYS일 이내 가입
  ⚪ 저장 안 함 — 위 조건 다 해당 안 되거나 전화번호 없는 사람

준비물: client_secret.json (같은 폴더), 최초 실행시 브라우저 인증 → token.json 생성
환경변수: SUPABASE_URL, SUPABASE_KEY
실행: py -3.12 구글연락처_동기화_v3.py
"""
import os, re, json, time
from datetime import datetime
from supabase import create_client
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

LEAD_TRIAL_DAYS = 180
GROUP_ACTIVE = "F45 활성회원"
GROUP_LEAD = "F45 리드"
GROUP_TRIAL = "F45 트라이얼"
GROUP_EXPIRED = "F45 만료"
GROUP_MAP = {"ACTIVE": GROUP_ACTIVE, "LEAD": GROUP_LEAD, "TRIAL": GROUP_TRIAL, "EXPIRED": GROUP_EXPIRED}
LEADING_TAG_PATTERN = re.compile(r"^\[[A-Z]+\]\s*")       # 이름 앞의 [ACTIVE] 등 인식용
TRAILING_DETAIL_PATTERN = re.compile(r"\s*\([^()]*\)\s*$")  # 이름 뒤의 (상세정보) 인식용

SCOPES = ["https://www.googleapis.com/auth/contacts"]

def get_creds():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return creds

def norm_phone(p):
    d = re.sub(r"\D", "", str(p or ""))
    if d.startswith("82"): d = d[2:]
    if d.startswith("0"): d = d[1:]
    return d[-9:] if len(d) >= 9 else d

def md(date_str):
    """'2026-07-29' -> '7/29'"""
    if not date_str or len(date_str) < 10:
        return None
    try:
        d = datetime.fromisoformat(date_str[:10])
        return f"{d.month}/{d.day}"
    except Exception:
        return None

def pay_member(m):
    t = (m.get("membership") or "")
    if "무료혜택" in t or "Complimentary" in t:
        return False
    if "횟수권" in t or "Class Pass" in t:
        total = m.get("pass_total") or 0
        remain = m.get("pass_remaining")
        remain = remain if remain is not None else total
        return total >= 10 and remain > 0
    return True

EXPIRED_DAYS = 90  # 최근 90일 이내 만료된 사람까지만 EXPIRED로 동기화 (그 이전은 저장 안 함)

def classify_tier(m):
    """None(저장안함) / 'ACTIVE' / 'LEAD' / 'TRIAL' / 'EXPIRED' 반환"""
    if not (m.get("phone") or "").strip():
        return None
    stage = m.get("stage") or ""

    if stage == "회원" and pay_member(m):
        return "ACTIVE"

    if stage == "리드":
        jd = m.get("join_date")
        if jd:
            try:
                jd_date = datetime.fromisoformat(jd[:10])
                if (datetime.now() - jd_date).days <= LEAD_TRIAL_DAYS:
                    return "LEAD"
            except Exception:
                pass
        return None

    if stage == "트라이얼":
        jd = m.get("join_date")
        if jd:
            try:
                jd_date = datetime.fromisoformat(jd[:10])
                if (datetime.now() - jd_date).days <= LEAD_TRIAL_DAYS:
                    return "TRIAL"
            except Exception:
                pass
        return None

    if stage == "과거회원":
        ed = m.get("end_date")
        if ed:
            try:
                ed_date = datetime.fromisoformat(ed[:10])
                if (datetime.now() - ed_date).days <= EXPIRED_DAYS:
                    return "EXPIRED"
            except Exception:
                pass
        return None

    return None

def months_of(membership_text, join_date, end_date):
    m = re.search(r"(\d+)\s*개월", membership_text or "")
    if m:
        return m.group(1)
    if join_date and end_date and len(join_date) >= 10 and len(end_date) >= 10:
        try:
            j = datetime.fromisoformat(join_date[:10])
            e = datetime.fromisoformat(end_date[:10])
            n = round((e - j).days / 30)
            if n > 0:
                return str(n)
        except Exception:
            pass
    return "?"

def membership_detail(m, expired=False):
    """회원권 종류+기간/횟수 정보만 (기간권/횟수권/구독)"""
    membership = m.get("membership") or ""
    if m.get("pass_remaining") is not None and not expired:
        remain = m.get("pass_remaining")
        exp = md(m.get("pass_expiry")) or "?"
        return f"횟수권{remain}회,{exp}"
    if "구독" in membership or "Subscription" in membership:
        return "구독" + ("" if not expired else f",만료{md(m.get('end_date')) or '?'}")
    if expired:
        exp = md(m.get("end_date")) or "?"
        return f"기간권,만료{exp}"
    mon = months_of(membership, m.get("join_date"), m.get("end_date"))
    exp = md(m.get("end_date")) or "?"
    return f"기간권{mon}개월,{exp}"

def build_tag(tier, m, last_visit):
    """tier: 'ACTIVE'/'LEAD'/'TRIAL'/'EXPIRED' -> (prefix, detail) 튜플로 반환
    prefix는 이름 앞에, detail은 이름 뒤에 붙임: 'prefix 이름 detail'"""
    lv_txt = f"최근{md(last_visit)}" if last_visit else "최근없음"
    prefix = f"[{tier}]"

    if tier == "LEAD":
        jd_txt = md(m.get("join_date")) or "?"
        return prefix, f"(가입{jd_txt})"

    if tier == "TRIAL":
        return prefix, f"({lv_txt})"

    if tier == "EXPIRED":
        detail = membership_detail(m, expired=True)
        return prefix, f"({detail}·{lv_txt})"

    # ACTIVE
    detail = membership_detail(m, expired=False)
    return prefix, f"({detail}·{lv_txt})"

def clean_name(name):
    """기존 이름에서 앞의 [TIER] 접두사와 뒤의 (상세정보) 제거해서 순수 이름만 추출"""
    n = (name or "").strip()
    n = LEADING_TAG_PATTERN.sub("", n).strip()
    n = TRAILING_DETAIL_PATTERN.sub("", n).strip()
    return n

def status_note(m, last_visit):
    """메모란에도 동일 정보 보존 (검색/백업용)"""
    lv_txt = f"마지막방문 {last_visit[:10]}" if last_visit else "방문기록없음"
    return f"[F45] {m.get('stage','')} · {m.get('membership','') or ''} · {lv_txt}"

def add_to_group(service, group_res, contact_resource_name, retries=5):
    """연락처를 커스텀 그룹에 추가 — createContact는 커스텀 그룹을 직접 못 넣어서 별도 호출 필요"""
    for i in range(retries):
        try:
            service.contactGroups().members().modify(
                resourceName=group_res,
                body={"resourceNamesToAdd": [contact_resource_name]},
            ).execute()
            return
        except Exception as e:
            msg = str(e)
            is_retryable = ("429" in msg or "WinError" in msg or "ConnectionError" in msg
                            or "Unable to find the server" in msg or "timed out" in msg.lower())
            if is_retryable and i < retries - 1:
                time.sleep(3 * (i + 1))
                continue
            print(f"    그룹 추가 실패: {str(e)[:80]}")
            return

def api_call_with_retry(fn, retries=5):
    """429(속도제한)뿐 아니라 네트워크 순간 끊김(WinError, ConnectionError 등)도 재시도"""
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            msg = str(e)
            is_retryable = ("429" in msg or "WinError" in msg or "ConnectionError" in msg
                            or "Unable to find the server" in msg or "timed out" in msg.lower())
            if is_retryable and i < retries - 1:
                wait = 3 * (i + 1)
                print(f"    (네트워크 재시도 {i+1}/{retries}, {wait}초 대기...)")
                time.sleep(wait)
                continue
            raise

def ensure_group(service, cache, name):
    if name in cache:
        return cache[name]
    resp = service.contactGroups().list(pageSize=200).execute()
    for g in resp.get("contactGroups", []):
        if g.get("name") == name:
            cache[name] = g["resourceName"]
            return g["resourceName"]
    created = service.contactGroups().create(body={"contactGroup": {"name": name}}).execute()
    cache[name] = created["resourceName"]
    print(f"  그룹 생성: {name}")
    return cache[name]

def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    creds = get_creds()
    service = build("people", "v1", credentials=creds)

    print("구글 연락처 불러오는 중...")
    contacts_by_phone = {}
    page_token = None
    while True:
        resp = service.people().connections().list(
            resourceName="people/me", pageSize=1000,
            personFields="phoneNumbers,biographies,names,memberships",
            pageToken=page_token,
        ).execute()
        for p in resp.get("connections", []):
            for ph in p.get("phoneNumbers", []):
                key = norm_phone(ph.get("value"))
                if key:
                    contacts_by_phone[key] = p
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    print(f"기존 연락처 {len(contacts_by_phone)}건")

    groups_cache = {}
    group_res_map = {tier: ensure_group(service, groups_cache, name) for tier, name in GROUP_MAP.items()}

    # 회원 전체
    people, frm = [], 0
    while True:
        r = sb.table("people").select(
            "glofox_user_id,name,phone,stage,membership,end_date,join_date,pass_remaining,pass_total,pass_expiry"
        ).range(frm, frm + 999).execute()
        d = r.data or []
        people.extend(d)
        if len(d) < 1000: break
        frm += 1000
    print(f"Supabase 회원 {len(people)}명")

    # 마지막 방문일 계산 (attended=true 전체 조회, 클라이언트에서 max 계산)
    print("출석 기록 불러오는 중 (마지막 방문일 계산용)...")
    last_visit = {}
    frm = 0
    while True:
        r = sb.table("attendance").select("glofox_user_id,class_time").eq("attended", True).range(frm, frm + 999).execute()
        d = r.data or []
        for row in d:
            uid = row.get("glofox_user_id")
            ct = row.get("class_time")
            if uid and ct:
                if uid not in last_visit or ct > last_visit[uid]:
                    last_visit[uid] = ct
        if len(d) < 1000: break
        frm += 1000
    print(f"출석 있는 회원 {len(last_visit)}명")

    stats = {"active_updated": 0, "active_created": 0, "lead_updated": 0, "lead_created": 0, "skipped": 0}
    total_targets = sum(1 for m in people if classify_tier(m) is not None)
    print(f"처리 대상(활성+리드/트라이얼): {total_targets}명 · 예상 소요시간 약 {round(total_targets*0.6/60,1)}분\n")
    processed = 0

    for m in people:
        tier = classify_tier(m)
        if tier is None:
            stats["skipped"] += 1
            continue

        uid = m["glofox_user_id"]
        phone = m["phone"].strip()
        key = norm_phone(phone)
        group_res = group_res_map[tier]
        lv = last_visit.get(uid)
        tag_prefix, tag_detail = build_tag(tier, m, lv)
        base_name = m.get("name") or "이름없음"
        full_name = f"{tag_prefix} {base_name} {tag_detail}"
        note = status_note(m, lv)

        existing = contacts_by_phone.get(key)
        try:
            if existing:
                new_clean = clean_name(base_name)  # Supabase 이름 기준으로 항상 정규화
                api_call_with_retry(lambda: service.people().updateContact(
                    resourceName=existing["resourceName"],
                    updatePersonFields="names,biographies",
                    body={
                        "names": [{"givenName": f"{tag_prefix} {new_clean} {tag_detail}"}],
                        "biographies": [{"value": note, "contentType": "TEXT_PLAIN"}],
                        "etag": existing.get("etag"),
                    },
                ).execute())
                add_to_group(service, group_res, existing["resourceName"])
                stats[f"{tier}_updated"] += 1
            else:
                created = api_call_with_retry(lambda: service.people().createContact(body={
                    "names": [{"givenName": full_name}],
                    "phoneNumbers": [{"value": phone}],
                    "biographies": [{"value": note, "contentType": "TEXT_PLAIN"}],
                }).execute())
                add_to_group(service, group_res, created["resourceName"])
                stats[f"{tier}_created"] += 1
            time.sleep(0.3)  # 속도제한(429) 예방용 딜레이
            processed += 1
            if processed % 20 == 0:
                print(f"  ...진행중 {processed}/{total_targets}명 처리됨")
        except Exception as e:
            detail = getattr(e, "content", None)
            detail_txt = detail.decode("utf-8", "ignore")[:300] if detail else str(e)[:200]
            print(f"  실패({base_name}): {detail_txt}")
            time.sleep(1)

    print("\n✅ 완료")
    print(f"  활성회원: 갱신 {stats['active_updated']}건 · 신규생성 {stats['active_created']}건")
    print(f"  리드/트라이얼: 갱신 {stats['lead_updated']}건 · 신규생성 {stats['lead_created']}건")
    print(f"  저장 안 함(제외): {stats['skipped']}명")

if __name__ == "__main__":
    main()
