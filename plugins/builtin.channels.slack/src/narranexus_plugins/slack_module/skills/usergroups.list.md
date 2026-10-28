# usergroups.list

## Description
List all User Groups for a team

## Required scope
`usergroups:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `include_users` | boolean | no | Include the list of users for each User Group. |
| `include_count` | boolean | no | Include the number of users in each User Group. |
| `include_disabled` | boolean | no | Include disabled User Groups. |

## Example
```python
slack_cli("usergroups.list", {})
```
