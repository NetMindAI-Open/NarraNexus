# apps.permissions.resources.list

## Description
Returns list of resource grants this app has on a team.

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `cursor` | string | no | Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail. |
| `limit` | integer | no | The maximum number of items to return. |

## Example
```python
slack_cli("apps.permissions.resources.list", {})
```
