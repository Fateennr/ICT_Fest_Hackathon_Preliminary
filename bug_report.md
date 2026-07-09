# Bug Report

## Overview

This document summarizes the identified bugs, their causes, and their
resolutions.

  ------------------------------------------------------------------------
  ID        Issue            Cause            Resolution
  --------- ---------------- ---------------- ----------------------------
  B1        Access token     Token expiration Removed the extra `*60`;
            lifetime         multiplied       token lifetime is now 900
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

  B14       Timezone offsets Timezone removed Convert to UTC before
            ignored          before UTC       stripping timezone
                             conversion.      information.

  B15       Cross-tenant     Export ignored   Restrict export queries by
            booking export   organization     organization ID.
                             filtering.       
  ------------------------------------------------------------------------

## Summary

A total of **15 bugs** were identified and fixed. The fixes improved:

-   Authentication and token security
-   Booking validation and scheduling
-   Sorting and pagination
-   Refund calculation accuracy
-   Cache consistency
-   Timezone handling
-   Multi-tenant data isolation
