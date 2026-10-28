# conversations.leave

## Description
Leaves a conversation.

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Conversation to leave |

## Example
```python
slack_cli("conversations.leave", {})
```
