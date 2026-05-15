# reactions.list

## Description
Lists reactions made by a user.

## Required scope
`reactions:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `user` | string | no | Show reactions made by this user. Defaults to the authed user. |
| `full` | boolean | no | If true always return the complete reaction list. |
| `count` | integer | no | — |
| `page` | integer | no | — |
| `cursor` | string | no | Parameter for pagination. Set `cursor` equal to the `next_cursor` attribute returned by the previous request's `response_metadata`. This parameter is optional, but pagination is mandatory: the default value simply fetches the first "page" of the collection. See [pagination](/docs/pagination) for more details. |
| `limit` | integer | no | The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the list hasn't been reached. |

## Example
```python
slack_cli("reactions.list", {})
```
