"""One password, one cookie.

Deliberately the smallest thing that is still honest security. There is exactly
one user, so there is no account model, no registration, no reset flow and no
password table - just a shared secret in the environment and a signed session
cookie.

What it does not skimp on, because these are the parts that actually matter
once the app has a public URL:

- the password is compared in constant time, and is stored as a PBKDF2 hash
  when you use ``HOFRADAR_PASSWORD_HASH`` (``hofradar hash-password`` prints
  one). A plain ``HOFRADAR_PASSWORD`` is accepted for convenience.
- the cookie carries no data, only an expiry and an HMAC over it, so it cannot
  be forged without the signing key and cannot be replayed after it expires.
- the signing key is generated once and persisted with 0600 permissions, so
  restarting the app does not log you out and two machines do not disagree.
- failed attempts are throttled per client, so a public URL is not a free
  brute-force oracle.

With no password configured the middleware is not installed at all. Running on
localhost stays exactly as convenient as it was.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from collections import defaultdict
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

log = logging.getLogger(__name__)

COOKIE_NAME = "hofradar_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # a month; this is a weekly-use tool
TOKEN_VERSION = "v1"

PBKDF2_ROUNDS = 240_000
PBKDF2_PREFIX = "pbkdf2_sha256"

#: Paths reachable without a session. Everything else needs one.
PUBLIC_PATH_PREFIXES = ("/static/", "/login", "/logout")
PUBLIC_PATHS = frozenset({"/healthz", "/favicon.ico"})

#: Login throttle: after this many failures from one client, refuse for a while.
MAX_FAILED_ATTEMPTS = 8
LOCKOUT_SECONDS = 300
ATTEMPT_WINDOW_SECONDS = 900

_failed_attempts: dict[str, list[float]] = defaultdict(list)


# --------------------------------------------------------------------------- #
# The configured secret
# --------------------------------------------------------------------------- #


def password_configured() -> bool:
    """Is authentication switched on at all?"""
    return bool(os.environ.get("HOFRADAR_PASSWORD_HASH") or os.environ.get("HOFRADAR_PASSWORD"))


def hash_password(password: str, *, salt: str | None = None) -> str:
    """Produce a ``pbkdf2_sha256$rounds$salt$hex`` string for the environment."""
    salt = salt or secrets.token_hex(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), PBKDF2_ROUNDS
    )
    return f"{PBKDF2_PREFIX}${PBKDF2_ROUNDS}${salt}${derived.hex()}"


def verify_password(candidate: str) -> bool:
    """Constant-time check against whichever form the environment provides."""
    stored_hash = os.environ.get("HOFRADAR_PASSWORD_HASH")
    if stored_hash:
        return _verify_against_hash(candidate, stored_hash)
    plain = os.environ.get("HOFRADAR_PASSWORD")
    if plain:
        return hmac.compare_digest(candidate.encode("utf-8"), plain.encode("utf-8"))
    return False


def _verify_against_hash(candidate: str, stored: str) -> bool:
    try:
        prefix, rounds_raw, salt, expected_hex = stored.split("$", 3)
        if prefix != PBKDF2_PREFIX:
            return False
        rounds = int(rounds_raw)
    except (ValueError, TypeError):
        log.warning("HOFRADAR_PASSWORD_HASH is malformed; refusing every password")
        return False
    derived = hashlib.pbkdf2_hmac(
        "sha256", candidate.encode("utf-8"), salt.encode("ascii"), rounds
    )
    return hmac.compare_digest(derived.hex(), expected_hex)


# --------------------------------------------------------------------------- #
# The signing key
# --------------------------------------------------------------------------- #


def _key_path() -> Path:
    return Path(os.environ.get("HOFRADAR_DATA_DIR", "data")) / "secret_key"


def secret_key() -> bytes:
    """The HMAC key for session cookies.

    ``HOFRADAR_SECRET_KEY`` wins - set it when you run more than one instance,
    or the two will reject each other's cookies. Otherwise one is generated and
    persisted next to the database, so a restart does not log you out.
    """
    from_env = os.environ.get("HOFRADAR_SECRET_KEY")
    if from_env:
        return from_env.encode("utf-8")

    path = _key_path()
    if path.exists():
        return path.read_bytes().strip()

    key = secrets.token_hex(32).encode("ascii")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:  # pragma: no cover - non-POSIX filesystems
        log.warning("could not restrict permissions on %s", path)
    log.info("generated a new session signing key at %s", path)
    return key


# --------------------------------------------------------------------------- #
# Tokens
# --------------------------------------------------------------------------- #


def _sign(payload: str) -> str:
    return hmac.new(secret_key(), payload.encode("ascii"), hashlib.sha256).hexdigest()


def make_token(*, now: float | None = None, max_age: int = SESSION_MAX_AGE_SECONDS) -> str:
    """``v1.<expiry>.<hmac>`` - no identity inside, because there is only one user."""
    expires_at = int((now if now is not None else time.time()) + max_age)
    payload = f"{TOKEN_VERSION}.{expires_at}"
    return f"{payload}.{_sign(payload)}"


def validate_token(token: str | None, *, now: float | None = None) -> bool:
    if not token:
        return False
    try:
        version, expiry_raw, signature = token.split(".", 2)
        expires_at = int(expiry_raw)
    except (ValueError, AttributeError):
        return False
    if version != TOKEN_VERSION:
        return False
    if not hmac.compare_digest(_sign(f"{version}.{expires_at}"), signature):
        return False
    return expires_at > (now if now is not None else time.time())


def is_authenticated(request: Request) -> bool:
    if not password_configured():
        return True
    return validate_token(request.cookies.get(COOKIE_NAME))


# --------------------------------------------------------------------------- #
# Throttling
# --------------------------------------------------------------------------- #


def client_key(request: Request) -> str:
    """Identify the caller for throttling.

    Behind a reverse proxy the socket address is the proxy, so the first hop in
    ``X-Forwarded-For`` is used when present. It is spoofable, which is why the
    throttle is a speed bump and the password is the actual control.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _recent_failures(key: str, *, now: float) -> list[float]:
    cutoff = now - ATTEMPT_WINDOW_SECONDS
    kept = [t for t in _failed_attempts[key] if t >= cutoff]
    _failed_attempts[key] = kept
    return kept


def locked_out_for(key: str, *, now: float | None = None) -> int:
    """Seconds remaining before this client may try again. 0 means go ahead."""
    now = now if now is not None else time.time()
    failures = _recent_failures(key, now=now)
    if len(failures) < MAX_FAILED_ATTEMPTS:
        return 0
    remaining = int(failures[-1] + LOCKOUT_SECONDS - now)
    return max(remaining, 0)


def record_failure(key: str, *, now: float | None = None) -> None:
    now = now if now is not None else time.time()
    _recent_failures(key, now=now)
    _failed_attempts[key].append(now)


def clear_failures(key: str) -> None:
    _failed_attempts.pop(key, None)


def reset_throttle() -> None:
    """Test hook - the throttle is process-local state."""
    _failed_attempts.clear()


# --------------------------------------------------------------------------- #
# Cookies
# --------------------------------------------------------------------------- #


def _secure_cookies(request: Request) -> bool:
    """Set ``Secure`` when the browser actually reached us over HTTPS.

    Hard-coding it would break plain-HTTP localhost; never setting it would send
    the cookie in clear text on a public deployment. The forwarded header is what
    Fly, Render and Railway put in front of the app.
    """
    if os.environ.get("HOFRADAR_FORCE_SECURE_COOKIES") == "1":
        return True
    if request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https":
        return True
    return request.url.scheme == "https"


def set_session_cookie(response: Response, request: Request) -> None:
    response.set_cookie(
        COOKIE_NAME,
        make_token(),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_secure_cookies(request),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# --------------------------------------------------------------------------- #
# Middleware
# --------------------------------------------------------------------------- #


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PATH_PREFIXES)


class PasswordGateMiddleware(BaseHTTPMiddleware):
    """Redirect anonymous browsers to the login page; 401 anonymous API calls."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if is_public_path(request.url.path) or is_authenticated(request):
            return await call_next(request)

        # An HTMX fetch or a JSON client must not be handed an HTML login page.
        wants_data = request.url.path.startswith("/api/") or request.headers.get("hx-request")
        if wants_data:
            response = Response(status_code=401, content="Anmeldung erforderlich")
            response.headers["HX-Redirect"] = "/login"
            return response

        target = request.url.path
        if request.url.query:
            target = f"{target}?{request.url.query}"
        return RedirectResponse(f"/login?next={_quote(target)}", status_code=303)


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def safe_next_path(candidate: str | None) -> str:
    """Only ever redirect within this app - an open redirect is a phishing gift."""
    if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    if is_public_path(candidate):
        return "/"
    return candidate
