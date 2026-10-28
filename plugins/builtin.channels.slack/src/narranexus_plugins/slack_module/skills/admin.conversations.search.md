# admin.conversations.search

## Description
Search for public or private channels in an Enterprise organization.

## Required scope
`admin.conversations:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_ids` | string | no | Comma separated string of team IDs, signifying the workspaces to search through. |
| `query` | string | no | Name of the the channel to query by. |
| `limit` | integer | no | Maximum number of items to be returned. Must be between 1 - 20 both inclusive. Default is 10. |
| `cursor` | string | no | Set `cursor` to `next_cursor` returned by the previous call to list items in the next page. |
| `search_channel_types` | string | no | The type of channel to include or exclude in the search. For example `private` will search private channels, while `private_exclude` will exclude them. For a full list of types, check the [Types section](#types). |
| `sort` | string | no | Possible values are `relevant` (search ranking based on what we think is closest), `name` (alphabetical), `member_count` (number of users in the channel), and `created` (date channel was created). You can optionally pair this with the `sort_dir` arg to change how it is sorted |
| `sort_dir` | string | no | Sort direction. Possible values are `asc` for ascending order like (1, 2, 3) or (a, b, c), and `desc` for descending order like (3, 2, 1) or (c, b, a) |

## Example
```python
slack_cli("admin.conversations.search", {})
```
