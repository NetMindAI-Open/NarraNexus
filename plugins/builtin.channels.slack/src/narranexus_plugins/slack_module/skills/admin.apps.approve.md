# admin.apps.approve

## Description
Approve an app for installation on a workspace.

## Required scope
`admin.apps:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `app_id` | string | no | The id of the app to approve. |
| `request_id` | string | no | The id of the request to approve. |
| `team_id` | string | no | — |

## Example
```python
slack_cli("admin.apps.approve", {})
```
