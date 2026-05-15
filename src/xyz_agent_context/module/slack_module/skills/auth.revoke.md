# auth.revoke

## Description
Revokes a token.

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `test` | boolean | no | Setting this parameter to `1` triggers a _testing mode_ where the specified token will not actually be revoked. |

## Example
```python
slack_cli("auth.revoke", {})
```
