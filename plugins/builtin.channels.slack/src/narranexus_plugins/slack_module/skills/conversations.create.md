# conversations.create

## Description
Initiates a public or private channel-based conversation

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `name` | string | no | Name of the public or private channel to create |
| `is_private` | boolean | no | Create a private channel instead of a public one |

## Example
```python
slack_cli("conversations.create", {})
```
