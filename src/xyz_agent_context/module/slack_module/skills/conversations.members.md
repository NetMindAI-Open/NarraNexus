# conversations.members

## Description
Retrieve members of a conversation.

## Required scope
`channels:read`, `groups:read`, `im:read`, `mpim:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | ID of the conversation to retrieve members for |
| `limit` | integer | no | The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the users list hasn't been reached. |
| `cursor` | string | no | Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail. |

## Example
```python
slack_cli("conversations.members", {})
```
