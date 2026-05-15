# admin.conversations.invite

## Description
Invite a user to a public or private channel.

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `user_ids` | string | yes | The users to invite. |
| `channel_id` | string | yes | The channel that the users will be invited to. |

## Example
```python
slack_cli("admin.conversations.invite", {"user_ids": "...", "channel_id": "..."})
```
