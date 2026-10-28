# conversations.kick

## Description
Removes a user from a conversation.

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | ID of conversation to remove user from. |
| `user` | string | no | User ID to be removed. |

## Example
```python
slack_cli("conversations.kick", {})
```
