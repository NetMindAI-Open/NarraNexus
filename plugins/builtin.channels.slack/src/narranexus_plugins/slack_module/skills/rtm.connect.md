# rtm.connect

## Description
Starts a Real Time Messaging session.

## Required scope
`rtm:stream`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `batch_presence_aware` | boolean | no | Batch presence deliveries via subscription. Enabling changes the shape of `presence_change` events. See [batch presence](/docs/presence-and-status#batching). |
| `presence_sub` | boolean | no | Only deliver presence events when requested by subscription. See [presence subscriptions](/docs/presence-and-status#subscriptions). |

## Example
```python
slack_cli("rtm.connect", {})
```
