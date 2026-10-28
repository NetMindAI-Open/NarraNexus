# conversations.history

## Description
Fetches a conversation's history of messages and events.

## Required scope
`channels:history`, `groups:history`, `im:history`, `mpim:history`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Conversation ID to fetch history for. |
| `latest` | number | no | End of time range of messages to include in results. |
| `oldest` | number | no | Start of time range of messages to include in results. |
| `inclusive` | boolean | no | Include messages with latest or oldest timestamp in results only when either timestamp is specified. |
| `limit` | integer | no | The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the users list hasn't been reached. |
| `cursor` | string | no | Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail. |

## Example
```python
slack_cli("conversations.history", {})
```
