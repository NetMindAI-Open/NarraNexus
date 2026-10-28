# admin.users.list

## Description
List users on a workspace

## Required scope
`admin.users:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | The ID (`T1234`) of the workspace. |
| `cursor` | string | no | Set `cursor` to `next_cursor` returned by the previous call to list items in the next page. |
| `limit` | integer | no | Limit for how many users to be retrieved per page |

## Example
```python
slack_cli("admin.users.list", {"team_id": "..."})
```
