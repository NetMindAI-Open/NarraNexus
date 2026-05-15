# users.conversations

## Description
List conversations the calling user may access.

## Required scope
`channels:read`, `groups:read`, `im:read`, `mpim:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `user` | string | no | Browse conversations by a specific user ID's membership. Non-public channels are restricted to those where the calling user shares membership. |
| `types` | string | no | Mix and match channel types by providing a comma-separated list of any combination of `public_channel`, `private_channel`, `mpim`, `im` |
| `exclude_archived` | boolean | no | Set to `true` to exclude archived channels from the list |
| `limit` | integer | no | The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the list hasn't been reached. Must be an integer no larger than 1000. |
| `cursor` | string | no | Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail. |

## Example
```python
slack_cli("users.conversations", {})
```
