# calls.end

## Description
Ends a Call.

## Required scope
`calls:write`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `id` | string | yes | `id` returned when registering the call using the [`calls.add`](/methods/calls.add) method. |
| `duration` | integer | no | Call duration in seconds |

## Example
```python
slack_cli("calls.end", {"id": "..."})
```
