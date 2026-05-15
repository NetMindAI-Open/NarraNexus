# admin.teams.admins.list

## Description
List all of the admins on a given workspace.

## Required scope
`admin.teams:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `limit` | integer | no | The maximum number of items to return. |
| `cursor` | string | no | Set `cursor` to `next_cursor` returned by the previous call to list items in the next page. |
| `team_id` | string | yes | — |

## Example
```python
slack_cli("admin.teams.admins.list", {"team_id": "..."})
```
