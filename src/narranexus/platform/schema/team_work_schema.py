"""
@file_name: team_work_schema.py
@author:
@date: 2026-08-07
@description: Team work board — the first task-level object in the product.

Everything before this recorded CONVERSATION (messages, runs, errands): a task
existed only inside the turn discussing it, so a Leader's assignment died with
its own run and nobody could notice it stalled. A work item is a task that
outlives the run that created it.

Layered with, not merged into, the errand object (owner decision 2026-08-07):
an errand is a MESSAGE-level fact recorded automatically (A @ B opens it, B's
reply closes it); a work item is a TASK-level object maintained explicitly
through tools, and routinely spans several errands.
"""

from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class WorkItemStatus:
    """The board's state machine.

    Two of these are NOT the model's to write:

    * ``STALLED`` is derived by the platform from ``bus_agent_activity`` plus
      errand timeouts. Iron rule #15 — a correctness-critical fact must not
      depend on model obedience. The lead's judgement applies to what to DO
      about a stalled item, never to whether it is stalled.
    * ``PAUSED`` is what a stop leaves behind. Stopping a run tree stops the
      RUNNING, not the task; without this state the item stays ``OPEN`` and the
      next patrol dutifully revives exactly what the owner just stopped.
      Resuming is an explicit user action.
    """

    OPEN = "open"
    IN_PROGRESS = "in_progress"
    STALLED = "stalled"
    DONE = "done"
    PAUSED = "paused"
    CANCELLED = "cancelled"

    #: Items patrol should still care about. `paused` is absent on purpose —
    #: that is the whole point of the state — and so are the terminal ones.
    ACTIVE = (OPEN, IN_PROGRESS, STALLED)

    #: What a model may set through the tools. `stalled` and `paused` are
    #: platform-written; `cancelled` is the user's call, not an agent's.
    MODEL_SETTABLE = (OPEN, IN_PROGRESS, DONE)


class WorkItemOrigin:
    """Which of the two layers put this row on the board.

    The layers were kept separate by owner decision (2026-08-07) and this
    column is where that decision is enforced at runtime:

    * ``TOOL`` — a TASK the Leader maintains explicitly through
      ``team_work_add`` / ``team_work_complete``. One task routinely spans
      several errands, so nothing may close it automatically.
    * ``AUTO`` — a message-level ERRAND recorded by the platform when one
      agent @mentions another (``message_bus/errand.py``). It closes itself
      when the assignee delivers, which is what makes automatic opening safe
      to do at all.
    """

    TOOL = "tool"
    AUTO = "auto"


class WorkItem(BaseModel):
    id: Optional[int] = None
    item_id: str
    team_id: str
    channel_id: str
    title: str
    #: None = unclaimed. Patrol reads this to tell "nobody picked it up" from
    #: "somebody has it and went quiet" — two different prompts.
    assignee_id: Optional[str] = None
    status: str = WorkItemStatus.OPEN
    created_by: str = ""
    source_message_id: Optional[str] = None
    #: Which layer created this row — see :class:`WorkItemOrigin`. Defaults to
    #: ``tool`` so every row written before the errand layer existed reads as
    #: what it actually was: something a model asked for explicitly.
    origin: str = WorkItemOrigin.TOOL
    #: The trigger tree running when this item was created, so a cascade stop
    #: can pause what it silenced (shipped by #252).
    root_run_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
