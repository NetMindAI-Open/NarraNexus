# usergroups.enable

## Description
Enable a User Group

## Required scope
`usergroups:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `include_count` | boolean | no | Include the number of users in the User Group. |
| `usergroup` | string | yes | The encoded ID of the User Group to enable. |

## Example
```python
slack_cli("usergroups.enable", {"usergroup": "..."})
```
