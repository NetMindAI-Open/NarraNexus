# calls.add

## Description
Registers a new Call.

## Required scope
`calls:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `external_unique_id` | string | yes | An ID supplied by the 3rd-party Call provider. It must be unique across all Calls from that service. |
| `external_display_id` | string | no | An optional, human-readable ID supplied by the 3rd-party Call provider. If supplied, this ID will be displayed in the Call object. |
| `join_url` | string | yes | The URL required for a client to join the Call. |
| `desktop_app_join_url` | string | no | When supplied, available Slack clients will attempt to directly launch the 3rd-party Call with this URL. |
| `date_start` | integer | no | Call start time in UTC UNIX timestamp format |
| `title` | string | no | The name of the Call. |
| `created_by` | string | no | The valid Slack user ID of the user who created this Call. When this method is called with a user token, the `created_by` field is optional and defaults to the authed user of the token. Otherwise, the field is required. |
| `users` | string | no | The list of users to register as participants in the Call. [Read more on how to specify users here](/apis/calls#users). |

## Example
```python
slack_cli("calls.add", {"external_unique_id": "...", "join_url": "..."})
```
