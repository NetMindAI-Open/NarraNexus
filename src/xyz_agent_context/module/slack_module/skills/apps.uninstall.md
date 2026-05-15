# apps.uninstall

## Description
Uninstalls your app from a workspace.

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `client_id` | string | no | Issued when you created your application. |
| `client_secret` | string | no | Issued when you created your application. |

## Example
```python
slack_cli("apps.uninstall", {})
```
