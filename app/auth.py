import asyncio
import base64
import json
import time
from typing import Optional
import httpx

from .config import config, clean_token

GOOGLE_TOKEN_URL = "https://securetoken.googleapis.com/v1/token"


def extract_jwt_exp(token: str) -> float:
    """Extract expiry timestamp from JWT payload without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1]
            rem = len(payload_b64) % 4
            if rem > 0:
                payload_b64 += "=" * (4 - rem)
            decoded = base64.urlsafe_b64decode(payload_b64)
            data = json.loads(decoded)
            return float(data.get("exp", 0.0))
    except Exception:
        pass
    return 0.0


class TokenManager:
    def __init__(
        self,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.access_token: str = clean_token(access_token or config.bearer_token)
        self.refresh_token: str = clean_token(refresh_token or config.refresh_token)
        self.api_key: str = clean_token(api_key or config.api_key)
        self.expires_at: float = extract_jwt_exp(self.access_token) if self.access_token else 0.0
        self._async_lock = asyncio.Lock()

    @property
    def can_refresh(self) -> bool:
        return bool(self.refresh_token and self.api_key)

    def is_expired(self) -> bool:
        """Check if the current access token is missing or expired (with 60-second safety window)."""
        if not self.access_token:
            return True
        if self.expires_at > 0.0:
            return time.time() >= (self.expires_at - 60)
        # If we have refresh credentials and cannot determine expiry, refresh to be safe
        return self.can_refresh

    def refresh_sync(self, force: bool = True) -> str:
        """Synchronously request a new access_token from Google Identity Platform."""
        if not self.can_refresh:
            raise ValueError("Cannot refresh token: REFRESH_TOKEN or API_KEY is missing!")

        if not force and not self.is_expired() and self.access_token:
            return self.access_token

        url = f"{GOOGLE_TOKEN_URL}?key={self.api_key}"
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }

        with httpx.Client(timeout=15.0) as client:
            res = client.post(url, data=data)
            if res.status_code != 200:
                raise RuntimeError(
                    f"Failed to refresh token from Google! (HTTP {res.status_code}): {res.text}"
                )
            res_data = res.json()
            new_token = res_data.get("access_token") or res_data.get("id_token")
            if not new_token:
                raise RuntimeError(f"No access_token found in response: {res_data}")

            self.access_token = new_token
            expires_in = float(res_data.get("expires_in", 3600))
            self.expires_at = time.time() + expires_in

            # Update refresh token if Google rotated it
            if res_data.get("refresh_token"):
                self.refresh_token = res_data.get("refresh_token")

            print(f"[AUTH] Successfully refreshed access token! (Valid for {int(expires_in)}s)")
            return self.access_token

    async def refresh(self, force: bool = True) -> str:
        """Asynchronously request a new access_token from Google Identity Platform."""
        if not self.can_refresh:
            raise ValueError("Cannot refresh token: REFRESH_TOKEN or API_KEY is missing!")

        async with self._async_lock:
            # Check if another coroutine already refreshed it while waiting for the lock
            if not force and not self.is_expired() and self.access_token:
                return self.access_token

            url = f"{GOOGLE_TOKEN_URL}?key={self.api_key}"
            data = {
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }

            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, data=data)
                if res.status_code != 200:
                    raise RuntimeError(
                        f"Failed to refresh token from Google! (HTTP {res.status_code}): {res.text}"
                    )
                res_data = res.json()
                new_token = res_data.get("access_token") or res_data.get("id_token")
                if not new_token:
                    raise RuntimeError(f"No access_token found in response: {res_data}")

                self.access_token = new_token
                expires_in = float(res_data.get("expires_in", 3600))
                self.expires_at = time.time() + expires_in

                if res_data.get("refresh_token"):
                    self.refresh_token = res_data.get("refresh_token")

                print(f"[AUTH] Successfully refreshed access token! (Valid for {int(expires_in)}s)")
                return self.access_token

    def get_token_sync(self) -> str:
        """Get a valid access token synchronously (auto-refreshes if needed)."""
        if self.is_expired():
            if self.can_refresh:
                return self.refresh_sync(force=True)
        return self.access_token

    async def get_token(self) -> str:
        """Get a valid access token asynchronously (auto-refreshes if needed)."""
        if self.is_expired():
            if self.can_refresh:
                return await self.refresh(force=True)
        return self.access_token
