# files.info

## Description
Gets information about a file.

## Required scope
`files:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `file` | string | no | Specify a file by providing its ID. |
| `count` | string | no | — |
| `page` | string | no | — |
| `limit` | integer | no | The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the list hasn't been reached. |
| `cursor` | string | no | Parameter for pagination. File comments are paginated for a single file. Set `cursor` equal to the `next_cursor` attribute returned by the previous request's `response_metadata`. This parameter is optional, but pagination is mandatory: the default value simply fetches the first "page" of the collection of comments. See [pagination](/docs/pagination) for more details. |

## Example
```python
slack_cli("files.info", {})
```
