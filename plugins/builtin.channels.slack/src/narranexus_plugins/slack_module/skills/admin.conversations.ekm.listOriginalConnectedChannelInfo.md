# admin.conversations.ekm.listOriginalConnectedChannelInfo

## Description
List all disconnected channels—i.e., channels that were once connected to other workspaces and then disconnected—and the corresponding original channel IDs for key revocation with EKM.

## Required scope
`admin.conversations:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_ids` | string | no | A comma-separated list of channels to filter to. |
| `team_ids` | string | no | A comma-separated list of the workspaces to which the channels you would like returned belong. |
| `limit` | integer | no | The maximum number of items to return. Must be between 1 - 1000 both inclusive. |
| `cursor` | string | no | Set `cursor` to `next_cursor` returned by the previous call to list items in the next page. |

## Example
```python
slack_cli("admin.conversations.ekm.listOriginalConnectedChannelInfo", {})
```
