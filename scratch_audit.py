"""Adversarial audit of auth + every endpoint against the business rules.
Prints PASS/FAIL per check. Run against a live server on 127.0.0.1:8012.
"""
import httpx, jwt
from datetime import datetime, timedelta, timezone

B = "http://127.0.0.1:8012"
SECRET = "test-secret"
fails = []
def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond: fails.append(name)

def reg(o, u, p="pw12345"):
    return httpx.post(f"{B}/auth/register", json={"org_name": o, "username": u, "password": p})
def login(o, u, p="pw12345"):
    return httpx.post(f"{B}/auth/login", json={"org_name": o, "username": u, "password": p})
def H(tok): return {"Authorization": f"Bearer {tok}"}
def fut(h):
    return (datetime.now(timezone.utc)+timedelta(hours=h)).replace(minute=0, second=0, microsecond=0).isoformat()

# ---------- REGISTER / ROLES (Rule 15) ----------
r = reg("orgA", "adminA"); check("register new org -> admin", r.status_code==201 and r.json()["role"]=="admin", r.status_code)
r = reg("orgA", "memberA"); check("register known org -> member", r.status_code==201 and r.json()["role"]=="member")
r = reg("orgA", "adminA"); check("dup username -> 409 USERNAME_TAKEN", r.status_code==409 and r.json().get("code")=="USERNAME_TAKEN", r.status_code)

# ---------- LOGIN (Rule contract) ----------
r = login("orgA", "adminA"); check("login ok -> 200 + tokens", r.status_code==200 and "access_token" in r.json())
adminA = r.json()
r = login("orgA", "adminA", "wrong"); check("bad password -> 401 INVALID_CREDENTIALS", r.status_code==401 and r.json().get("code")=="INVALID_CREDENTIALS", r.status_code)
r = login("nope", "x"); check("unknown org -> 401 INVALID_CREDENTIALS", r.status_code==401 and r.json().get("code")=="INVALID_CREDENTIALS")

# ---------- JWT CLAIMS (Rule 8) ----------
at = jwt.decode(adminA["access_token"], SECRET, algorithms=["HS256"])
rt = jwt.decode(adminA["refresh_token"], SECRET, algorithms=["HS256"])
check("access claims present", all(k in at for k in ["sub","org","role","jti","iat","exp","type"]), sorted(at))
check("access exp-iat == 900", at["exp"]-at["iat"]==900, at["exp"]-at["iat"])
check("access type == access", at["type"]=="access")
check("sub is string", isinstance(at["sub"], str))
check("refresh exp-iat == 7 days", rt["exp"]-rt["iat"]==7*24*3600, rt["exp"]-rt["iat"])
check("refresh type == refresh", rt["type"]=="refresh")
check("access/refresh distinct jti", at["jti"]!=rt["jti"])

# ---------- AUTH ENFORCEMENT ----------
check("no token -> 401", httpx.get(f"{B}/rooms").status_code==401)
check("garbage token -> 401", httpx.get(f"{B}/rooms", headers=H("garbage")).status_code==401)
check("refresh token used as access -> 401", httpx.get(f"{B}/rooms", headers=H(adminA["refresh_token"])).status_code==401)
# expired access token
exp_tok = jwt.encode({"sub":at["sub"],"org":at["org"],"role":"admin","jti":"x","iat":at["iat"]-1000,"exp":at["iat"]-100,"type":"access"}, SECRET, algorithm="HS256")
check("expired access -> 401", httpx.get(f"{B}/rooms", headers=H(exp_tok)).status_code==401)

# ---------- LOGOUT (Rule 8) ----------
r2 = login("orgA","adminA").json(); tok = r2["access_token"]
check("pre-logout /rooms 200", httpx.get(f"{B}/rooms", headers=H(tok)).status_code==200)
check("logout 200", httpx.post(f"{B}/auth/logout", headers=H(tok)).status_code==200)
check("post-logout token -> 401", httpx.get(f"{B}/rooms", headers=H(tok)).status_code==401)

# ---------- REFRESH single-use (Rule 8) ----------
r3 = login("orgA","adminA").json(); oldrt = r3["refresh_token"]
r4 = httpx.post(f"{B}/auth/refresh", json={"refresh_token": oldrt})
check("refresh rotates -> 200", r4.status_code==200 and "access_token" in r4.json())
newrt = r4.json()["refresh_token"]
check("reuse old refresh -> 401", httpx.post(f"{B}/auth/refresh", json={"refresh_token": oldrt}).status_code==401)
check("new refresh works -> 200", httpx.post(f"{B}/auth/refresh", json={"refresh_token": newrt}).status_code==200)
check("access token as refresh -> 401", httpx.post(f"{B}/auth/refresh", json={"refresh_token": r3["access_token"]}).status_code==401)

# ---------- ADMIN-ONLY (Rule contract) ----------
memTok = login("orgA","memberA").json()["access_token"]
admTok = login("orgA","adminA").json()["access_token"]
check("member POST /rooms -> 403 FORBIDDEN", httpx.post(f"{B}/rooms", json={"name":"x","capacity":2,"hourly_rate_cents":100}, headers=H(memTok)).status_code==403)
rroom = httpx.post(f"{B}/rooms", json={"name":"R","capacity":4,"hourly_rate_cents":1000}, headers=H(admTok))
check("admin POST /rooms -> 201", rroom.status_code==201)
rid = rroom.json()["id"]
check("member GET /rooms -> 200", httpx.get(f"{B}/rooms", headers=H(memTok)).status_code==200)
check("member GET usage-report -> 403", httpx.get(f"{B}/admin/usage-report?from=2026-01-01&to=2026-12-31", headers=H(memTok)).status_code==403)
check("member GET export -> 403", httpx.get(f"{B}/admin/export", headers=H(memTok)).status_code==403)

# ---------- MULTI-TENANCY (Rule 9) ----------
reg("orgB","adminB"); bTok = login("orgB","adminB").json()["access_token"]
check("orgB sees 0 of orgA rooms", all(r["id"]!=rid for r in httpx.get(f"{B}/rooms", headers=H(bTok)).json()))
check("orgB availability on orgA room -> 404 ROOM_NOT_FOUND", httpx.get(f"{B}/rooms/{rid}/availability?date=2026-07-10", headers=H(bTok)).status_code==404)
check("orgB stats on orgA room -> 404", httpx.get(f"{B}/rooms/{rid}/stats", headers=H(bTok)).status_code==404)
check("orgB booking on orgA room -> 404 ROOM_NOT_FOUND", httpx.post(f"{B}/bookings", json={"room_id":rid,"start_time":fut(50),"end_time":fut(51)}, headers=H(bTok)).status_code==404)

# ---------- BOOKING VISIBILITY (Rule 10) ----------
# memberA books, another member (memberA2) and admin try to read/cancel
reg("orgA","memberA2")
mA = login("orgA","memberA").json()["access_token"]
mA2 = login("orgA","memberA2").json()["access_token"]
bk = httpx.post(f"{B}/bookings", json={"room_id":rid,"start_time":fut(60),"end_time":fut(61)}, headers=H(mA))
check("memberA create booking -> 201", bk.status_code==201, bk.text[:120])
bid = bk.json().get("id")
check("owner GET own booking -> 200", httpx.get(f"{B}/bookings/{bid}", headers=H(mA)).status_code==200)
check("other member GET booking -> 404 (Rule 10)", httpx.get(f"{B}/bookings/{bid}", headers=H(mA2)).status_code==404)
check("admin GET member booking -> 200 (Rule 10)", httpx.get(f"{B}/bookings/{bid}", headers=H(admTok)).status_code==200)
check("other member CANCEL booking -> 404", httpx.post(f"{B}/bookings/{bid}/cancel", headers=H(mA2)).status_code==404)
check("orgB GET orgA booking -> 404", httpx.get(f"{B}/bookings/{bid}", headers=H(bTok)).status_code==404)

# ---------- BOOKING WINDOW (Rule 2) ----------
check("past start -> 400", httpx.post(f"{B}/bookings", json={"room_id":rid,"start_time":fut(-5),"end_time":fut(1)}, headers=H(admTok)).status_code==400)
check("end==start -> 400", httpx.post(f"{B}/bookings", json={"room_id":rid,"start_time":fut(70),"end_time":fut(70)}, headers=H(admTok)).status_code==400)
check("9h duration -> 400", httpx.post(f"{B}/bookings", json={"room_id":rid,"start_time":fut(70),"end_time":fut(79)}, headers=H(admTok)).status_code==400)
check("1.5h duration -> 400", httpx.post(f"{B}/bookings", json={"room_id":rid,"start_time":fut(70)[:-6]+"+00:00","end_time":(datetime.now(timezone.utc)+timedelta(hours=71,minutes=30)).replace(second=0,microsecond=0).isoformat()}, headers=H(admTok)).status_code==400)

# ---------- CANCEL REFUND (Rule 6) exactly-24h boundary ----------
# start exactly 24h from now -> should be 50%
b24 = httpx.post(f"{B}/bookings", json={"room_id":rid,"start_time":fut(24),"end_time":fut(25)}, headers=H(admTok))
if b24.status_code==201:
    c24 = httpx.post(f"{B}/bookings/{b24.json()['id']}/cancel", headers=H(admTok)).json()
    check("cancel exactly ~24h notice -> 50%", c24.get("refund_percent")==50, c24)

# ---------- 404 for unknown ids ----------
check("GET unknown booking -> 404 BOOKING_NOT_FOUND", httpx.get(f"{B}/bookings/999999", headers=H(admTok)).status_code==404)
check("cancel unknown booking -> 404", httpx.post(f"{B}/bookings/999999/cancel", headers=H(admTok)).status_code==404)

print("\n==== FAILURES:", fails if fails else "NONE")
