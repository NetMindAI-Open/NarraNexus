# admin.conversations.restrictAccess.removeGroup

## Description
Remove a linked IDP group linked from a private channel

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | yes | The workspace where the channel exists. This argument is required for channels only tied to one workspace, and optional for channels that are shared across an organization. |
| `group_id` | string | yes | The [IDP Group](https://slack.com/help/articles/115001435788-Connect-identity-provider-groups-to-your-Enterprise-Grid-org) ID to remove from the private channel. |
| `channel_id` | string | yes | The channel to remove the linked group from. |

## Example
```python
slack_cli("admin.conversations.restrictAccess.removeGroup", {"team_id": "...", "group_id": "...", "channel_id": "..."})
```
