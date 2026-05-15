# admin.teams.owners.list

## Description
List all of the owners on a given workspace.

## Required scope
`admin.teams:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | — |
| `limit` | integer | no | The maximum number of items to return. Must be between 1 - 1000 both inclusive. |
| `cursor` | string | no | Set `cursor` to `next_cursor` returned by the previous call to list items in the next page. |

## Example
```python
slack_cli("admin.teams.owners.list", {"team_id": "..."})
```
