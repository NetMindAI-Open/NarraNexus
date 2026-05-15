# admin.emoji.addAlias

## Description
Add an emoji alias.

## Required scope
`admin.teams:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `name` | string | yes | The name of the emoji to be aliased. Colons (`:myemoji:`) around the value are not required, although they may be included. |
| `alias_for` | string | yes | The alias of the emoji. |

## Example
```python
slack_cli("admin.emoji.addAlias", {"name": "...", "alias_for": "..."})
```
