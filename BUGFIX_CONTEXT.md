# CoWork Bug-Fix — Session Context File

> Portable context for resuming this bug-fix work in another Claude/LLM session.
> Keep this file updated as bugs get fixed. Last updated: 2026-07-09.

## Project

CoWork = FastAPI + SQLAlchemy + SQLite coworking-room booking API. Multi-tenant
(orgs, admins, members). This is a **bug-fix hackathon**: a broken codebase with
bugs across easy/medium/concurrency tiers. Grading is **black-box** — a grader
builds the Docker container and asserts behavior against the business rules in
`README.md` / `ICT_Fest_Hackathon_Preliminary.pdf`. **Must preserve the API
contract exactly** (paths, status codes, error codes, JSON field names, JWT
claims). Do NOT refactor unrelated code — only fix broken code.

## Environment / how to run (local, no Docker)

- System Python 3.12.3 (project targets 3.11; Docker uses 3.11-slim for grading).
- venv at `.venv/`. Deps: `requirements.txt` + `pytest` + `httpx` (last two NOT in
  requirements.txt but needed for the smoke test / TestClient).

Run the server (port 8000, auto-reload):
```bash
cd "/home/akib/Desktop/iut hackathon/ICT_Fest_Hackathon_Preliminary"
JWT_SECRET=test-secret .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```
- API: http://localhost:8000  | Swagger UI: http://localhost:8000/docs
- `http://localhost:8000/` (root) intentionally has NO route → blank/404 is normal.
  Use `/docs` or `/health`.

Run smoke test:
```bash
JWT_SECRET=test-secret .venv/bin/python -m pytest -q
```
Reset DB: `rm -f cowork.db` then restart. Stop server: `pkill -f "uvicorn app.main:app"`.

Grader-faithful run: `docker compose up --build`.

## File map

```
app/main.py            app wiring
app/config.py          env config (token lifetimes, DB url)
app/database.py        engine + session
app/models.py          ORM: Organization, User, Room, Booking, RefundLog
app/schemas.py         pydantic request models
app/serializers.py     serialize_booking
app/timeutils.py       parse_input_datetime / iso_utc
app/auth.py            password hash, JWT create/decode, dependencies, revocation
app/cache.py           in-memory report + availability caches
app/errors.py          AppError + handler
app/routers/auth.py    register / login / refresh / logout
app/routers/rooms.py   rooms list/create, availability, stats
app/routers/bookings.py create / list / detail / cancel
app/routers/admin.py   usage-report / export
app/services/reference.py    reference codes (counter)
app/services/ratelimit.py    per-user rolling rate limit
app/services/stats.py        in-memory per-room stats
app/services/refunds.py      log_refund
app/services/export.py       CSV export
app/services/notifications.py notify_created / notify_cancelled (locks)
```

---

## BUG LIST (status: [ ] todo, [x] fixed)

### Tier 1 — easy logic / one-liners

- [x] **B1 — Access token lifetime wrong.** `app/auth.py:50`
  `lifetime = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES * 60)`. With
  `ACCESS_TOKEN_EXPIRE_MINUTES = 15` this is 900 *minutes* = 54000s.
  Rule 8: access `exp − iat` must equal exactly **900 seconds**.
  **Fix:** `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)`.
  **Expect:** decode access token → `exp - iat == 900`.

- [x] **B2 — Logout does not invalidate the token.** `app/auth.py:97` vs `:86`.
  `revoke_access_token` stores `payload["jti"]` but `get_token_payload` checks
  `if payload.get("sub") in _revoked_tokens`. Mismatched claim → logout is a no-op
  (and if it "worked" it'd wrongly nuke every token for the user).
  Rule 8: logout immediately invalidates the presented access token.
  **Fix:** line 97 → `if payload.get("jti") in _revoked_tokens:`.
  **Expect:** call `/auth/logout`, then reuse same access token → **401**.

- [x] **B3 — Duplicate username not rejected.** `app/routers/auth.py:37-43`.
  On existing user it returns the existing user instead of erroring.
  Rule 15: duplicate username within org → **409 USERNAME_TAKEN**.
  **Fix:** replace the `return {...existing...}` block with
  `raise AppError(409, "USERNAME_TAKEN", "Username already taken")`.
  **Expect:** register same org_name+username twice → 2nd → 409 `{code:"USERNAME_TAKEN"}`.

- [x] **B5 — Overlap check rejects back-to-back.** `app/routers/bookings.py:50`.
  `if b.start_time <= end and start <= b.end_time:` uses `<=`.
  Rule 3: overlap iff `existing.start < new.end AND new.start < existing.end`;
  back-to-back (one ends exactly when next starts) is ALLOWED.
  **Fix:** `if b.start_time < end and start < b.end_time:`.
  **Expect:** new.start == existing.end → 201; real overlap → 409 ROOM_CONFLICT.

- [x] **B6 — 5-minute grace window on past bookings.** `app/routers/bookings.py:86`.
  `if start <= now - timedelta(seconds=300):` allows starts up to 5 min in the past.
  Rule 2: start must be **strictly** in the future — no grace window.
  **Fix:** `if start <= now:`.
  **Expect:** start in past/now → 400 INVALID_BOOKING_WINDOW; strictly future → ok.

- [x] **B7 — Missing min-duration / end>start check.** `app/routers/bookings.py:93`.
  Only checks `duration_hours > MAX`. A 0-hour booking (end==start) → duration 0 →
  passes. Rule 2: duration whole, **min 1**, max 8; end strictly after start.
  **Fix:** `if duration_hours < MIN_DURATION_HOURS or duration_hours > MAX_DURATION_HOURS:`.
  **Expect:** end<=start or duration 0/>8 → 400; 1..8 → ok.

- [x] **B9 — Booking detail overwrites start_time with created_at.**
  `app/routers/bookings.py:166`: `response["start_time"] = iso_utc(booking.created_at)`.
  Corrupts the `start_time` field. **Fix:** delete that line (serialize_booking already
  sets correct start_time). **Expect:** GET `/bookings/{id}` shows real start_time + `refunds`.

- [x] **B10 — Refund tiers wrong at boundaries.** `app/routers/bookings.py:200-206`.
  `if notice_hours > 48` should be `>= 48`; the `else` returns `50` but must be `0`.
  Rule 6: ≥48h→100, 24..<48→50, <24→**0**.
  **Fix:**
  ```python
  if notice >= timedelta(hours=48):
      refund_percent = 100
  elif notice >= timedelta(hours=24):
      refund_percent = 50
  else:
      refund_percent = 0
  ```
  **Expect:** notice exactly 48h → 100%; <24h → 0%.

- [x] **B14 — Offset datetimes not converted to UTC.** `app/timeutils.py:12-13`.
  `dt.replace(tzinfo=None)` drops the offset instead of converting. `10:00+06:00`
  is stored as `10:00` instead of `04:00Z`. Rule 1: offset inputs converted to UTC.
  **Fix:** `dt = dt.astimezone(timezone.utc).replace(tzinfo=None)`.
  **Expect:** input `...T10:00:00+06:00` → stored/returned `04:00:00Z`.

- [x] **B19 — Export cross-tenant leak.** `app/services/export.py:48-51`.
  `include_all=True` + `room_id` → `fetch_bookings_raw(db, room_id)` which does NOT
  filter by org. Admin can read another org's room bookings. Rule 9: multi-tenancy.
  **Fix:** `rows = _fetch_scoped(db, org_id, None, room_id)`.
  **Expect:** export with foreign room_id → only own-org data (no leak).

### Tier 2 — medium

- [x] **B4 — Refresh tokens not single-use.** `app/routers/auth.py:81-93`.
  `refresh` issues new tokens but never invalidates the presented refresh token, so
  it can be reused. Rule 8: refresh is single-use; reuse → 401.
  **Fix:** track used refresh `jti`s (e.g. add `_used_refresh_tokens: set` + helpers
  in `auth.py`); in `refresh`: if `data["jti"]` already used → 401, else mark used.
  **Expect:** first refresh rotates tokens; reusing same refresh token → 401.

- [x] **B8 — List bookings: wrong order + pagination.** `app/routers/bookings.py:136-140`.
  `order_by(start_time.desc()...)` (should be asc), `.offset(page*limit)` (should be
  `(page-1)*limit`), `.limit(10)` hardcoded (should be `limit`). Rule 11.
  **Fix:**
  ```python
  base.order_by(Booking.start_time.asc(), Booking.id.asc())
      .offset((page - 1) * limit)
      .limit(limit)
  ```
  **Expect:** asc by start_time (ties by id); page1 limit10 → items[0:10]; page2 → [10:20];
  no skips/repeats.

- [x] **B11 — Refund rounding wrong + response≠ledger.**
  `app/services/refunds.py:17` uses `int(refund_dollars*100)` (truncates);
  `app/routers/bookings.py:208` uses `round(...)` (banker's). Rule 6: nearest cent,
  half-cents **round up**; and cancel response must equal RefundLog amount.
  **Fix:** half-up integer formula in ONE place, reuse for both:
  - `refunds.py`: `amount_cents = (booking.price_cents * percent + 50) // 100`.
  - `bookings.py`: `entry = log_refund(db, booking, refund_percent);
     refund_amount_cents = entry.amount_cents` (delete the separate `round()` line 208).
  **Expect:** 50% of 1001 → 501; response.refund_amount_cents == RefundLog.amount_cents.

- [x] **B12 — Create doesn't invalidate usage-report cache.**
  `app/routers/bookings.py` create path (~line 121). New confirmed booking won't
  appear in an already-cached usage report. Rule 12: reflect current state immediately.
  **Fix:** add `cache.invalidate_report(user.org_id)` after commit in create_booking.
  **Expect:** create booking in range → usage-report immediately includes it.

- [x] **B13 — Cancel doesn't invalidate availability cache.**
  `app/routers/bookings.py` cancel path (~line 217). Cancelled booking still shows as
  busy in cached availability. Rule 13.
  **Fix:** add
  `cache.invalidate_availability(booking.room_id, booking.start_time.date().isoformat())`
  in cancel_booking. **Expect:** cancel → availability no longer lists that interval.

### Tier 3 — concurrency (Rules 3,4,5,7,14,16; "holds under concurrent requests")

> All caused by unsynchronized read-modify-write; the `time.sleep()` "pause" helpers
> are deliberately placed to widen the race windows. Fix by adding locking (single
> process / single container, so `threading.Lock` suffices), not by removing sleeps.

- [x] **B1- [ ] **B15X — Duplicate reference codes.** `app/services/reference.py`.
  `next_reference_code` reads counter, sleeps, then increments → concurrent callers get
  the same code. Rule 7: unique even under concurrency.
  **Fix:** guard read+increment with a module `threading.Lock()`.
  **Expect:** N concurrent bookings → N distinct reference_codes.

- [x] **B1- [ ] **B16X — Rate limit not enforced under concurrency.** `app/services/ratelimit.py`.
  Trim/append/store not atomic → lost updates let >20 through. Rule 5.
  **Fix:** wrap the critical section in a `threading.Lock()`.
  **Expect:** 21st request within 60s → 429, even under bursts.

- [x] **B1- [ ] **B17X — Stats drift under concurrency.** `app/services/stats.py`.
  `record_create`/`record_cancel` read-modify-write with a sleep between → lost updates.
  Rule 14: stats always equal DB-derived values.
  **Fix:** guard both with a `threading.Lock()`.
  **Expect:** after concurrent create/cancel bursts, stats == DB counts/revenue.

- [x] **B1- [ ] **B18X — Deadlock between create and cancel notifications.**
  `app/services/notifications.py`. `notify_created` locks email→audit;
  `notify_cancelled` locks audit→email → opposite ordering → deadlock. Rule 16: liveness.
  **Fix:** make both acquire in the SAME order (email→audit). Rewrite `notify_cancelled`:
  ```python
  with _email_lock:
      _send_email("cancelled", booking)
      with _audit_lock:
          _write_audit("cancelled", booking)
  ```
  **Expect:** concurrent create+cancel never hang.

- [x] **B20/B21 — Double-booking & quota bypass under concurrency.**
  `app/routers/bookings.py` create: conflict check + quota check + insert are not atomic
  (sleeps widen the gap) → two concurrent requests both pass → double-booked / >3 quota.
  Rules 3 & 4.
  **Fix:** hold a module `threading.Lock()` around the region from `_has_conflict`
  through `db.commit()` (re-checking inside the lock).
  **Expect:** exactly one of two conflicting concurrent bookings succeeds; quota never >3.

- [x] **B22 — Double refund under concurrent cancels.** `app/routers/bookings.py` cancel.
  Two concurrent cancels of the same booking can both pass the `status=="cancelled"`
  check → two RefundLog entries / double refund. Rule 6: exactly one RefundLog entry;
  holds under concurrent cancels.
  **Fix:** serialize the cancel check→log→commit (lock; re-read status inside).
  **Expect:** one cancel succeeds (one RefundLog), the other → 409 ALREADY_CANCELLED.

---

## Notes / non-bugs (don't "fix")

- Root path `/` has no route — blank/404 is expected.
- `models.Booking.reference_code` isn't `unique=True`, but uniqueness is enforced in
  the service (see B15); adding a DB unique constraint is optional hardening, not required.
- `time.sleep(...)` "pause" helpers are intentional race-wideners — keep them.

## Progress log

- 2026-07-09: Full codebase read; setup + smoke test green; 22 bugs identified across
  3 tiers (above).
- 2026-07-09: Branch `akib-bug-fix` created. **Tier 1 + Tier 2 all fixed & verified**
  (B1,B2,B3,B4,B5,B6,B7,B8,B9,B10,B11,B12,B13,B14,B19). Smoke test passes; targeted
  behavioral checks pass (token=900s, logout 401, dup username 409, refresh reuse 401,
  back-to-back 201 / overlap 409, zero-duration 400, tz +06:00→UTC, pagination asc/no-repeat,
  refund tiers 100/50/0 with half-up 501, ledger==response, report/availability cache fresh,
  export org-isolated).
- 2026-07-09: **Tier 3 concurrency all fixed & verified** (B15–B22). Added
  `threading.Lock`s in reference.py, ratelimit.py, stats.py; fixed lock ordering in
  notifications.py; added `_booking_lock` (with `db.rollback()`/`db.refresh` to dodge stale
  SQLite read snapshots) around the create and cancel critical sections in bookings.py.
  Verified with a live server + real parallel threads: same-slot 8×→1 confirmed/7 conflict;
  8 concurrent→8 unique reference codes; quota→3 in-window/2 rejected; 30 concurrent→20
  non-429; concurrent cancel→1 success/5 ALREADY_CANCELLED/1 RefundLog; interleaved
  create+cancel ×24 all returned ~1s (no deadlock); stats == DB-derived.
  **ALL 22 BUGS FIXED.** Full report in `BUG_REPORT.md`. Not committed yet.
