import asyncio
import time
from typing import Optional, Any
import httpx

from .config import config

START_URL = "https://api.parroto.app/api/learning-time/start"
HEARTBEAT_URL = "https://api.parroto.app/api/learning-time/heartbeat"
END_URL = "https://api.parroto.app/api/learning-time/end"


class HeartbeatService:
    """
    Background heartbeat service for Parroto learning time tracking.
    
    Automatically manages session lifecycle:
    1. Obtains a session_id via POST /api/learning-time/start
    2. Sends periodic heartbeats via POST /api/learning-time/heartbeat with sequence and delta_seconds
    3. Handles 401 (auto-refresh token) and 404/409 (restarts session)
    4. Cleanly closes session via POST /api/learning-time/end on stop
    """

    def __init__(
        self,
        token_manager: Optional[Any] = None,
        interval_minutes: Optional[float] = None,
        activity_type: str = "other",
        source: str = "app_layout",
    ):
        self.token_manager = token_manager
        minutes = interval_minutes if interval_minutes is not None else config.heartbeat_interval_minutes
        self.interval_seconds: float = max(1.0, float(minutes) * 60.0)
        self.activity_type = activity_type
        self.source = source

        self.session_id: Optional[str] = None
        self.sequence: int = 0
        self.last_beat_time: float = 0.0
        self._running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_token(self) -> str:
        if self.token_manager:
            return await self.token_manager.get_token()
        return config.bearer_token

    def _get_headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=20.0)
        return self._client

    async def start_session(self) -> Optional[str]:
        """Start a new learning session on Parroto to acquire session_id."""
        client = await self._ensure_client()
        token = await self._get_token()
        payload = {
            "activity_type": self.activity_type,
            "source": self.source,
        }

        try:
            res = await client.post(
                START_URL,
                json=payload,
                headers=self._get_headers(token),
                timeout=15.0,
            )

            # Auto-refresh if 401
            if res.status_code == 401 and self.token_manager and self.token_manager.can_refresh:
                print("[HEARTBEAT] Token expired (401). Refreshing token for session start...")
                await self.token_manager.refresh()
                token = await self._get_token()
                res = await client.post(
                    START_URL,
                    json=payload,
                    headers=self._get_headers(token),
                    timeout=15.0,
                )

            if res.status_code == 200:
                data = res.json().get("data", {}) or {}
                self.session_id = data.get("session_id")
                self.sequence = 0
                self.last_beat_time = time.time()
                print(f"[HEARTBEAT] Session started successfully: {self.session_id}")
                return self.session_id
            else:
                print(f"[HEARTBEAT] Failed to start session (HTTP {res.status_code}): {res.text[:120]}")
        except Exception as e:
            print(f"[HEARTBEAT] Error starting session: {e}")
        return None

    async def send_heartbeat(self) -> bool:
        """Send a heartbeat ping with current session_id, sequence, and delta_seconds."""
        if not self.session_id:
            await self.start_session()
            if not self.session_id:
                return False

        client = await self._ensure_client()
        now = time.time()
        delta_seconds = int(round(now - self.last_beat_time)) if self.last_beat_time > 0 else int(self.interval_seconds)
        if delta_seconds <= 0:
            delta_seconds = int(self.interval_seconds)

        self.sequence += 1
        payload = {
            "session_id": self.session_id,
            "sequence": self.sequence,
            "delta_seconds": delta_seconds,
        }

        token = await self._get_token()
        try:
            res = await client.post(
                HEARTBEAT_URL,
                json=payload,
                headers=self._get_headers(token),
                timeout=15.0,
            )

            # Auto-refresh if 401
            if res.status_code == 401 and self.token_manager and self.token_manager.can_refresh:
                print("[HEARTBEAT] Token expired (401). Refreshing token for heartbeat...")
                await self.token_manager.refresh()
                token = await self._get_token()
                res = await client.post(
                    HEARTBEAT_URL,
                    json=payload,
                    headers=self._get_headers(token),
                    timeout=15.0,
                )

            if res.status_code == 200:
                self.last_beat_time = now
                data = res.json().get("data", {}) or {}
                total_secs = data.get("total_seconds", 0)
                print(
                    f"[HEARTBEAT] Beat #{self.sequence} sent (+{delta_seconds}s, session total: {total_secs}s) "
                    f"-> Status: Online"
                )
                return True
            elif res.status_code in (404, 409):
                print(
                    f"[HEARTBEAT] Session expired or invalid (HTTP {res.status_code}). Starting a new session..."
                )
                self.session_id = None
                if await self.start_session():
                    return await self.send_heartbeat()
            else:
                print(f"[HEARTBEAT] Heartbeat request failed (HTTP {res.status_code}): {res.text[:120]}")
        except Exception as e:
            print(f"[HEARTBEAT] Network error sending heartbeat: {e}")
        return False

    async def end_session(self) -> bool:
        """Close current learning session cleanly."""
        if not self.session_id or not self._client:
            return True

        client = await self._ensure_client()
        final_delta = int(round(time.time() - self.last_beat_time)) if self.last_beat_time > 0 else 0
        payload = {
            "session_id": self.session_id,
            "final_delta_seconds": max(0, final_delta),
        }

        token = await self._get_token()
        try:
            res = await client.post(
                END_URL,
                json=payload,
                headers=self._get_headers(token),
                timeout=10.0,
            )
            if res.status_code == 200:
                print(f"[HEARTBEAT] Session closed cleanly (final delta: {max(0, final_delta)}s).")
                self.session_id = None
                return True
        except Exception:
            pass

        self.session_id = None
        return False

    async def start(self) -> None:
        """Launch background heartbeat task."""
        if self._running:
            return
        self._running = True
        # Start session immediately so user online status and session_id are active
        await self.start_session()
        self._task = asyncio.create_task(self._run_loop())

    async def _run_loop(self) -> None:
        try:
            while self._running:
                await asyncio.sleep(self.interval_seconds)
                if not self._running:
                    break
                await self.send_heartbeat()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[HEARTBEAT] Background loop error: {e}")

    async def stop(self) -> None:
        """Stop background heartbeat task and close session cleanly."""
        if not self._running:
            return
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        try:
            await self.end_session()
        except Exception:
            pass

        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
