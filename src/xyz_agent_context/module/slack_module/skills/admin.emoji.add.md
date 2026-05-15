# admin.emoji.add

## Description
Add an emoji.

## Required scope
`admin.teams:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `name` | string | yes | The name of the emoji to be removed. Colons (`:myemoji:`) around the value are not required, although they may be included. |
| `url` | string | yes | The URL of a file to use as an image for the emoji. Square images under 128KB and with transparent backgrounds work best. |

## Example
```python
slack_cli("admin.emoji.add", {"name": "...", "url": "..."})
```
