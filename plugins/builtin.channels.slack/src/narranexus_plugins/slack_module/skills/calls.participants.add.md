# calls.participants.add

## Description
Registers new participants added to a Call.

## Required scope
`calls:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `id` | string | yes | `id` returned by the [`calls.add`](/methods/calls.add) method. |
| `users` | string | yes | The list of users to add as participants in the Call. [Read more on how to specify users here](/apis/calls#users). |

## Example
```python
slack_cli("calls.participants.add", {"id": "...", "users": "..."})
```
