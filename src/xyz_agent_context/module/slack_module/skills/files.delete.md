# files.delete

## Description
Deletes a file.

## Required scope
`files:write:user`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `file` | string | no | ID of file to delete. |

## Example
```python
slack_cli("files.delete", {})
```
