# usergroups.users.update

## Description
Update the list of users for a User Group

## Required scope
`usergroups:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `include_count` | boolean | no | Include the number of users in the User Group. |
| `usergroup` | string | yes | The encoded ID of the User Group to update. |
| `users` | string | yes | A comma separated string of encoded user IDs that represent the entire list of users for the User Group. |

## Example
```python
slack_cli("usergroups.users.update", {"usergroup": "...", "users": "..."})
```
