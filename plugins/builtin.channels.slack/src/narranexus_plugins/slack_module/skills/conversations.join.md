# conversations.join

## Description
Joins an existing conversation.

## Required scope
`channels:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | ID of conversation to join |

## Example
```python
slack_cli("conversations.join", {})
```
