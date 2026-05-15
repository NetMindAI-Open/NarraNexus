# users.setPhoto

## Description
Set the user profile photo

## Required scope
`users.profile:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `crop_w` | string | no | Width/height of crop box (always square) |
| `crop_x` | string | no | X coordinate of top-left corner of crop box |
| `crop_y` | string | no | Y coordinate of top-left corner of crop box |
| `image` | string | no | File contents via `multipart/form-data`. |

## Example
```python
slack_cli("users.setPhoto", {})
```
