# usergroups.users.list

## Description
List all users in a User Group

## Required scope
`usergroups:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `include_disabled` | boolean | no | Allow results that involve disabled User Groups. |
| `usergroup` | string | yes | The encoded ID of the User Group to update. |

## Example
```python
slack_cli("usergroups.users.list", {"usergroup": "..."})
```
