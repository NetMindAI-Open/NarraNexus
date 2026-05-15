# admin.teams.settings.setDiscoverability

## Description
An API method that allows admins to set the discoverability of a given workspace

## Required scope
`admin.teams:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | The ID of the workspace to set discoverability on. |
| `discoverability` | string | yes | This workspace's discovery setting. It must be set to one of `open`, `invite_only`, `closed`, or `unlisted`. |

## Example
```python
slack_cli("admin.teams.settings.setDiscoverability", {"team_id": "...", "discoverability": "..."})
```
