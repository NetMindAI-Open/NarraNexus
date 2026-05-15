# admin.users.invite

## Description
Invite a user to a workspace.

## Required scope
`admin.users:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | The ID (`T1234`) of the workspace. |
| `email` | string | yes | The email address of the person to invite. |
| `channel_ids` | string | yes | A comma-separated list of `channel_id`s for this user to join. At least one channel is required. |
| `custom_message` | string | no | An optional message to send to the user in the invite email. |
| `real_name` | string | no | Full name of the user. |
| `resend` | boolean | no | Allow this invite to be resent in the future if a user has not signed up yet. (default: false) |
| `is_restricted` | boolean | no | Is this user a multi-channel guest user? (default: false) |
| `is_ultra_restricted` | boolean | no | Is this user a single channel guest user? (default: false) |
| `guest_expiration_ts` | string | no | Timestamp when guest account should be disabled. Only include this timestamp if you are inviting a guest user and you want their account to expire on a certain date. |

## Example
```python
slack_cli("admin.users.invite", {"team_id": "...", "email": "...", "channel_ids": "..."})
```
