# usergroups.update

## Description
Update an existing User Group

## Required scope
`usergroups:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `handle` | string | no | A mention handle. Must be unique among channels, users and User Groups. |
| `description` | string | no | A short description of the User Group. |
| `channels` | string | no | A comma separated string of encoded channel IDs for which the User Group uses as a default. |
| `include_count` | boolean | no | Include the number of users in the User Group. |
| `usergroup` | string | yes | The encoded ID of the User Group to update. |
| `name` | string | no | A name for the User Group. Must be unique among User Groups. |

## Example
```python
slack_cli("usergroups.update", {"usergroup": "..."})
```
