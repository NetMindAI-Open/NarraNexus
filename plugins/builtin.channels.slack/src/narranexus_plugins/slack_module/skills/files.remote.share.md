# files.remote.share

## Description
Share a remote file into a channel.

## Required scope
`remote_files:share`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `file` | string | no | Specify a file registered with Slack by providing its ID. Either this field or `external_id` or both are required. |
| `external_id` | string | no | The globally unique identifier (GUID) for the file, as set by the app registering the file with Slack. Either this field or `file` or both are required. |
| `channels` | string | no | Comma-separated list of channel IDs where the file will be shared. |

## Example
```python
slack_cli("files.remote.share", {})
```
