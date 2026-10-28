# files.remote.add

## Description
Adds a file from a remote service

## Required scope
`remote_files:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `external_id` | string | no | Creator defined GUID for the file. |
| `title` | string | no | Title of the file being shared. |
| `filetype` | string | no | type of file |
| `external_url` | string | no | URL of the remote file. |
| `preview_image` | string | no | Preview of the document via `multipart/form-data`. |
| `indexable_file_contents` | string | no | A text file (txt, pdf, doc, etc.) containing textual search terms that are used to improve discovery of the remote file. |

## Example
```python
slack_cli("files.remote.add", {})
```
