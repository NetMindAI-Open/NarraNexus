"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-07
@description: What ``builtin.job`` contributes to the host, as the objects its manifest names. The plugin owns this table; the platform holds no list of builtins.
"""
from __future__ import annotations

from narranexus.contracts.channel import MessageSourceSpec
from narranexus.contracts.trigger import TriggerSpec
from narranexus.platform.channel.contributions import message_source_contribution, module_contribution, trigger_contribution

PLUGIN_ID = "builtin.job"
MODULES = (module_contribution("narranexus_plugins.job_module.job_module:JobModule", PLUGIN_ID, channel=False),)

# The job clock runs as a worker of the workers supervisor (bare name "jobs": run.sh / compose address it with --only/--exclude).
TRIGGERS = (trigger_contribution(TriggerSpec("jobs", "narranexus_plugins.job_module.job_trigger:JobTrigger", host="workers", kwargs={"poll_interval": 60, "max_workers": 5})),)

# A job-triggered turn is not a channel, so its message source is a standalone
# ``ingress.message_sources`` entry rather than a ChannelDescriptor field. Jobs
# reuse ``notify_owner`` when the agent decides to message the user about an
# outcome; the row prefix is what tells the LLM "this stored row came from a
# scheduled task, not a live UI conversation" so it can weigh follow-ups.
MESSAGE_SOURCES = (
    message_source_contribution(MessageSourceSpec(
        name="job",
        display_label="NarraNexus (scheduled job)",
        reply_tools=("notify_owner",),
        row_prefix_template="[Background Job]",
    )),
)

__all__ = ["MESSAGE_SOURCES", "MODULES", "PLUGIN_ID", "TRIGGERS"]
