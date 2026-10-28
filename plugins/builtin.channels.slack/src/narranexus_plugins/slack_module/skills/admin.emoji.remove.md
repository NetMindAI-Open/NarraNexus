# admin.emoji.remove

## Description
Remove an emoji across an Enterprise Grid organization

## Required scope
`admin.teams:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `name` | string | yes | The name of the emoji to be removed. Colons (`:myemoji:`) around the value are not required, although they may be included. |

## Example
```python
slack_cli("admin.emoji.remove", {"name": "..."})
```
