"""Remembers the customer's client ID in ADK session state, shared by the Temporal and
standalone versions of the agents."""

CLIENT_ID_STATE_KEY = "client_id"


def remember_client_id(tool, args: dict, tool_context) -> None:
    """before_tool_callback: save the client ID in session state the first time a tool receives it.

    Every agent's instructions read it back through the {client_id?} placeholder, so the
    customer is asked for it once, no matter how many agent handoffs follow.
    """
    client_id = args.get("client_id")
    if client_id and tool_context.state.get(CLIENT_ID_STATE_KEY) != client_id:
        tool_context.state[CLIENT_ID_STATE_KEY] = client_id
    return None
