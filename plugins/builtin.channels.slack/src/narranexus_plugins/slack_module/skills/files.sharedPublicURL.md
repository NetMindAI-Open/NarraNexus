# files.sharedPublicURL

## Description
Enables a file for public/external sharing.

## Required scope
`files:write:user`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `file` | string | no | File to share |

## Example
```python
slack_cli("files.sharedPublicURL", {})
```
