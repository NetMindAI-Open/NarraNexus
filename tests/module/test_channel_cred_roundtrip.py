"""
@file_name: test_channel_cred_roundtrip.py
@author:
@date: 2026-08-11
@description: Per-channel to_raw_dict <-> _cred_from_raw round-trip (blueprint
P2, #2). The seam's Direct<->Http parity (test_channel_store.py) proves the
DISPATCH; this proves each channel's own raw serialisation is a faithful inverse
so an HttpStore-backed lookup rebuilds byte-identical to the DirectStore object
— including the secret field(s), which to_public_dict deliberately drops.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from narranexus.platform.module_system.data_access.channel_store import (
    SUPPORTED_CHANNELS,
    _read_method_name,
)

_DT = datetime(2026, 8, 11, 3, 4, 5, tzinfo=timezone.utc)


def _cases():
    from narranexus_plugins.discord_module._discord_credential_manager import (
        DiscordCredential,
        _cred_from_raw as discord_from_raw,
    )
    from narranexus_plugins.slack_module._slack_credential_manager import (
        SlackCredential,
        _cred_from_raw as slack_from_raw,
    )
    from narranexus_plugins.telegram_module._telegram_credential_manager import (
        TelegramCredential,
        _cred_from_raw as telegram_from_raw,
    )
    from narranexus_plugins.wechat_module._wechat_credential_manager import (
        WeChatCredential,
        _cred_from_raw as wechat_from_raw,
    )
    from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import (
        NarramessengerCredential,
        _cred_from_raw as narra_from_raw,
    )
    from narranexus_plugins.lark_module._lark_credential_manager import (
        LarkCredential,
        _cred_from_raw as lark_from_raw,
    )
    from narranexus_plugins.home_assistant_module._home_assistant_impl.binding import (
        _HABindingCred,
        _cred_from_raw as ha_from_raw,
    )

    # EVERY field set to a distinctive NON-default value (incl. enabled/is_active
    # = False), so the round-trip below catches a field dropped by EITHER
    # to_raw_dict OR _cred_from_raw — not just the handful a partial fixture
    # happens to set (pre-review: silent field loss when you add a field to
    # to_raw_dict but forget _cred_from_raw).
    return [
        ("discord", DiscordCredential(
            agent_id="agent_x", bot_token="d-secret", bot_user_id="1",
            bot_username="Scout", owner_user_id="o", owner_name="Al",
            enabled=False, created_at=_DT, updated_at=_DT),
         discord_from_raw, ["bot_token"]),
        ("slack", SlackCredential(
            agent_id="agent_x", bot_token="s-bot", app_token="s-app",
            bot_user_id="U1", team_id="T1", team_name="Team", owner_email="a@b.c",
            owner_user_id="o", owner_name="Al", enabled=False,
            created_at=_DT, updated_at=_DT),
         slack_from_raw, ["bot_token", "app_token"]),
        ("telegram", TelegramCredential(
            agent_id="agent_x", bot_token="t-secret", bot_user_id="9", bot_username="bot",
            owner_username="@al", owner_user_id="o", owner_name="Al", enabled=False,
            created_at=_DT, updated_at=_DT),
         telegram_from_raw, ["bot_token"]),
        ("wechat", WeChatCredential(
            agent_id="agent_x", bot_token="w-secret", base_url="https://x",
            bot_wx_id="wx", owner_wx_id="ow", owner_user_id="o", owner_name="Al",
            enabled=False, created_at=_DT, updated_at=_DT),
         wechat_from_raw, ["bot_token"]),
        ("narramessenger", NarramessengerCredential(
            agent_id="agent_x", bearer_token="n-bearer", backend_base_url="https://b",
            matrix_homeserver_url="https://m", matrix_user_id="@u:m",
            nexus_principal_id="p", nexus_profile_id="pf", bind_room_id="!r:m",
            owner_matrix_user_id="@o:m", owner_name="Al", connection_mode="direct",
            enabled=False, matrix_access_token="syt_secret", matrix_device_id="DEV",
            matrix_since_token="s_123", created_at=_DT, updated_at=_DT),
         narra_from_raw, ["bearer_token", "matrix_access_token"]),
        ("lark", LarkCredential(
            agent_id="agent_x", app_id="cli_x", app_secret_ref="appsecret:cli_x",
            brand="feishu", profile_name="agent_agent_x", workspace_path="/ws",
            bot_name="Bot", bot_open_id="ou_x", app_secret_encoded="YmFzZTY0c2VjcmV0",
            owner_open_id="ou_o", owner_name="Al", auth_status="user_logged_in",
            is_active=False,
            permission_state={"user_oauth_completed_at": "2026-08-11T00:00:00"},
            created_at=_DT, updated_at=_DT),
         lark_from_raw, ["app_secret_encoded"]),
        # Home Assistant: the secret (the Long-Lived Access Token) rides INSIDE
        # config_json — the raw dict carries the blob verbatim.
        ("home_assistant", _HABindingCred(
            config_json='{"base_url": "https://ha", "token": "llat-secret", "verify_tls": true}'),
         ha_from_raw, ["config_json"]),
    ]


@pytest.mark.parametrize("channel,cred,from_raw,secret_fields", _cases())
def test_raw_dict_roundtrips_and_carries_the_secret(channel, cred, from_raw, secret_fields):
    raw = cred.to_raw_dict()
    # the secret(s) the send tools need are present in the raw dict...
    for f in secret_fields:
        assert raw[f] == getattr(cred, f) and raw[f]
    # ...and NOT in the sanitised view (that is the whole point of two methods).
    # Lark has no to_public_dict (its dataclass is treated as internal), so the
    # "absent from public" invariant only applies where a sanitised view exists.
    if hasattr(cred, "to_public_dict"):
        public = cred.to_public_dict()
        for f in secret_fields:
            assert f not in public
    # inverse rebuilds an identical dataclass (datetimes survive ISO round-trip)
    assert from_raw(raw) == cred


def test_every_supported_channel_has_a_read_method():
    for channel in SUPPORTED_CHANNELS:
        assert _read_method_name(channel) in {"get", "get_credential"}


@pytest.mark.parametrize("channel,cred,from_raw,secret_fields", _cases())
def test_to_raw_dict_covers_every_dataclass_field(channel, cred, from_raw, secret_fields):
    # Belt-and-suspenders on top of the round-trip: to_raw_dict must emit a key
    # for EVERY dataclass field — a dropped key is silent data loss on the cloud
    # (HttpStore) path. _HABindingCred (config_json blob) is a dataclass too.
    import dataclasses

    raw_keys = set(cred.to_raw_dict().keys())
    field_names = {f.name for f in dataclasses.fields(cred)}
    missing = field_names - raw_keys
    assert not missing, f"{channel}: to_raw_dict drops {sorted(missing)}"
