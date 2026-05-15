# users.list

## Description
Lists all users in a Slack team.

## Required scope
`users:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `limit` | integer | no | The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the users list hasn't been reached. Providing no `limit` value will result in Slack attempting to deliver you the entire result set. If the collection is too large you may experience `limit_required` or HTTP 500 errors. |
| `cursor` | string | no | Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail. |
| `include_locale` | boolean | no | Set this to `true` to receive the locale for users. Defaults to `false` |

## Example
```python
slack_cli("users.list", {})
```
