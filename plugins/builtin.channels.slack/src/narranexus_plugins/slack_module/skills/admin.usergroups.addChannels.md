# admin.usergroups.addChannels

## Description
Add one or more default channels to an IDP group.

## Required scope
`admin.usergroups:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `usergroup_id` | string | yes | ID of the IDP group to add default channels for. |
| `team_id` | string | no | The workspace to add default channels in. |
| `channel_ids` | string | yes | Comma separated string of channel IDs. |

## Example
```python
slack_cli("admin.usergroups.addChannels", {"usergroup_id": "...", "channel_ids": "..."})
```
