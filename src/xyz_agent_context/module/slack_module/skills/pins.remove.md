# pins.remove

## Description
Un-pins an item from a channel.

## Required scope
`pins:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | yes | Channel where the item is pinned to. |
| `timestamp` | string | no | Timestamp of the message to un-pin. |

## Example
```python
slack_cli("pins.remove", {"channel": "..."})
```
