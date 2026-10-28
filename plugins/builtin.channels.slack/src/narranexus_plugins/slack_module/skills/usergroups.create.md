# usergroups.create

## Description
Create a User Group

## Required scope
`usergroups:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `channels` | string | no | A comma separated string of encoded channel IDs for which the User Group uses as a default. |
| `description` | string | no | A short description of the User Group. |
| `handle` | string | no | A mention handle. Must be unique among channels, users and User Groups. |
| `include_count` | boolean | no | Include the number of users in each User Group. |
| `name` | string | yes | A name for the User Group. Must be unique among User Groups. |

## Example
```python
slack_cli("usergroups.create", {"name": "..."})
```
