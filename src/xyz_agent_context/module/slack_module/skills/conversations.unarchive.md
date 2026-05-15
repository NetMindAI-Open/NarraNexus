# conversations.unarchive

## Description
Reverses conversation archival.

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | ID of conversation to unarchive |

## Example
```python
slack_cli("conversations.unarchive", {})
```
