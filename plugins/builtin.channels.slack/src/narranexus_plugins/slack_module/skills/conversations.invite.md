# conversations.invite

## Description
Invites users to a channel.

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | The ID of the public or private channel to invite user(s) to. |
| `users` | string | no | A comma separated list of user IDs. Up to 1000 users may be listed. |

## Example
```python
slack_cli("conversations.invite", {})
```
