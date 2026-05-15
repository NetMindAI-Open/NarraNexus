# users.setPresence

## Description
Manually sets user presence.

## Required scope
`users:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `presence` | string | yes | Either `auto` or `away` |

## Example
```python
slack_cli("users.setPresence", {"presence": "..."})
```
