# NE FRESH Login CSRF Cookie Fix

## Fixed issue
After CSRF protection was added, login could fail on localhost/http with:

Security token expired. Please refresh and try again.

## Cause
The session cookie was configured with SameSite=None while SESSION_COOKIE_SECURE was false. Modern Chrome/Safari reject SameSite=None cookies unless Secure is true, so the browser did not keep the session CSRF token between GET /login and POST /login.

## Fix applied
- Local/http default session SameSite is now Lax.
- SameSite=None is still allowed for HTTPS production only when SESSION_COOKIE_SECURE=true.
- Login form now includes a server-rendered CSRF hidden field directly.
- Existing base-template JS token injection and CSRF protection remain active for other forms.

## Production setting
For HTTPS production, use:

SESSION_COOKIE_SECURE=true
SESSION_COOKIE_SAMESITE=None

For localhost/http development, use default or:

SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=Lax
