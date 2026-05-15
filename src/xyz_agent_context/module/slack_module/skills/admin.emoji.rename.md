# admin.emoji.rename

## Description
Rename an emoji.

## Required scope
`admin.teams:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `name` | string | yes | The name of the emoji to be renamed. Colons (`:myemoji:`) around the value are not required, although they may be included. |
| `new_name` | string | yes | The new name of the emoji. |

## Example
```python
slack_cli("admin.emoji.rename", {"name": "...", "new_name": "..."})
```
