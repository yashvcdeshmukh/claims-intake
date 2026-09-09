"""HTTP surface for the claims intake service.

This layer does three things and no more: it parses the request, it calls the
service, and it maps the outcome to a status code. It holds no rule logic. A rule
that appears here is a rule the service layer cannot be tested for.

Day 4 lab. Implement against `docs/api-contract.md` sections 5 and 6.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from claims.api.mapping import (
    MappedResponse,
    map_lookup_failed,
    map_recorded,
    map_rule_failure,
    parse_notification,
)
from claims.models import RuleFailure
from claims.policy_client import PolicyClient, PolicyLookupFailed, StubPolicyClient
from claims.repository import NotificationRepository
from claims.service import submit_notification


def create_app(
    policy_client: PolicyClient | None = None,
    repository: NotificationRepository | None = None,
) -> FastAPI:
    """Build an app with its own client and store.

    Tests pass a fresh repository (and a client with `fail_with` set) so the
    suite is not order-dependent. The module-level `app` is the running service.
    """

    application = FastAPI(title="Claims Intake Service")
    application.state.policy_client = policy_client or StubPolicyClient()
    application.state.repository = repository or NotificationRepository()
    application.add_api_route(
        "/notifications",
        post_notification,
        methods=["POST"],
        response_class=JSONResponse,
    )
    return application


async def post_notification(request: Request) -> JSONResponse:
    """POST /notifications. Parse, submit, map. Nothing else."""

    parsed = parse_notification(await request.body())
    if isinstance(parsed, MappedResponse):
        return _respond(parsed)

    policy_client: PolicyClient = request.app.state.policy_client
    repository: NotificationRepository = request.app.state.repository
    try:
        outcome = submit_notification(parsed, policy_client, repository)
    except PolicyLookupFailed as failed:
        return _respond(map_lookup_failed(failed))
    if isinstance(outcome, RuleFailure):
        return _respond(map_rule_failure(outcome, parsed, policy_client, repository))
    return _respond(map_recorded(outcome))


def _respond(mapped: MappedResponse) -> JSONResponse:
    return JSONResponse(status_code=mapped.status_code, content=mapped.body)


app = create_app()
