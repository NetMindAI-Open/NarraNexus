from narranexus.sdk import hookimpl

SEEN: list[str] = []


@hookimpl("onDidPersistTurn")
async def after_turn(run_id, agent_id):
    # Observe a persisted turn; never block the turn (the host budgets hooks).
    SEEN.append(f"{agent_id}:{run_id}")


HOOKS = (after_turn,)


def activate(ctx):
    ctx.log.info("hooks registered")
