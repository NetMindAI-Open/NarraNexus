# conversations.open

## Description
Opens or resumes a direct message or multi-person direct message.

## Required scope
`channels:write`, `groups:write`, `im:write`, `mpim:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Resume a conversation by supplying an `im` or `mpim`'s ID. Or provide the `users` field instead. |
| `users` | string | no | Comma separated lists of users. If only one user is included, this creates a 1:1 DM. The ordering of the users is preserved whenever a multi-person direct message is returned. Supply a `channel` when not supplying `users`. |
| `return_im` | boolean | no | Boolean, indicates you want the full IM channel definition in the response. |

## Example
```python
slack_cli("conversations.open", {})
```
