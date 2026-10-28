"""An IM channel as a plugin: the descriptor (credential schema, webhook
transport, UI row), the trigger (webhook-fed; parses the platform's event
shape into ParsedMessage) and the module (the agent's send tool).

Everything comes from ``narranexus.sdk`` — the ONE import root a plugin is
allowed. The channel base classes used to be scaffolded as
``narranexus.platform.*`` imports: seven of this file's eight imports pointed
at modules ``docs/API_POLICY.md`` §1 says are not a public API and that the
griffe gate (which diffs ``contracts`` + ``sdk``) never looks at, so a patch
release could break every plugin written from this template with nothing in
the release notes. They are graded ``channel_authoring`` = alpha (API_POLICY
§2): still moving, but now visibly."""
from typing import Any, Dict, List, Optional

from narranexus.sdk import (
    ChannelContextBuilderBase,
    ChannelDescriptor,
    ChannelModuleBase,
    ChannelUi,
    ChatType,
    Contribution,
    CredentialField,
    CredentialSchema,
    GenericCredentialStore,
    MessageContentType,
    ModuleConfig,
    ParsedMessage,
    TriggerSpec,
    WebhookChannelTriggerBase,
    WorkingSource,
)

CHANNEL_NAME = "__PLUGIN_PKG__"
# The channel's working source — registered by the channel itself; the platform holds
# no channel-name table. It has to happen before the class bodies below, which read
# ``SOURCE`` at DEFINITION time; the host registers the same value idempotently when it
# imports this module's contributions at boot.
SOURCE = WorkingSource.register(CHANNEL_NAME)

DESCRIPTOR = ChannelDescriptor(
    name=CHANNEL_NAME,
    display_name="__DISPLAY_NAME__",
    transport="webhook",  # inbound events arrive at the host's webhook route for this channel
    credential_schema=CredentialSchema(
        fields=(
            CredentialField("api_token", "secret", label="API token", required=True),
            CredentialField("bot_id", "string", label="Bot id", required=True),
        ),
        supports_test=False,
        external_id_field="bot_id",
    ),
    trigger_ref="nxplugins.__PLUGIN_PKG__:__PLUGIN_PKG___Trigger",
    module_ref="nxplugins.__PLUGIN_PKG__:__PLUGIN_PKG___Module",
    has_bind=True,
    has_test=False,
    # The channel's MESSAGE SOURCE, declared here and nowhere else: which tool calls
    # count as "the agent answered whoever wrote in" and how a stored row of this
    # channel is labelled to the LLM. Get this wrong (or omit it) and a delivered
    # reply is recorded as NO-REPLY and the row reads as if it came from the
    # NarraNexus web UI. ``reply_extractor_ref`` is optional and lazy — point it at a
    # ``(tool_name, arguments) -> str | None`` function only when the reply text is
    # not in an ``arguments["content"]`` field.
    reply_tools=("__PLUGIN_PKG___send",),
    row_prefix_template="[__DISPLAY_NAME__ · {sender_name} · {chat_id}]",
    dedicated_trigger=False,  # True only if this channel runs its own long-lived trigger process
    ui=ChannelUi(label="__DISPLAY_NAME__", icon="message-square", order=90),
)

SENT: list[dict[str, Any]] = []  # replace with the real platform client


class _ContextBuilder(ChannelContextBuilderBase):
    def __init__(self, message: ParsedMessage, credential: Any, agent_id: str) -> None:
        self._message = message
        self._credential = credential
        self._agent_id = agent_id

    async def get_message_info(self) -> Dict[str, Any]:
        return {
            "agent_id": self._agent_id,
            "channel_display_name": "__DISPLAY_NAME__",
            "channel_key": CHANNEL_NAME,
            "room_name": self._message.chat_id,
            "room_id": self._message.chat_id,
            "room_type": "Direct Message" if self._message.chat_type == ChatType.PRIVATE else "Group Room",
            "sender_display_name": self._message.sender_name,
            "sender_id": self._message.sender_id,
            "timestamp": str(self._message.timestamp_ms),
            "my_channel_id": getattr(self._credential, "external_id", "") or "",
            "message_body": self._message.content,
            "send_tool_name": "__PLUGIN_PKG___send",
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        return []

    async def get_room_members(self) -> List[Dict[str, Any]]:
        return []


class __PLUGIN_PKG___Trigger(WebhookChannelTriggerBase):
    channel_name = CHANNEL_NAME
    brand_display = "__DISPLAY_NAME__"
    working_source = SOURCE

    def parse_event(self, raw: dict) -> Optional[ParsedMessage]:
        text = str(raw.get("text") or "").strip()
        if not text or not raw.get("message_id"):
            return None
        return ParsedMessage(
            message_id=str(raw["message_id"]),
            chat_id=str(raw.get("chat_id") or "dm"),
            sender_id=str(raw.get("sender_id") or "someone"),
            sender_name=str(raw.get("sender_name") or "Someone"),
            content=text,
            content_type=MessageContentType.TEXT,
            chat_type=ChatType.GROUP if raw.get("group") else ChatType.PRIVATE,
            timestamp_ms=int(raw.get("ts_ms") or 0),
            raw=raw,
        )

    async def is_echo(self, message: ParsedMessage, credential: Any) -> bool:
        return message.sender_id == (getattr(credential, "external_id", "") or "")

    async def resolve_sender_name(self, sender_id: str, credential: Any) -> str:
        return sender_id

    def create_context_builder(self, message: ParsedMessage, credential: Any, agent_id: str) -> ChannelContextBuilderBase:
        return _ContextBuilder(message, credential, agent_id)


class __PLUGIN_PKG___Module(ChannelModuleBase):
    channel_name = CHANNEL_NAME
    brand_display = "__DISPLAY_NAME__"
    working_source = SOURCE
    ctx_data_key = "__PLUGIN_PKG___info"
    mcp_server_name = "__PLUGIN_PKG__"
    all_tool_names = ("__PLUGIN_PKG___send",)
    reply_tool_names = ("__PLUGIN_PKG___send",)

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(name="__PLUGIN_PKG___Module", priority=9, enabled=True, description="__DISPLAY_NAME__ channel.", module_type="capability")

    async def get_credential(self, agent_id: str) -> Optional[Any]:
        return await GenericCredentialStore(self.database_client).get(CHANNEL_NAME, agent_id)

    async def send_to_agent(self, agent_id: str, target_id: str, message: str, **kwargs: Any) -> dict:
        SENT.append({"agent_id": agent_id, "target_id": target_id, "message": message})
        return {"success": True, "delivered": True}

    def register_mcp_tools(self, mcp) -> None:
        module = self

        @mcp.tool(name="__PLUGIN_PKG___send")
        async def send(chat_id: str, text: str) -> dict:
            return await module.send_to_agent(module.agent_id, chat_id, text)

    async def contribute_instructions(self, ctx_data: Any) -> str:
        return "Reply with `__PLUGIN_PKG___send(chat_id, text)` — exactly one message."

    async def build_extra_data(self, cred: Any, ctx_data: Any) -> dict:
        return {"bot_id": getattr(cred, "external_id", None)}


CHANNEL = (Contribution(CHANNEL_NAME, lambda: DESCRIPTOR),)
TRIGGERS = (Contribution(CHANNEL_NAME, lambda: TriggerSpec(CHANNEL_NAME, DESCRIPTOR.trigger_ref)),)
MODULES = (Contribution("__PLUGIN_PKG___Module", lambda: __PLUGIN_PKG___Module, meta={"plugin_id": "__PLUGIN_ID__", "channel": True}),)
