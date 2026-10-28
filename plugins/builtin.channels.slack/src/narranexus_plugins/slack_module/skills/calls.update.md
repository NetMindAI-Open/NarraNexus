# calls.update

## Description
Updates information about a Call.

## Required scope
`calls:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `id` | string | yes | `id` returned by the [`calls.add`](/methods/calls.add) method. |
| `title` | string | no | The name of the Call. |
| `join_url` | string | no | The URL required for a client to join the Call. |
| `desktop_app_join_url` | string | no | When supplied, available Slack clients will attempt to directly launch the 3rd-party Call with this URL. |

## Example
```python
slack_cli("calls.update", {"id": "..."})
```
