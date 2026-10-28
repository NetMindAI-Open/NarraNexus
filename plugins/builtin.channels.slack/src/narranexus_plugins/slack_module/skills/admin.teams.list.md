# admin.teams.list

## Description
List all teams on an Enterprise organization

## Required scope
`admin.teams:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `limit` | integer | no | The maximum number of items to return. Must be between 1 - 100 both inclusive. |
| `cursor` | string | no | Set `cursor` to `next_cursor` returned by the previous call to list items in the next page. |

## Example
```python
slack_cli("admin.teams.list", {})
```
