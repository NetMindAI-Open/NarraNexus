# admin.apps.restrict

## Description
Restrict an app for installation on a workspace.

## Required scope
`admin.apps:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `app_id` | string | no | The id of the app to restrict. |
| `request_id` | string | no | The id of the request to restrict. |
| `team_id` | string | no | — |

## Example
```python
slack_cli("admin.apps.restrict", {})
```
