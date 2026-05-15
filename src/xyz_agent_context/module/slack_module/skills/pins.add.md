# pins.add

## Description
Pins an item to a channel.

## Required scope
`pins:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | yes | Channel to pin the item in. |
| `timestamp` | string | no | Timestamp of the message to pin. |

## Example
```python
slack_cli("pins.add", {"channel": "..."})
```
