# bots.info

## Description
Gets information about a bot user.

## Required scope
`users:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `bot` | string | no | Bot user to get info on |

## Example
```python
slack_cli("bots.info", {})
```
