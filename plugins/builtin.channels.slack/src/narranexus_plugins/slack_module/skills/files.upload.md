# files.upload

## Description
Uploads or creates a file.

## Required scope
`files:write:user`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `file` | string | no | File contents via `multipart/form-data`. If omitting this parameter, you must submit `content`. |
| `content` | string | no | File contents via a POST variable. If omitting this parameter, you must provide a `file`. |
| `filetype` | string | no | A [file type](/types/file#file_types) identifier. |
| `filename` | string | no | Filename of file. |
| `title` | string | no | Title of file. |
| `initial_comment` | string | no | The message text introducing the file in specified `channels`. |
| `channels` | string | no | Comma-separated list of channel names or IDs where the file will be shared. |
| `thread_ts` | number | no | Provide another message's `ts` value to upload this file as a reply. Never use a reply's `ts` value; use its parent instead. |

## Example
```python
slack_cli("files.upload", {})
```
