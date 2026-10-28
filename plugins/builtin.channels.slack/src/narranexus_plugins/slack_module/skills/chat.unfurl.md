# chat.unfurl

## Description
Provide custom unfurl behavior for user-posted URLs

## Required scope
`links:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | yes | Channel ID of the message |
| `ts` | string | yes | Timestamp of the message to add unfurl behavior to. |
| `unfurls` | string | no | URL-encoded JSON map with keys set to URLs featured in the the message, pointing to their unfurl blocks or message attachments. |
| `user_auth_message` | string | no | Provide a simply-formatted string to send as an ephemeral message to the user as invitation to authenticate further and enable full unfurling behavior |
| `user_auth_required` | boolean | no | Set to `true` or `1` to indicate the user must install your Slack app to trigger unfurls for this domain |
| `user_auth_url` | string | no | Send users to this custom URL where they will complete authentication in your app to fully trigger unfurling. Value should be properly URL-encoded. |

## Example
```python
slack_cli("chat.unfurl", {"channel": "...", "ts": "..."})
```
