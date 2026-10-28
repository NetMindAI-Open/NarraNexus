# files.remote.list

## Description
Retrieve information about a remote file added to Slack

## Required scope
`remote_files:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channel` | string | no | Filter files appearing in a specific channel, indicated by its ID. |
| `ts_from` | number | no | Filter files created after this timestamp (inclusive). |
| `ts_to` | number | no | Filter files created before this timestamp (inclusive). |
| `limit` | integer | no | The maximum number of items to return. |
| `cursor` | string | no | Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail. |

## Example
```python
slack_cli("files.remote.list", {})
```
