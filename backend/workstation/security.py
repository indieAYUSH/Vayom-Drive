import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, List
from fastapi import Request, Header, HTTPException, status
from ..config import settings

logger = logging.getLogger("vayom.workstation.security")


def is_contained(path: str, parent: str) -> bool:
    """Returns True if path is equal to or contained within parent directory."""
    try:
        p_real = os.path.realpath(path)
        parent_real = os.path.realpath(parent)
        return os.path.commonpath([p_real, parent_real]) == parent_real
    except Exception:
        return False


def validate_watch_folder_path(path_str: str) -> Path:
    """
    Validates a prospective watch folder path for safety:
    1. Resolves symlinks via os.path.realpath and expands ~
    2. Enforces existence and directory type
    3. Rejects filesystem root (/), /etc, and sensitive system directories
    4. Rejects user home directories directly (e.g. ~, /home/<user>, /root)
    5. Rejects hidden directories or directories containing hidden components (.ssh, .config, etc.)
    6. Rejects settings.DATA_DIR and any path containing or contained in it
    7. Rejects settings.ROOT_DIR (application source) and any path containing or contained in it
    8. Enforces containment within allowlisted roots (WORKSTATION_ALLOWED_ROOTS)
    """
    if not path_str or not path_str.strip():
        raise ValueError("Directory path cannot be empty.")

    raw_path = Path(path_str).expanduser()
    real_path_str = os.path.realpath(str(raw_path))
    resolved_path = Path(real_path_str)

    if not resolved_path.exists():
        raise FileNotFoundError(f"Directory path does not exist: {path_str}")
    if not resolved_path.is_dir():
        raise NotADirectoryError(f"Path is not a directory: {path_str}")

    # 1. Rejection: filesystem root
    if real_path_str == "/" or (os.name == "nt" and resolved_path.parent == resolved_path):
        raise ValueError("Filesystem root directory cannot be registered as a watch folder.")

    # 2. Rejection: /etc and sensitive system directories
    system_dirs = [
        "/etc", "/sys", "/proc", "/dev", "/boot", "/bin", "/sbin",
        "/usr", "/lib", "/lib64", "/var"
    ]
    for sys_dir in system_dirs:
        if is_contained(real_path_str, sys_dir):
            raise ValueError(f"System directory ({sys_dir}) cannot be registered as a watch folder.")

    # 3. Rejection: system home root / user home directories directly
    if real_path_str in ("/home", "/root"):
        raise ValueError("System home directory cannot be registered as a watch folder.")

    current_home = os.path.realpath(str(Path.home()))
    if real_path_str == current_home:
        raise ValueError(
            "User home directory cannot be watched directly. "
            "Please specify a dedicated subfolder (e.g. ~/Pictures or ~/Videos)."
        )

    # Check if direct child of /home (e.g. /home/otheruser)
    if os.path.dirname(real_path_str) == "/home":
        raise ValueError(
            "User home directory cannot be watched directly. "
            "Please specify a dedicated subfolder."
        )

    # 4. Rejection: hidden directories or paths containing hidden components (e.g. ~/.ssh, ~/.config)
    for part in resolved_path.parts:
        if part != "/" and part.startswith("."):
            raise ValueError(
                f"Hidden directories or directories containing hidden components ({part}) "
                "cannot be registered as a watch folder."
            )

    # 5. Rejection: settings.DATA_DIR (neither inside nor containing DATA_DIR)
    real_data_dir = os.path.realpath(str(settings.DATA_DIR))
    if is_contained(real_path_str, real_data_dir):
        raise ValueError("Application data directory (DATA_DIR) cannot be registered as a watch folder.")
    if is_contained(real_data_dir, real_path_str):
        raise ValueError("Cannot register a watch folder containing the application data directory.")

    # 6. Rejection: settings.ROOT_DIR (neither inside nor containing ROOT_DIR)
    real_root_dir = os.path.realpath(str(settings.ROOT_DIR))
    if is_contained(real_path_str, real_root_dir):
        raise ValueError("Application source/project directory cannot be registered as a watch folder.")
    if is_contained(real_root_dir, real_path_str):
        raise ValueError("Cannot register a watch folder containing the application source/project directory.")

    # 7. Containment check: must reside within at least one allowlisted root
    allowed_roots = getattr(
        settings,
        "WORKSTATION_ALLOWED_ROOTS",
        [str(Path.home()), "/media", "/mnt", "/Volumes", tempfile.gettempdir()]
    )

    is_allowed = False
    for root in allowed_roots:
        real_allowed = os.path.realpath(os.path.expanduser(root))
        if is_contained(real_path_str, real_allowed):
            # Reject direct watching of system mount / temp roots
            if real_path_str in ("/media", "/mnt", "/Volumes", tempfile.gettempdir(), "/tmp", "/var/tmp"):
                raise ValueError(
                    f"System directory root ({real_path_str}) cannot be watched directly. "
                    "Please specify a dedicated subfolder."
                )
            is_allowed = True
            break

    if not is_allowed:
        raise ValueError(f"Path is outside allowlisted watch roots: {path_str}")

    return resolved_path


def is_safe_file_path(file_path: Path) -> bool:
    """
    Validates that a file to be processed, ingested, or auto-deleted is safe:
    - Never inside DATA_DIR (e.g. vayom.db, auth_config.json, sessions)
    - Never inside ROOT_DIR (e.g. .env, source code)
    - Never inside /etc or other sensitive system directories
    - Never a direct child of user home root (e.g. ~/.bashrc)
    - Never inside a hidden directory (e.g. ~/.ssh/id_rsa)
    - Must be within an allowlisted root
    """
    try:
        real_p = os.path.realpath(str(file_path))

        # 1. Never inside DATA_DIR
        real_data = os.path.realpath(str(settings.DATA_DIR))
        if is_contained(real_p, real_data):
            return False

        # 2. Never inside ROOT_DIR
        real_root = os.path.realpath(str(settings.ROOT_DIR))
        if is_contained(real_p, real_root):
            return False

        # 3. Never inside sensitive system directories
        for sys_dir in ("/etc", "/sys", "/proc", "/dev", "/boot", "/bin", "/sbin", "/usr", "/lib", "/lib64", "/var"):
            if is_contained(real_p, sys_dir):
                return False

        # 4. Never direct child of home or system home
        home_real = os.path.realpath(str(Path.home()))
        file_dir = os.path.dirname(real_p)
        if file_dir in (home_real, "/home", "/root"):
            return False
        if os.path.dirname(file_dir) == "/home":
            return False

        # 5. Never inside a hidden directory component
        for part in Path(real_p).parent.parts:
            if part != "/" and part.startswith("."):
                return False

        # 6. Must reside in at least one allowlisted root
        allowed_roots = getattr(
            settings,
            "WORKSTATION_ALLOWED_ROOTS",
            [str(Path.home()), "/media", "/mnt", "/Volumes", tempfile.gettempdir()]
        )
        if allowed_roots:
            is_allowed = False
            for root in allowed_roots:
                real_allowed = os.path.realpath(os.path.expanduser(root))
                if is_contained(real_p, real_allowed):
                    is_allowed = True
                    break
            if not is_allowed:
                return False

        return True
    except Exception:
        return False


async def verify_workstation_auth(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None)
) -> bool:
    """
    Authenticates requests to workstation endpoints:
    1. Origin validation: blocks cross-origin requests from untrusted external origins.
    2. Header token validation: accepts valid Authorization Bearer token or X-API-Key.
    3. Node authentication validation: accepts requests when the drive node is actively authenticated.
    """
    # 1. Protect against CSRF / malicious browser pages from untrusted origins
    origin = request.headers.get("origin")
    if origin:
        trusted_origins = [
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
        ]
        is_trusted = (
            origin in trusted_origins
            or origin.startswith("http://localhost:")
            or origin.startswith("http://127.0.0.1:")
            or origin.startswith("https://localhost:")
            or origin.startswith("https://127.0.0.1:")
        )
        if not is_trusted:
            host = request.headers.get("host")
            if host and (origin == f"http://{host}" or origin == f"https://{host}"):
                is_trusted = True
        if not is_trusted:
            logger.warning(f"Forbidden cross-origin access to workstation route from: {origin}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-origin access from this origin is forbidden."
            )

    # 2. Check explicit token in Authorization or X-API-Key header
    token = None
    if authorization:
        if authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        else:
            token = authorization.strip()
    elif x_api_key:
        token = x_api_key.strip()

    if token:
        valid_tokens = {settings.SESSION_SECRET, settings.ADMIN_PASSWORD}
        if hasattr(settings, "API_TOKEN") and getattr(settings, "API_TOKEN"):
            valid_tokens.add(settings.API_TOKEN)
        if token in valid_tokens:
            return True
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials."
        )

    # 3. Check if drive node is authenticated (Telegram session active)
    if not settings.AUTH_MODE:
        settings.load_auth_config()
    is_node_authenticated = bool(
        settings.AUTH_MODE and (settings.TELEGRAM_CHANNEL_ID or settings.TELEGRAM_BOT_TOKEN)
    )
    if is_node_authenticated:
        return True

    # 4. If neither explicit valid token nor authenticated node, deny access
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required: Drive node is not authenticated."
    )
