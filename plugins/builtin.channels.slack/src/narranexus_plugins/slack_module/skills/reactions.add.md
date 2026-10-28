# reactions.add

## Description
Adds a reaction to an item.

## Required scope
`reactions:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | yes | Channel where the message to add reaction to was posted. |
| `name` | string | yes | Reaction (emoji) name. |
| `timestamp` | string | yes | Timestamp of the message to add reaction to. |

## Example
```python
slack_cli("reactions.add", {"channel": "...", "name": "...", "timestamp": "..."})
```
