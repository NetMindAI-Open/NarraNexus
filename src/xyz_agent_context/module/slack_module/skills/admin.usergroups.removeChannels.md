# admin.usergroups.removeChannels

## Description
Remove one or more default channels from an org-level IDP group (user group).

## Required scope
`admin.usergroups:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `usergroup_id` | string | yes | ID of the IDP Group |
| `channel_ids` | string | yes | Comma-separated string of channel IDs |

## Example
```python
slack_cli("admin.usergroups.removeChannels", {"usergroup_id": "...", "channel_ids": "..."})
```
