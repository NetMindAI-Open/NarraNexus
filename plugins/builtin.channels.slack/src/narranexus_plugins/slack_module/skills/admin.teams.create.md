# admin.teams.create

## Description
Create an Enterprise team.

## Required scope
`admin.teams:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_domain` | string | yes | Team domain (for example, slacksoftballteam). |
| `team_name` | string | yes | Team name (for example, Slack Softball Team). |
| `team_description` | string | no | Description for the team. |
| `team_discoverability` | string | no | Who can join the team. A team's discoverability can be `open`, `closed`, `invite_only`, or `unlisted`. |

## Example
```python
slack_cli("admin.teams.create", {"team_domain": "...", "team_name": "..."})
```
