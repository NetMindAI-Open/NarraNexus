# admin.emoji.list

## Description
List emoji for an Enterprise Grid organization.

## Required scope
`admin.teams:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `cursor` | string | no | Set `cursor` to `next_cursor` returned by the previous call to list items in the next page |
| `limit` | integer | no | The maximum number of items to return. Must be between 1 - 1000 both inclusive. |

## Example
```python
slack_cli("admin.emoji.list", {})
```
