# stars.list

## Description
Lists stars for a user.

## Required scope
`stars:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `count` | string | no | — |
| `page` | string | no | — |
| `cursor` | string | no | Parameter for pagination. Set `cursor` equal to the `next_cursor` attribute returned by the previous request's `response_metadata`. This parameter is optional, but pagination is mandatory: the default value simply fetches the first "page" of the collection. See [pagination](/docs/pagination) for more details. |
| `limit` | integer | no | The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the list hasn't been reached. |

## Example
```python
slack_cli("stars.list", {})
```
