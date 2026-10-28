# calls.info

## Description
Returns information about a Call.

## Required scope
`calls:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `id` | string | yes | `id` of the Call returned by the [`calls.add`](/methods/calls.add) method. |

## Example
```python
slack_cli("calls.info", {"id": "..."})
```
