# apps.event.authorizations.list

## Description
Get a list of authorizations for the given event context. Each authorization represents an app installation that the event is visible to.

## Required scope
`authorizations:read`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `event_context` | string | yes | — |
| `cursor` | string | no | — |
| `limit` | integer | no | — |

## Example
```python
slack_cli("apps.event.authorizations.list", {"event_context": "..."})
```
