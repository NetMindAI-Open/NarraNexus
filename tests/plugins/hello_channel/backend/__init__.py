"""Hello Channel — an IM channel as a plugin (acme.hello_channel).

Everything a channel is, in one plugin: the descriptor (credential schema,
webhook transport, UI row), the trigger (webhook-fed, parses the platform's
event shape into ParsedMessage) and the module (the agent's send tool).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from narranexus.contracts.channel import ChannelDescriptor, ChannelUi, CredentialField, CredentialSchema
from narranexus.contracts.trigger import TriggerSpec
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.channel.channel_context_builder_base import ChannelContextBuilderBase
from narranexus.platform.channel.channel_module_base import ChannelModuleBase
from narranexus.platform.channel.webhook_transport import WebhookChannelTriggerBase
from narranexus.platform.schema.hook_schema import WorkingSource
from narranexus.platform.schema.module_schema import ModuleConfig
from narranexus.platform.schema.parsed_message import ChatType, MessageContentType, ParsedMessage

CHANNEL_NAME = "hello_channel"
HELLO_SOURCE = WorkingSource.register(CHANNEL_NAME)

DESCRIPTOR = ChannelDescriptor(
    name=CHANNEL_NAME,
    display_name="Hello Channel",
    transport="webhook",
    credential_schema=CredentialSchema(
        fields=(
            CredentialField("api_token", "secret", label="API token", required=True),
            CredentialField("bot_id", "string", label="Bot id", required=True),
            CredentialField("workspace", "string", label="Workspace", required=False),
        ),
        supports_test=False,
        external_id_field="bot_id",
    ),
    trigger_ref="nxplugins.acme_hello_channel:HelloChannelTrigger",
    module_ref="nxplugins.acme_hello_channel:HelloChannelModule",
    has_bind=True,
    has_test=False,
    ui=ChannelUi(label="Hello Channel", icon="message-square", order=90),
)

SENT: list[dict[str, Any]] = []  # what the module "sent" (the demo platform is this list)


class HelloContextBuilder(ChannelContextBuilderBase):
    def __init__(self, message: ParsedMessage, credential: Any, agent_id: str) -> None:
        self._message = message
        self._credential = credential
        self._agent_id = agent_id

    async def get_message_info(self) -> Dict[str, Any]:
        return {
            "agent_id": self._agent_id,
            "channel_display_name": "Hello Channel",
            "channel_key": CHANNEL_NAME,
            "room_name": self._message.chat_id,
            "room_id": self._message.chat_id,
            "room_type": "Direct Message" if self._message.chat_type == ChatType.PRIVATE else "Group Room",
            "sender_display_name": self._message.sender_name,
            "sender_id": self._message.sender_id,
            "timestamp": str(self._message.timestamp_ms),
            "my_channel_id": getattr(self._credential, "external_id", "") or "",
            "message_body": self._message.content,
            "send_tool_name": "hello_send",
        }

    async def get_conversation_history(self, limit: int) -> List[Dict[str, Any]]:
        return []

    async def get_room_members(self) -> List[Dict[str, Any]]:
        return []


class HelloChannelTrigger(WebhookChannelTriggerBase):
    channel_name = CHANNEL_NAME
    brand_display = "Hello Channel"
    working_source = HELLO_SOURCE
    WEBHOOK_POLL_SECONDS = 0.05
    CREDENTIAL_POLL_INTERVAL_SECONDS = 1
    IDLE_POLL_INTERVAL_SECONDS = 1

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
        return HelloContextBuilder(message, credential, agent_id)


class HelloChannelModule(ChannelModuleBase):
    channel_name = CHANNEL_NAME
    brand_display = "Hello Channel"
    working_source = HELLO_SOURCE
    ctx_data_key = "hello_channel_info"
    mcp_server_name = "hello_channel"
    all_tool_names = ("hello_send",)
    reply_tool_names = ("hello_send",)

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(name="HelloChannelModule", priority=9, enabled=True, description="Hello Channel (plugin channel demo).", module_type="capability")

    async def get_credential(self, agent_id: str) -> Optional[Any]:
        from narranexus.platform.channel.credential_store import GenericCredentialStore

        return await GenericCredentialStore(self.database_client).get(CHANNEL_NAME, agent_id)

    async def send_to_agent(self, agent_id: str, target_id: str, message: str, **kwargs: Any) -> dict:
        SENT.append({"agent_id": agent_id, "target_id": target_id, "message": message})
        return {"success": True, "delivered": True}

    def register_mcp_tools(self, mcp) -> None:
        module = self

        @mcp.tool(name="hello_send")
        async def hello_send(chat_id: str, text: str) -> dict:
            return await module.send_to_agent(module.agent_id, chat_id, text)

    async def contribute_instructions(self, ctx_data: Any) -> str:
        return "Reply with `hello_send(chat_id, text)` — exactly one message."

    async def build_extra_data(self, cred: Any, ctx_data: Any) -> dict:
        return {"bot_id": getattr(cred, "external_id", None)}


CHANNEL = (Contribution(CHANNEL_NAME, lambda: DESCRIPTOR),)
TRIGGERS = (Contribution(CHANNEL_NAME, lambda: TriggerSpec(CHANNEL_NAME, "nxplugins.acme_hello_channel:HelloChannelTrigger")),)
MODULES = (Contribution("HelloChannelModule", lambda: HelloChannelModule, meta={"plugin_id": "acme.hello_channel", "channel": True}),)

__all__ = ["CHANNEL", "DESCRIPTOR", "HELLO_SOURCE", "HelloChannelModule", "HelloChannelTrigger", "MODULES", "SENT", "TRIGGERS"]
