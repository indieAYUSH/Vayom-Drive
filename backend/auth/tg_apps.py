import re
import time
import random
import logging
from typing import Optional, Tuple, Dict, List
import httpx

logger = logging.getLogger("vayom.auth.tg_apps")

_MY_TELEGRAM_BASE = "https://my.telegram.org"
_MY_TELEGRAM_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://my.telegram.org",
    "Referer": "https://my.telegram.org/auth",
}

# Local sliding-window attempt limiter per phone number
_phone_attempts: Dict[str, List[float]] = {}
MAX_ATTEMPTS_PER_PHONE = 20
ATTEMPT_WINDOW_SECONDS = 900.0  # 15 minutes


class AppsError(Exception):
    """Base exception for my.telegram.org automation errors."""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AppsRateLimitError(AppsError):
    """Raised when Telegram or local limiter throttles requests."""
    def __init__(self, message: str, wait_seconds: Optional[int] = None):
        super().__init__(message, status_code=400)
        self.wait_seconds = wait_seconds


class AppsInvalidCodeError(AppsError):
    """Raised when confirmation code is invalid or expired."""
    def __init__(self, message: str = "Invalid confirmation code. Check the code sent to your Telegram app and try again."):
        super().__init__(message, status_code=400)


class Apps2FARequiredError(AppsError):
    """Raised when Telegram requires web 2FA password."""
    def __init__(self, message: str = "Telegram 2FA Cloud Password is enabled on your account. Please enter your API ID and hash manually."):
        super().__init__(message, status_code=400)


class AppsParserError(AppsError):
    """Raised when HTML parsing fails due to site layout changes."""
    def __init__(
        self,
        message: str = (
            "Could not parse API credentials from my.telegram.org. The site layout may have changed. "
            "Please visit https://my.telegram.org and enter your keys manually."
        ),
    ):
        super().__init__(message, status_code=422)


class AppsPortalError(AppsError):
    """Raised when my.telegram.org is unreachable, blocked, or requires a captcha."""
    def __init__(self, message: str = "Telegram web portal is temporarily unreachable or returned unexpected content. Please enter keys manually."):
        super().__init__(message, status_code=502)


def check_and_record_phone_attempt(phone: str):
    """Enforces a local rate limit on attempts per phone number."""
    now = time.time()
    clean = re.sub(r"[^\d+]", "", phone.strip())
    attempts = _phone_attempts.get(clean, [])
    # Filter attempts within the sliding window
    valid_attempts = [t for t in attempts if now - t < ATTEMPT_WINDOW_SECONDS]
    if len(valid_attempts) >= MAX_ATTEMPTS_PER_PHONE:
        remaining_wait = int(ATTEMPT_WINDOW_SECONDS - (now - valid_attempts[0]))
        raise AppsRateLimitError(
            f"Too many attempts for this phone number. Please wait {max(1, remaining_wait)} seconds or enter keys manually.",
            wait_seconds=remaining_wait,
        )
    valid_attempts.append(now)
    _phone_attempts[clean] = valid_attempts


def reset_phone_attempts_for_test():
    """Helper for testing to reset the in-memory limiter."""
    _phone_attempts.clear()


def parse_apps_page(html: str) -> Tuple[Optional[int], Optional[str], Optional[str]]:
    """
    Parses the HTML of my.telegram.org/apps.
    Returns:
        (api_id, api_hash, create_hash)
    - If an app already exists: returns (api_id, api_hash, None).
    - If no app exists: returns (None, None, create_hash) where create_hash is the CSRF token
      from the /apps/create form.
    - If the layout is unparseable: returns (None, None, None).
    """
    if not html or not isinstance(html, str):
        return None, None, None

    api_id: Optional[int] = None
    api_hash: Optional[str] = None

    # Pattern A: input values or strong text for app_api_id / api_id
    id_m = re.search(
        r'(?:app_api_id|api_id)[^>]*?(?:value=["\'](\d+)["\']|><strong>(\d+)</strong>|>(\d+)<)',
        html,
        re.I,
    )
    if id_m:
        val = next((g for g in id_m.groups() if g), None)
        if val:
            try:
                api_id = int(val)
            except ValueError:
                pass

    if not api_id:
        fb = re.search(r'App\s+api_id:.*?(?:<strong>|>)\s*(\d+)\s*(?:</strong>|<)', html, re.I | re.DOTALL)
        if fb:
            try:
                api_id = int(fb.group(1))
            except ValueError:
                pass

    # Pattern B: input values or strong text for app_api_hash / api_hash (32-char hex)
    hash_m = re.search(
        r'(?:app_api_hash|api_hash)[^>]*?(?:value=["\']([a-f0-9]{32})["\']|><strong>([a-f0-9]{32})</strong>|>([a-f0-9]{32})<)',
        html,
        re.I,
    )
    if hash_m:
        val = next((g for g in hash_m.groups() if g), None)
        if val:
            api_hash = val.lower()

    if not api_hash:
        fb2 = re.search(r'App\s+api_hash:.*?(?:<strong>|>)\s*([a-f0-9]{32})\s*(?:</strong>|<)', html, re.I | re.DOTALL)
        if fb2:
            api_hash = fb2.group(1).lower()

    # If already found existing credentials, return them
    if api_id and api_hash:
        return api_id, api_hash, None

    # Pattern C: Check for app creation form CSRF token
    csrf_m = re.search(r'name=["\']hash["\']\s+value=["\']([a-zA-Z0-9_-]+)["\']', html, re.I)
    if not csrf_m:
        csrf_m = re.search(r'value=["\']([a-zA-Z0-9_-]+)["\']\s+name=["\']hash["\']', html, re.I)

    create_hash = csrf_m.group(1) if csrf_m else None
    return api_id, api_hash, create_hash


class TelegramAppsClient:
    """Client for automated API key generation from my.telegram.org."""

    def __init__(self, timeout: float = 20.0):
        self.timeout = timeout

    async def send_password(self, phone: str) -> str:
        """
        Sends an official login code to the user's Telegram app for my.telegram.org.
        Returns the random_hash needed for login.
        """
        raw_phone = phone.strip()
        clean_phone = re.sub(r"[^\d+]", "", raw_phone)
        if not clean_phone.startswith("+") and len(clean_phone) >= 10:
            clean_phone = f"+{clean_phone}"

        if len(clean_phone) < 7:
            raise AppsError("Invalid phone number format. Provide full international format with country code (e.g., +12025550123).")

        check_and_record_phone_attempt(clean_phone)

        try:
            async with httpx.AsyncClient(timeout=self.timeout, headers=_MY_TELEGRAM_HEADERS) as client:
                resp = await client.post(
                    f"{_MY_TELEGRAM_BASE}/auth/send_password",
                    data={"phone": clean_phone},
                )
                try:
                    data = resp.json()
                except Exception:
                    logger.warning("my.telegram.org send_password returned non-JSON response")
                    raise AppsPortalError("Telegram returned an unexpected response. Please enter keys manually.")

                if isinstance(data, dict):
                    if data.get("random_hash"):
                        return data["random_hash"]

                    err_text = data.get("text") or "Telegram rejected the phone number."
                    # Parse flood wait or too many tries
                    if "flood" in err_text.lower() or "too many" in err_text.lower():
                        secs_match = re.search(r'(\d+)', err_text)
                        secs = int(secs_match.group(1)) if secs_match else None
                        raise AppsRateLimitError(
                            f"Telegram rate limit: {err_text}. Please wait before retrying or enter keys manually.",
                            wait_seconds=secs,
                        )
                    raise AppsError(err_text)

                raise AppsPortalError()
        except (AppsError, AppsRateLimitError, AppsPortalError):
            raise
        except httpx.HTTPError as exc:
            logger.warning(f"Network error contacting my.telegram.org: {exc}")
            raise AppsPortalError(f"Network error contacting my.telegram.org: {exc}. Please enter keys manually.")
        except Exception as exc:
            logger.error(f"Unexpected error in send_password: {exc}")
            raise AppsPortalError("Unexpected error connecting to Telegram. Please enter keys manually.")

    async def login_and_get_keys(self, phone: str, random_hash: str, code: str) -> Tuple[int, str]:
        """
        Logs in to my.telegram.org with code, fetches or creates the app, and returns (api_id, api_hash).
        Immediately discards any session cookies and sensitive inputs.
        """
        clean_phone = phone.strip()
        clean_hash = random_hash.strip()
        clean_code = code.strip()

        if not clean_phone or not clean_hash or not clean_code:
            raise AppsError("Phone number, random hash, and confirmation code are required.")

        check_and_record_phone_attempt(clean_phone)

        # Use an isolated AsyncClient so session cookies are scoped only to this block and discarded immediately
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, headers=_MY_TELEGRAM_HEADERS) as client:
            try:
                # 1. Login
                login_resp = await client.post(
                    f"{_MY_TELEGRAM_BASE}/auth/login",
                    data={
                        "phone": clean_phone,
                        "random_hash": clean_hash,
                        "password": clean_code,
                    },
                )

                login_text = login_resp.text.strip()
                if login_resp.status_code != 200 or login_text.lower() != "true":
                    err_msg = None
                    try:
                        err_json = login_resp.json()
                        err_msg = err_json.get("text")
                    except Exception:
                        pass

                    if not err_msg:
                        lower_text = login_text.lower()
                        if "password" in lower_text or "two-step" in lower_text:
                            raise Apps2FARequiredError()
                        if "invalid" in lower_text or "wrong" in lower_text or "expired" in lower_text:
                            raise AppsInvalidCodeError()
                        if "flood" in lower_text or "too many" in lower_text:
                            raise AppsRateLimitError("Too many login attempts. Please wait or enter keys manually.")
                        err_msg = (login_text[:120] if login_text else "Telegram rejected web login.") + " Please enter keys manually."
                    raise AppsError(err_msg)

                # 2. GET /apps
                apps_resp = await client.get(f"{_MY_TELEGRAM_BASE}/apps")
                if apps_resp.status_code != 200:
                    raise AppsPortalError(f"Could not load Telegram applications page (HTTP {apps_resp.status_code}).")

                api_id, api_hash, create_hash = parse_apps_page(apps_resp.text)

                # 3. If no app exists, create one
                if (not api_id or not api_hash) and create_hash:
                    random_suffix = random.randint(1000, 99999)
                    create_resp = await client.post(
                        f"{_MY_TELEGRAM_BASE}/apps/create",
                        data={
                            "hash": create_hash,
                            "app_title": "Vayom Drive",
                            "app_shortname": f"vayom{random_suffix}",
                            "app_url": "https://vayom.local",
                            "app_platform": "desktop",
                            "app_desc": "Personal Cloud Storage Node",
                        },
                    )
                    if create_resp.status_code == 200:
                        api_id, api_hash, _ = parse_apps_page(create_resp.text)

                    # If not in the create response body, re-fetch /apps once
                    if not api_id or not api_hash:
                        apps_retry = await client.get(f"{_MY_TELEGRAM_BASE}/apps")
                        if apps_retry.status_code == 200:
                            api_id, api_hash, _ = parse_apps_page(apps_retry.text)

                if not api_id or not api_hash:
                    raise AppsParserError()

                return api_id, api_hash

            except (AppsError, AppsRateLimitError, AppsInvalidCodeError, Apps2FARequiredError, AppsParserError, AppsPortalError):
                raise
            except httpx.HTTPError as exc:
                logger.warning(f"Network error during key extraction: {exc}")
                raise AppsPortalError(f"Network error contacting my.telegram.org: {exc}. Please enter keys manually.")
            except Exception as exc:
                logger.error(f"Unexpected error in login_and_get_keys: {exc}")
                raise AppsPortalError("Unexpected error during key extraction. Please enter keys manually.")
