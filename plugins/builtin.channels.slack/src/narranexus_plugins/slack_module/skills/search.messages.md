# search.messages

## Description
Searches for messages matching a query.

## Required scope
`search:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `count` | integer | no | Pass the number of results you want per "page". Maximum of `100`. |
| `highlight` | boolean | no | Pass a value of `true` to enable query highlight markers (see below). |
| `page` | integer | no | — |
| `query` | string | yes | Search query. |
| `sort` | string | no | Return matches sorted by either `score` or `timestamp`. |
| `sort_dir` | string | no | Change sort direction to ascending (`asc`) or descending (`desc`). |

## Example
```python
slack_cli("search.messages", {"query": "..."})
```
