"""
LiteLLM pre-call hook — READ-ONLY guard.

Checks whether the requested model matches the currently active profile
(recorded in /state/active-profile by admin-api). If not, returns
HTTP 503 with a structured error message.

This hook cannot switch profiles. Only admin-api can.
"""

from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from litellm.integrations.custom_logger import CustomLogger
from litellm.proxy.proxy_server import UserAPIKeyAuth, DualCache


STATE_DIR = Path("/state")
ACTIVE_FILE = STATE_DIR / "active-profile"
ADMIN_LOCK = STATE_DIR / "locked"

SWITCHABLE_MODELS = {"fast", "coder", "reason", "smart", "local"}


class ProfileGuard(CustomLogger):
    def _current(self) -> str:
        try:
            return ACTIVE_FILE.read_text().strip()
        except FileNotFoundError:
            return "off"

    def _lock_reason(self) -> Optional[str]:
        try:
            return ADMIN_LOCK.read_text().strip() or "locked"
        except FileNotFoundError:
            return None

    async def async_pre_call_hook(
        self,
        user_api_key_dict: UserAPIKeyAuth,
        cache: DualCache,
        data: dict,
        call_type: str,
    ) -> Optional[dict]:
        requested = data.get("model", "").strip()

        if requested not in SWITCHABLE_MODELS:
            return data

        active = self._current()
        if active == requested:
            return data

        lock = self._lock_reason()
        lock_hint = f" (admin-locked: {lock})" if lock else ""

        raise HTTPException(
            status_code=503,
            detail={
                "error": "profile_not_active",
                "message": (
                    f"Model '{requested}' is not currently available. "
                    f"The active profile is '{active}'{lock_hint}. "
                    f"Use model='{active}' instead, or ask the admin to switch."
                ),
                "requested": requested,
                "active": active,
                "admin_locked": lock is not None,
            },
        )


proxy_handler_instance = ProfileGuard()
