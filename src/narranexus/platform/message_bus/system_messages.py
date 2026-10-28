"""
@file_name: system_messages.py
@author: NarraNexus
@date: 2026-08-11
@description: The room lines the PLATFORM writes about itself, in one place.

A team room carries two kinds of message: agents and people talking, and the
platform narrating what it just did — a run was stopped, the bulletin changed, a
patrol swept the board. Almost every consumer that counts, samples or summarises
room activity has to exclude the second kind, and each one had been deciding
that for itself by retyping string literals.

That went wrong exactly the way it always does. `system_stop` and
`system_bulletin` were listed in the summary worker; `patrol` arrived later from
a different feature and nothing told the worker about it, so a filter whose whole
purpose was to stop the platform triggering itself was reopened from the side it
did not know about. The cascade-depth query had the mirror-image gap: it excluded
`patrol` and not the other two.

So the tuple lives here and the consumers import it. Adding a fourth platform
message type is then one edit, and the compiler-free languages of SQL and string
literals stop being the place where the knowledge is kept.

Definitions stay in their own features (`patrol.py`, `team_bulletin.py`); this
module only assembles them, so adding a type does not mean moving it.
"""

from __future__ import annotations

from narranexus.platform.message_bus.delivery_notice import (
    DELIVERY_FAILED_MSG_TYPE,
    UNDELIVERED_MSG_TYPE,
)
from narranexus.platform.message_bus.patrol import PATROL_MSG_TYPE
from narranexus.platform.message_bus.team_notices import (
    CASCADE_MSG_TYPE,
    ROSTER_MSG_TYPE,
)
from narranexus.platform.message_bus.team_bulletin import (
    BULLETIN_NOTICE_MSG_TYPE,
    STOP_NOTICE_MSG_TYPE,
)

# Every msg_type the platform writes about itself. NOT team activity: none of
# them is an agent taking a turn or a person speaking.
PLATFORM_MSG_TYPES = (
    BULLETIN_NOTICE_MSG_TYPE,
    CASCADE_MSG_TYPE,
    DELIVERY_FAILED_MSG_TYPE,
    PATROL_MSG_TYPE,
    ROSTER_MSG_TYPE,
    STOP_NOTICE_MSG_TYPE,
    UNDELIVERED_MSG_TYPE,
)


#: What a platform line is CALLED where an agent reads one.
#:
#: Two surfaces render these rows and both must name them the same way: the
#: team prompt's scrollback (`message_bus_trigger._build_team_prompt`, as a
#: line prefix) and the module's unread list (`message_bus_module._bus_tag`, as
#: the sender field). They were independent literals until 2026-08-17, with the
#: agreement asserted only in a comment — so a rename on one side would have
#: left the SAME notice carrying two different names in one context window, and
#: the rule that teaches an agent what the label means ("not a teammate, not
#: someone to answer or @mention") reads only one of them.
#:
#: Lives here for the reason this whole module exists, stated in the header
#: above: the compiler-free language of string literals stops being the place
#: where the knowledge is kept. The two call sites keep their own FORMATS —
#: prefix vs field — and share only the name.
SYSTEM_SENDER_LABEL = "[system]"


# How each platform line should be introduced when an agent is told to respond
# to one. Keyed by type rather than hard-coded at the call site, which had
# assumed patrol is the only platform message that can become a trigger — true
# until 2026-08-13, when the undelivered notice became the second: an A2A one
# mentions the peer that asked, precisely so a blocked errand wakes up instead
# of waiting forever. The dispatch table was written for exactly this day.
_TRIGGER_LABELS = {
    PATROL_MSG_TYPE: "the team's Leader check",
    UNDELIVERED_MSG_TYPE: "a platform notice that the agent you contacted "
                          "ended its turn without replying",
    # Third trigger-capable platform line (2026-09-09): the recipient could not
    # RUN the message (poison threshold) and the sender is woken to hear it.
    DELIVERY_FAILED_MSG_TYPE: "a platform notice that the agent you contacted "
                              "could not process your message and it was dropped",
}
# Neutral fallback: a new platform type gets a truthful vague label rather than
# a synthetic `team_<id>` marker printed as if it were a teammate.
_TRIGGER_FALLBACK = "a platform notice"


def trigger_label(msg_type: str) -> str:
    """How to name a platform line an agent is being told to answer.

    Never the raw sender: a patrol line's `from_agent` is the synthetic
    `team_<id>` marker, which resolves to no member, so printing it invents a
    teammate the agent may then try to @mention back.
    """
    return _TRIGGER_LABELS.get(msg_type or "", _TRIGGER_FALLBACK)


def placeholders(ph: str = "%s") -> str:
    """`%s, %s, %s` sized to the tuple.

    So a query says ``NOT IN ({placeholders()})`` instead of hard-coding a
    count. Three call sites previously hard-coded ``(%s, %s)``, which is one
    more thing to remember to change and one more place to get it wrong.

    ``ph`` is a parameter because one caller reaches the backend's own
    placeholder style rather than the client's ``%s`` normalisation.
    """
    return ", ".join([ph] * len(PLATFORM_MSG_TYPES))


__all__ = [
    "PLATFORM_MSG_TYPES",
    "SYSTEM_SENDER_LABEL",
    "placeholders",
    "trigger_label",
]
