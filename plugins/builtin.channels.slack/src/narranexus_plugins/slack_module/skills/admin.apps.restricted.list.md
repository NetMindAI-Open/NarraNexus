# admin.apps.restricted.list

## Description
List restricted apps for an org or workspace.

## Required scope
`admin.apps:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `limit` | integer | no | The maximum number of items to return. Must be between 1 - 1000 both inclusive. |
| `cursor` | string | no | Set `cursor` to `next_cursor` returned by the previous call to list items in the next page |
| `team_id` | string | no | — |
| `enterprise_id` | string | no | — |

## Example
```python
slack_cli("admin.apps.restricted.list", {})
```
