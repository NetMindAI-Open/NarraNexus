# apps.permissions.users.request

## Description
Enables an app to trigger a permissions modal to grant an app access to a user access scope.

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `scopes` | string | yes | A comma separated list of user scopes to request for |
| `trigger_id` | string | yes | Token used to trigger the request |
| `user` | string | yes | The user this scope is being requested for |

## Example
```python
slack_cli("apps.permissions.users.request", {"scopes": "...", "trigger_id": "...", "user": "..."})
```
