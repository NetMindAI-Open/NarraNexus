# files.remote.update

## Description
Updates an existing remote file.

## Required scope
`remote_files:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `file` | string | no | Specify a file by providing its ID. |
| `external_id` | string | no | Creator defined GUID for the file. |
| `title` | string | no | Title of the file being shared. |
| `filetype` | string | no | type of file |
| `external_url` | string | no | URL of the remote file. |
| `preview_image` | string | no | Preview of the document via `multipart/form-data`. |
| `indexable_file_contents` | string | no | File containing contents that can be used to improve searchability for the remote file. |

## Example
```python
slack_cli("files.remote.update", {})
```
