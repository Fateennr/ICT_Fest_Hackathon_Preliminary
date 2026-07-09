# Bug Report

This document summarizes the identified bugs, their causes, and their
resolutions.

  ------------------------------------------------------------------------
  ID        Issue            Cause            Resolution
  --------- ---------------- ---------------- ----------------------------
  B1        Access token     Token expiration Removed the extra `*60`;
            lifsetime         multiplied       token lifetime is now 900
            incorrect        minutes by 60    seconds (15 minutes).
                             twice, producing 
                             15-hour tokens.  

  B2        Logout did not   Logout checked   Validate revoked tokens
            revoke tokens    `sub` while      using `jti`; reused
                             revocation       logged-out tokens now return
                             stored `jti`.    HTTP 401.

  B3        Duplicate        Existing user    Return HTTP 409
            usernames        was returned     `USERNAME_TAKEN`.
            accepted         instead of       
                             raising an       
                             error.           

  B4        Refresh tokens   Refresh tokens   Track used refresh token IDs
            reusable         were never       and reject reuse.
                             invalidated      
                             after use.       

  B5        Back-to-back     Overlap logic    Use strict overlap
            bookings         treated adjacent comparison so adjacent
            rejected         bookings as      bookings are allowed.
                             conflicts.       

  B6        Past bookings    Five-minute      Require booking start time
            accepted         grace window     to be at or after the
                             allowed bookings current time.
                             in the past.     

  B7        Zero-duration    Only maximum     Validate both minimum and
            bookings         duration was     maximum duration.
            accepted         checked.         

  B8        Sorting and      Descending sort, Sort ascending, compute
            pagination       incorrect        offset correctly, and use
            incorrect        offset,          configurable page limits.
                             hardcoded page   
                             size.            

  B9        Booking start    Response         Preserve the original
            time corrupted   overwrote        booking start time.
                             booking start    
                             time with        
                             creation time.   

  B10       Incorrect refund Boundary         Refund policy: ≥48h → 100%,
            boundaries       comparisons      ≥24h → 50%, otherwise 0%.
                             mishandled       
                             exactly 48       
                             hours.           

  B11       Refund rounding  Truncation and   Use consistent half-up
            inconsistent     banker's         rounding everywhere.
                             rounding         
                             produced         
                             different        
                             results.         

  B12       Stale report     Report cache not Invalidate report cache
            cache after      invalidated      after commit.
            booking          after booking    
                             creation.        

  B13       Stale            Availability     Invalidate availability
            availability     cache not        cache after cancellation.
            after            refreshed after  
            cancellation     cancellation.     

  B14       Room conflict    Overlap logic    Use strict overlap
            fix             treated adjacent comparison so adjacent
                            bookings as      bookings are allowed.
                            conflicts.        

  B15       Swagger auth     Security scheme  Include `HTTPBearer` in
            not shown        not wired into  `get_token_payload`; Swagger
                            dependencies     now shows bearer auth input.
                            chain.
  ------------------------------------------------------------------------

## Concurrency Related Bugs

The primary cause of these issues was **unsynchronized read-modify-write operations**. The `time.sleep()` pause helpers intentionally widen the race windows for testing and were intentionally kept in place. Since the application runs in a single process/container, a `threading.Lock()` is sufficient to ensure thread safety.

| ID | Issue | Cause | Resolution |
|----|-------|-------|------------|
| **B16** | Rate limit not enforced under concurrency | The rate limiter performed **trim → sleep → append → store** non-atomically, allowing concurrent requests to read stale buckets and under-count the request limit. | Wrapped the trim, append, and count operations inside a `threading.Lock()`. The artificial sleep was moved outside the critical section. |
| **B17** | Stats drift under concurrency | `record_create` and `record_cancel` performed read-modify-write operations with an artificial delay, causing concurrent updates to overwrite each other and resulting in incorrect statistics. | Protected both read-modify-write sequences with a `threading.Lock()` to ensure atomic updates. |
| **B18** | Deadlock between create and cancel notifications | `notify_created` acquired locks in the order **email → audit**, while `notify_cancelled` acquired them in the reverse order **audit → email**, creating a deadlock under concurrent execution. | Standardized lock acquisition order so both functions acquire **email → audit**, eliminating the deadlock. |
| **B19** | Duplicate reference codes | The shared reference counter was read, delayed, and incremented without synchronization, allowing concurrent threads to generate identical reference codes. | Guarded the read-and-increment operation with a module-level `threading.Lock()` and moved the formatting delay outside the lock. |
| **B20** | Double-booking under concurrency | Booking conflict detection and insertion were performed separately without synchronization, allowing concurrent requests to pass the conflict check simultaneously. | Protected the entire **check → commit** sequence using a module-level `_booking_lock`, with `db.rollback()` before validation to ensure the latest committed state is read. |
| **B21** | Booking quota bypass under concurrency | The quota validation and booking insertion were not atomic, allowing concurrent requests to bypass booking limits. | Used the same `_booking_lock` around the quota check and insertion process, together with `db.rollback()` to validate against the latest committed database state. |
| **B22** | Double refund on concurrent cancellations | The cancellation status check, refund logging, and booking status update were executed independently, allowing simultaneous cancellation requests to issue multiple refunds. | Wrapped the cancellation logic inside `_booking_lock`, performing `db.rollback()` and `db.refresh(booking)` before checking the booking status to ensure only one refund is processed. |
| **B23** | Booking details accessible by any member in the organization | `get_booking` only filtered bookings by `Room.org_id == user.org_id` and did not verify ownership, allowing one member to retrieve another member's booking using its ID. | Added an ownership check after the not-found validation: non-admin users can only access bookings where `booking.user_id == user.id`; otherwise, return `404 BOOKING_NOT_FOUND`. |
| **B24** | Stale usage report after room creation | After creating a room, the room was committed to the database but the usage report cache was not invalidated, causing cached reports to omit newly created rooms. | Added `cache.invalidate_report(admin.org_id)` immediately after the room creation commit so the usage report reflects the latest state, including newly created rooms. |

## (Authentication / Registration) Bugs

| ID | Issue | Cause | Resolution |
|----|-------|-------|------------|
| **B25** | `POST /bookings` with a malformed datetime returned HTTP 500 | `parse_input_datetime` called `datetime.fromisoformat` on the raw `start_time`/`end_time` strings; an unparseable value raised an uncaught `ValueError`, producing a 500 instead of a client error. | Wrapped the two `parse_input_datetime` calls in `try/except (ValueError, TypeError)` and raised `400 INVALID_BOOKING_WINDOW`. |
| **B26** | Concurrent registration of a **new** org returned HTTP 500 | `register` did read-org → (None) → `INSERT org` non-atomically. When several requests registered the same brand-new `org_name` at once, all saw `org is None`, all inserted, and every loser hit the unique-name constraint → uncaught `IntegrityError` → 500. | Caught `IntegrityError` on the org insert: `rollback`, re-query the now-existing org, and continue as a **member**. Exactly one racer becomes admin, the rest join as members (Rule 15). |
| **B27** | Concurrent registration of the same **username** returned HTTP 500 instead of 409 | The `existing` username check and the user `INSERT` were not atomic, so two concurrent identical registrations both passed the check, then the loser violated `uq_user_org_username` → uncaught `IntegrityError` → 500. | Caught `IntegrityError` on the user insert: `rollback` and raise `409 USERNAME_TAKEN` (Rule 15). |
| **B4b** | Refresh single-use not atomic under concurrency | `is_refresh_token_used` (check) and `mark_refresh_token_used` (mark) were separate calls with a DB query between them, so two concurrent uses of the same refresh token could both rotate. | Replaced with `consume_refresh_token()` — an atomic check-and-mark guarded by a `threading.Lock()`; the first caller wins, all concurrent reuses get 401 (Rule 8). |

All four were verified with real parallel-thread tests: malformed datetime → 400; 10 concurrent new-org registrations → exactly 1 admin + 9 members, zero 500s; 10 concurrent duplicate-username registrations → 1×201 + 9×409, zero 500s; 12 concurrent refreshes of one token → exactly 1 success.

Our identified bugs and fixes improved:

-   Authentication and token security
-   Registration robustness under concurrency
-   Booking validation and scheduling
-   Sorting and pagination
-   Refund calculation accuracy
-   Cache consistency
-   Timezone handling
-   Multi-tenant data isolation
