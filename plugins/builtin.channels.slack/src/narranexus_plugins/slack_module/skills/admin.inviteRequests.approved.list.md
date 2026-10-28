# admin.inviteRequests.approved.list

## Description
List all approved workspace invite requests.

## Required scope
`admin.invites:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | no | ID for the workspace where the invite requests were made. |
| `cursor` | string | no | Value of the `next_cursor` field sent as part of the previous API response |
| `limit` | integer | no | The number of results that will be returned by the API on each invocation. Must be between 1 - 1000, both inclusive |

## Example
```python
slack_cli("admin.inviteRequests.approved.list", {})
```
