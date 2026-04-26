"""
Admin API — the only component that can stop/start model containers.

Not exposed to users. Bound to 127.0.0.1 by default. Gated by ADMIN_TOKEN
(distinct from the user-facing LITELLM_MASTER_KEY).

Endpoints:
  GET  /status    → current profile, lock state, running containers
  POST /switch    → { profile: "fast"|"coder"|"reason"|"smart"|"off" }
  POST /lock      → { profile: str, reason: str }
  POST /unlock
  POST /reset
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Literal, Optional

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Coding Stack Admin API")

STATE_DIR = Path("/state")
ACTIVE_FILE = STATE_DIR / "active-profile"
LAST_SWITCH_FILE = STATE_DIR / "last-switch-at"
SWITCHING_LOCK = STATE_DIR / "switching"
ADMIN_LOCK = STATE_DIR / "locked"

COMPOSE_FILE = "/workspace/docker-compose.yml"
SWITCH_TIMEOUT = 180
POLL_INTERVAL = 3

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
if not ADMIN_TOKEN:
    raise RuntimeError("ADMIN_TOKEN env var is required")


# --------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------
def require_admin(token: Optional[str]) -> None:
    if token != ADMIN_TOKEN:
        raise HTTPException(401, "invalid admin token")


# --------------------------------------------------------------------
# State
# --------------------------------------------------------------------
def current_profile() -> str:
    STATE_DIR.mkdir(exist_ok=True)
    if not ACTIVE_FILE.exists():
        ACTIVE_FILE.write_text("off")
    return ACTIVE_FILE.read_text().strip()


def mark_switched(profile: str) -> None:
    ACTIVE_FILE.write_text(profile)
    LAST_SWITCH_FILE.write_text(str(time.time()))


def lock_reason() -> Optional[str]:
    if ADMIN_LOCK.exists():
        return ADMIN_LOCK.read_text().strip() or "no reason given"
    return None


# --------------------------------------------------------------------
# Docker ops
# --------------------------------------------------------------------
async def run_cmd(*cmd: str, check: bool = True) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    rc = proc.returncode or 0
    if check and rc != 0:
        raise HTTPException(
            500, f"command failed ({rc}): {' '.join(cmd)} :: {err.decode()[:500]}"
        )
    return rc, out.decode(), err.decode()


async def stop_all_model_containers() -> None:
    for name in ("vllm-main", "vllm-fim"):
        await run_cmd("docker", "stop", name, check=False)
        await run_cmd("docker", "rm", name, check=False)


async def start_profile(profile: str) -> None:
    await run_cmd(
        "docker", "compose", "-f", COMPOSE_FILE,
        "--profile", profile, "up", "-d",
    )


async def wait_ready() -> None:
    deadline = time.time() + SWITCH_TIMEOUT
    async with httpx.AsyncClient(timeout=5.0) as client:
        while time.time() < deadline:
            try:
                r = await client.get("http://vllm-main:8000/v1/models")
                if r.status_code == 200:
                    return
            except Exception:
                pass
            await asyncio.sleep(POLL_INTERVAL)
    raise HTTPException(504, "model backend did not become ready in time")


# --------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------
class SwitchReq(BaseModel):
    profile: Literal["fast", "coder", "reason", "smart", "local", "off"]


class LockReq(BaseModel):
    profile: Literal["fast", "coder", "reason", "smart", "local"]
    reason: str = "locked by admin"


# --------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------
@app.get("/status")
async def status():
    rc, out, _ = await run_cmd(
        "docker", "ps", "--format",
        "{{.Names}}\t{{.Image}}\t{{.Status}}", check=False,
    )
    containers = [
        dict(zip(("name", "image", "status"), line.split("\t")))
        for line in out.strip().splitlines() if line
    ]
    last_age = None
    if LAST_SWITCH_FILE.exists():
        try:
            last_age = int(time.time() - float(LAST_SWITCH_FILE.read_text().strip()))
        except Exception:
            pass

    return {
        "active_profile": current_profile(),
        "admin_lock": lock_reason(),
        "switching_in_progress": SWITCHING_LOCK.exists(),
        "last_switch_age_seconds": last_age,
        "containers": containers,
    }


@app.post("/switch")
async def switch(req: SwitchReq, x_admin_token: Optional[str] = Header(None)):
    require_admin(x_admin_token)

    if (lr := lock_reason()) is not None:
        raise HTTPException(423, f"admin lock is set: {lr}. Use /unlock first.")

    if SWITCHING_LOCK.exists():
        raise HTTPException(409, "a switch is already in progress")

    SWITCHING_LOCK.touch()
    try:
        cur = current_profile()
        if cur == req.profile:
            return {"ok": True, "unchanged": True, "profile": cur}

        await stop_all_model_containers()
        if req.profile != "off":
            await start_profile(req.profile)
            await wait_ready()

        mark_switched(req.profile)
        return {"ok": True, "from": cur, "to": req.profile}
    finally:
        SWITCHING_LOCK.unlink(missing_ok=True)


@app.post("/lock")
async def lock(req: LockReq, x_admin_token: Optional[str] = Header(None)):
    require_admin(x_admin_token)

    if current_profile() != req.profile:
        # Reuse switch logic; it handles timeouts and state
        await switch(SwitchReq(profile=req.profile), x_admin_token)

    ADMIN_LOCK.write_text(req.reason)
    return {"ok": True, "locked_to": req.profile, "reason": req.reason}


@app.post("/unlock")
async def unlock(x_admin_token: Optional[str] = Header(None)):
    require_admin(x_admin_token)
    ADMIN_LOCK.unlink(missing_ok=True)
    return {"ok": True}


@app.post("/reset")
async def reset(x_admin_token: Optional[str] = Header(None)):
    require_admin(x_admin_token)
    SWITCHING_LOCK.unlink(missing_ok=True)
    ADMIN_LOCK.unlink(missing_ok=True)
    await stop_all_model_containers()
    ACTIVE_FILE.write_text("off")
    return {"ok": True, "state": "reset"}
