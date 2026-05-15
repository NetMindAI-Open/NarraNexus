# files.remote.remove

## Description
Remove a remote file.

## Required scope
`remote_files:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `file` | string | no | Specify a file by providing its ID. |
| `external_id` | string | no | Creator defined GUID for the file. |

## Example
```python
slack_cli("files.remote.remove", {})
```
