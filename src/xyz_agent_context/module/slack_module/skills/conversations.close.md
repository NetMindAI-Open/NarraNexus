# conversations.close

## Description
Closes a direct message or multi-person direct message.

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Conversation to close. |

## Example
```python
slack_cli("conversations.close", {})
```
