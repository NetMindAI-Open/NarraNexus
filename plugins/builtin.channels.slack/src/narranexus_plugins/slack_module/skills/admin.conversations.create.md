# admin.conversations.create

## Description
Create a public or private channel-based conversation.

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `name` | string | yes | Name of the public or private channel to create. |
| `description` | string | no | Description of the public or private channel to create. |
| `is_private` | boolean | yes | When `true`, creates a private channel instead of a public channel |
| `org_wide` | boolean | no | When `true`, the channel will be available org-wide. Note: if the channel is not `org_wide=true`, you must specify a `team_id` for this channel |
| `team_id` | string | no | The workspace to create the channel in. Note: this argument is required unless you set `org_wide=true`. |

## Example
```python
slack_cli("admin.conversations.create", {"name": "...", "is_private": true})
```
