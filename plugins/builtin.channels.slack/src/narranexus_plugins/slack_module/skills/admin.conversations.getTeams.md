# admin.conversations.getTeams

## Description
Get all the workspaces a given public or private channel is connected to within this Enterprise org.

## Required scope
`admin.conversations:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_id` | string | yes | The channel to determine connected workspaces within the organization for. |
| `cursor` | string | no | Set `cursor` to `next_cursor` returned by the previous call to list items in the next page |
| `limit` | integer | no | The maximum number of items to return. Must be between 1 - 1000 both inclusive. |

## Example
```python
slack_cli("admin.conversations.getTeams", {"channel_id": "..."})
```
