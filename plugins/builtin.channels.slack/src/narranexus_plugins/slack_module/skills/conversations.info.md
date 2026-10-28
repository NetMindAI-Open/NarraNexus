# conversations.info

## Description
Retrieve information about a conversation.

## Required scope
`channels:read`, `groups:read`, `im:read`, `mpim:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Conversation ID to learn more about |
| `include_locale` | boolean | no | Set this to `true` to receive the locale for this conversation. Defaults to `false` |
| `include_num_members` | boolean | no | Set to `true` to include the member count for the specified conversation. Defaults to `false` |

## Example
```python
slack_cli("conversations.info", {})
```
