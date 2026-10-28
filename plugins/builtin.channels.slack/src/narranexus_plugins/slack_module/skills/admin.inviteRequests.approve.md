# admin.inviteRequests.approve

## Description
Approve a workspace invite request.

## Required scope
`admin.invites:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `team_id` | string | no | ID for the workspace where the invite request was made. |
| `invite_request_id` | string | yes | ID of the request to invite. |

## Example
```python
slack_cli("admin.inviteRequests.approve", {"invite_request_id": "..."})
```
