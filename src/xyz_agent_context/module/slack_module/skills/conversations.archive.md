# conversations.archive

## Description
Archives a conversation.

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | ID of conversation to archive |

## Example
```python
slack_cli("conversations.archive", {})
```
