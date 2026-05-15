# files.comments.delete

## Description
Deletes an existing comment on a file.

## Required scope
`files:write:user`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `file` | string | no | File to delete a comment from. |
| `id` | string | no | The comment to delete. |

## Example
```python
slack_cli("files.comments.delete", {})
```
