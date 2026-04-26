"""HTTP client for the QuickBites simulator.

The simulator is the server, this is the client. Idempotent retries on 5xx.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx


class SimulatorError(RuntimeError):
    pass


class SimulatorClient:
    def __init__(
        self,
        base_url: str,
        candidate_token: str | None = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.candidate_token = candidate_token or ""
        self._timeout = timeout
        self._max_retries = max_retries

    @property
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.candidate_token:
            h["X-Candidate-Token"] = self.candidate_token
        return h

    async def _request(
        self, client: httpx.AsyncClient, method: str, path: str, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await client.request(
                    method, f"{self.base_url}{path}", json=json, headers=self._headers
                )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                last_exc = e
                if attempt == self._max_retries:
                    raise SimulatorError(f"network_error:{type(e).__name__}:{e}") from e
            else:
                if 200 <= resp.status_code < 300:
                    return resp.json()
                if resp.status_code in (409, 404):
                    raise SimulatorError(
                        f"http_{resp.status_code}:{resp.text[:200]}"
                    )
                if resp.status_code >= 500:
                    last_exc = SimulatorError(f"http_{resp.status_code}:{resp.text[:200]}")
                    if attempt == self._max_retries:
                        raise last_exc
                else:
                    raise SimulatorError(
                        f"http_{resp.status_code}:{resp.text[:200]}"
                    )
            delay = (2 ** attempt) * 0.5 + random.uniform(0, 0.25)
            await asyncio.sleep(delay)
        raise SimulatorError(f"unreachable_after_{self._max_retries}_retries: {last_exc}")

    async def health(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await self._request(client, "GET", "/healthz")

    async def start_session(
        self, mode: str, scenario_id: int | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"mode": mode}
        if scenario_id is not None:
            body["scenario_id"] = scenario_id
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await self._request(client, "POST", "/v1/session/start", json=body)

    async def reply(
        self, session_id: str, bot_message: str, actions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        body = {"bot_message": bot_message, "actions": actions}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await self._request(client, "POST", f"/v1/session/{session_id}/reply", json=body)

    async def transcript(self, session_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await self._request(client, "GET", f"/v1/session/{session_id}/transcript")

    async def candidate_summary(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await self._request(client, "GET", "/v1/candidate/summary")
