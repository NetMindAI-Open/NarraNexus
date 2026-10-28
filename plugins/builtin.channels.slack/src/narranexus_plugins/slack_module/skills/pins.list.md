# pins.list

## Description
Lists items pinned to a channel.

## Required scope
`pins:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | yes | Channel to get pinned items for. |

## Example
```python
slack_cli("pins.list", {"channel": "..."})
```
