# files.remote.info

## Description
Retrieve information about a remote file added to Slack

## Required scope
`remote_files:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `file` | string | no | Specify a file by providing its ID. |
| `external_id` | string | no | Creator defined GUID for the file. |

## Example
```python
slack_cli("files.remote.info", {})
```
