# calls.participants.remove

## Description
Registers participants removed from a Call.

## Required scope
`calls:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `id` | string | yes | `id` returned by the [`calls.add`](/methods/calls.add) method. |
| `users` | string | yes | The list of users to remove as participants in the Call. [Read more on how to specify users here](/apis/calls#users). |

## Example
```python
slack_cli("calls.participants.remove", {"id": "...", "users": "..."})
```
