# conversations.setPurpose

## Description
Sets the purpose for a conversation.

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Conversation to set the purpose of |
| `purpose` | string | no | A new, specialer purpose |

## Example
```python
slack_cli("conversations.setPurpose", {})
```
