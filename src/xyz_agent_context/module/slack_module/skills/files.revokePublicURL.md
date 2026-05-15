# files.revokePublicURL

## Description
Revokes public/external sharing access for a file

## Required scope
`files:write:user`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `file` | string | no | File to revoke |

## Example
```python
slack_cli("files.revokePublicURL", {})
```
