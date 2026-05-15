# admin.usergroups.addTeams

## Description
Associate one or more default workspaces with an organization-wide IDP group.

## Required scope
`admin.teams:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `usergroup_id` | string | yes | An encoded usergroup (IDP Group) ID. |
| `team_ids` | string | yes | A comma separated list of encoded team (workspace) IDs. Each workspace *MUST* belong to the organization associated with the token. |
| `auto_provision` | boolean | no | When `true`, this method automatically creates new workspace accounts for the IDP group members. |

## Example
```python
slack_cli("admin.usergroups.addTeams", {"usergroup_id": "...", "team_ids": "..."})
```
