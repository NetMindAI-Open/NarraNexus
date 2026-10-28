# apps.permissions.request

## Description
Allows an app to request additional scopes

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `scopes` | string | yes | A comma separated list of scopes to request for |
| `trigger_id` | string | yes | Token used to trigger the permissions API |

## Example
```python
slack_cli("apps.permissions.request", {"scopes": "...", "trigger_id": "..."})
```
