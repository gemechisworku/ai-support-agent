"""The scheduler's client for the gateway's service-scope API (SCHED-R1; GW-R11, T-GW-10).

The scheduler reaches customer data only through here (`Authorization: Bearer $GATEWAY_SERVICE_TOKEN`),
never the bank directly. Every failure becomes a `ServiceCallError` that carries the status or cause but
never the token or a response body (which may hold customer data).

    GET  /service/candidates?campaign=...             -> list[Candidate]
    GET  /service/contact-preferences/{customer_id}   -> ContactPrefs      (re-read just before dialling)
    POST /service/attempts                            -> AttemptRecorded   (the log the frequency cap counts)
"""

import httpx
from pydantic import TypeAdapter, ValidationError

from supportagent.contracts.gateway import AttemptRecorded, CallAttempt, Candidate, ContactPrefs
from supportagent.contracts.scheduler import CampaignName

_CANDIDATES = TypeAdapter(list[Candidate])


class ServiceCallError(Exception):
    """A service-scope call failed. The message names the call and the HTTP status or cause only."""


class GatewayServiceClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
            timeout=timeout_s,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, what: str, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = await self._http.request(method, path, **kwargs)  # type: ignore[arg-type]
        except httpx.HTTPError as exc:
            raise ServiceCallError(f"{what}: gateway unreachable ({type(exc).__name__})") from exc
        if response.status_code >= 400:
            raise ServiceCallError(f"{what}: gateway answered {response.status_code}")
        return response

    async def candidates(self, campaign: CampaignName) -> list[Candidate]:
        response = await self._call("candidates", "GET", "/service/candidates", params={"campaign": campaign})
        try:
            return _CANDIDATES.validate_python(response.json())
        except (ValidationError, ValueError) as exc:
            raise ServiceCallError("candidates: unexpected response from the gateway") from exc

    async def contact_preferences(self, customer_id: int) -> ContactPrefs:
        response = await self._call(
            "contact_preferences", "GET", f"/service/contact-preferences/{customer_id}"
        )
        try:
            return ContactPrefs.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise ServiceCallError("contact_preferences: unexpected response from the gateway") from exc

    async def record_attempt(self, attempt: CallAttempt) -> AttemptRecorded:
        response = await self._call(
            "record_attempt",
            "POST",
            "/service/attempts",
            content=attempt.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
        try:
            return AttemptRecorded.model_validate(response.json())
        except (ValidationError, ValueError) as exc:
            raise ServiceCallError("record_attempt: unexpected response from the gateway") from exc
