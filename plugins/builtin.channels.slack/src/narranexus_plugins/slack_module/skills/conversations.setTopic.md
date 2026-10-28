# conversations.setTopic

## Description
Sets the topic for a conversation.

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Conversation to set the topic of |
| `topic` | string | no | The new topic string. Does not support formatting or linkification. |

## Example
```python
slack_cli("conversations.setTopic", {})
```
