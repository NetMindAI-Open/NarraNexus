# admin.conversations.setConversationPrefs

## Description
Set the posting permissions for a public or private channel.

## Required scope
`admin.conversations:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel_id` | string | yes | The channel to set the prefs for |
| `prefs` | string | yes | The prefs for this channel in a stringified JSON format. |

## Example
```python
slack_cli("admin.conversations.setConversationPrefs", {"channel_id": "...", "prefs": "..."})
```
