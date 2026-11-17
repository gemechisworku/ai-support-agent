"""The conversation states (one module each, decorated with `@register_state`).

Importing this package imports every state, which registers it for the static allow-list check
(`supportagent.agent.base.check_registered_states`). T-AGENT-05..12 add their states here; `flow.py` has the
names states use to hand off to each other.
"""

from supportagent.agent.states import closing, flow, greeting, policy, router, third_party, verify

__all__ = ["closing", "flow", "greeting", "policy", "router", "third_party", "verify"]
