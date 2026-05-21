## Summary

<!-- 1-3 sentences explaining what this PR does and why. -->

## Changes

<!-- The main changes, grouped logically. -->

-

## Verification

<!-- Check the items you completed. CI will re-run them; this is your local sanity check. -->

- [ ] `make lint && make typecheck` pass locally
- [ ] Import check: `uv run python -c "import xyz_agent_context.module; import xyz_agent_context.narrative; import xyz_agent_context.services; print('OK')"`
- [ ] Frontend builds (if frontend changed): `cd frontend && npm run build`
- [ ] Schema dry-run passes (if schema changed): `make db-sync-dry`
- [ ] No secrets committed (`.env`, API keys, tokens, credentials)

## Tier-2 doc sync (CLAUDE.md rule #10)

<!--
Did you change a .py / .ts / .tsx / .rs file? If yes, the matching
.mindflow/mirror/<path>.md needs to be updated in the same commit:
  - new source file -> new mirror md (mark frontmatter stub: false)
  - changed behavior -> rewrite the intent paragraph + refresh last_verified
  - deleted source file -> delete the mirror md

Your AI assistant (Claude Code / Cursor / etc.) handles this if you
loaded CLAUDE.md into its context. If you're short on time, add
`Mirror-md: needs-maintainer` below and a maintainer will handle it.
-->

- [ ] Mirror md updated in this PR
- [ ] OR — Mirror-md: needs-maintainer (paste this line in the description if you want a maintainer to handle the mirror sync)
- [ ] N/A — this PR doesn't touch any `.py / .ts / .tsx / .rs` files

## Related

<!-- Issues this closes or references, e.g. Closes #123 -->
