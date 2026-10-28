# admin.conversations.restrictAccess.addGroup

## Description
Add an allowlist of IDP groups for accessing a channel

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | no | The workspace where the channel exists. This argument is required for channels only tied to one workspace, and optional for channels that are shared across an organization. |
| `group_id` | string | yes | The [IDP Group](https://slack.com/help/articles/115001435788-Connect-identity-provider-groups-to-your-Enterprise-Grid-org) ID to be an allowlist for the private channel. |
| `channel_id` | string | yes | The channel to link this group to. |

## Example
```python
slack_cli("admin.conversations.restrictAccess.addGroup", {"group_id": "...", "channel_id": "..."})
```
