# conversations.rename

## Description
Renames a conversation.

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | ID of conversation to rename |
| `name` | string | no | New name for conversation. |

## Example
```python
slack_cli("conversations.rename", {})
```
