# conversations.mark

## Description
Sets the read cursor in a channel.

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Channel or conversation to set the read cursor for. |
| `ts` | number | no | Unique identifier of message you want marked as most recently seen in this conversation. |

## Example
```python
slack_cli("conversations.mark", {})
```
