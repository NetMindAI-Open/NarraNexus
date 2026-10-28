# admin.usergroups.listChannels

## Description
List the channels linked to an org-level IDP group (user group).

## Required scope
`admin.usergroups:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `usergroup_id` | string | yes | ID of the IDP group to list default channels for. |
| `team_id` | string | no | ID of the the workspace. |
| `include_num_members` | boolean | no | Flag to include or exclude the count of members per channel. |

## Example
```python
slack_cli("admin.usergroups.listChannels", {"usergroup_id": "..."})
```
