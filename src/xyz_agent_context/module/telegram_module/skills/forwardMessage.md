# forwardMessage

## Description
Use this method to forward messages of any kind. Service messages and messages with protected content can't be forwarded.

## Required scope
Telegram bots are token-scoped, no per-method permissions. The bot must have access to both source and target chats.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `chat_id` | integer or string | yes | Unique identifier for the target chat or username of the target channel (in the format `@channelusername`). |
| `message_thread_id` | integer | no | Unique identifier for the target message thread (topic) of the forum; for forum supergroups only. |
| `from_chat_id` | integer or string | yes | Unique identifier for the chat where the original message was sent (or channel username in the format `@channelusername`). |
| `video_start_timestamp` | integer | no | New start timestamp for the forwarded video in the message. |
| `disable_notification` | boolean | no | Sends the message silently. |
| `protect_content` | boolean | no | Protects the contents of the forwarded message from forwarding and saving. |
| `message_id` | integer | yes | Message identifier in the chat specified in `from_chat_id`. |

## Response
On success, the sent `Message` is returned.

## Example
```python
tg_cli("forwardMessage", {"chat_id": 123, "from_chat_id": 456, "message_id": 999})
```
