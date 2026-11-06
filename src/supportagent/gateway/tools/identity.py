"""Unverified-scope tools: `lookup_by_phone` and `verify_identity` (GW-R3)."""

from supportagent.contracts.gateway import (
    LookupByPhoneInput,
    LookupByPhoneOutput,
    VerifyIdentityInput,
    VerifyIdentityOutput,
)
from supportagent.gateway import identity
from supportagent.gateway.tools import ToolContext, gateway_tool


@gateway_tool(
    "lookup_by_phone", scope="unverified", input_model=LookupByPhoneInput, output_model=LookupByPhoneOutput
)
async def lookup_by_phone(ctx: ToolContext, _: LookupByPhoneInput) -> LookupByPhoneOutput:
    """Whether the line belongs to a known customer. Only outbound calls learn the given name
    (for "May I speak with Frank?"); inbound callers must say who they are."""
    session = ctx.session
    if session.direction == "outbound":
        if session.expected_customer_id is None:
            return LookupByPhoneOutput(match=False)
        customer = await ctx.gateway.bank.get_customer(session.expected_customer_id)
        if customer is None:
            return LookupByPhoneOutput(match=False)
        return LookupByPhoneOutput(match=True, first_name=identity.normalise_first(customer.full_name))
    return LookupByPhoneOutput(match=bool(session.candidate_ids))


@gateway_tool(
    "verify_identity", scope="unverified", input_model=VerifyIdentityInput, output_model=VerifyIdentityOutput
)
async def verify_identity(ctx: ToolContext, claim: VerifyIdentityInput) -> VerifyIdentityOutput:
    result = await identity.verify(ctx.session, claim, ctx.gateway.bank)
    return VerifyIdentityOutput(
        verified=result.verified,
        attempts_remaining=ctx.session.attempts_remaining,
        locked=ctx.session.locked,
        first_name=result.first_name,
    )
