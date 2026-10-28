# setWebhook

## Description
Use this method to specify a URL and receive incoming updates via an outgoing webhook. Whenever there is an update for the bot, Telegram will send an HTTPS POST request to the specified URL.

## Required scope
Telegram bots are token-scoped, no per-method permissions.

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `url` | string | yes | HTTPS URL to send updates to. Use an empty string to remove webhook integration. |
| `certificate` | InputFile | no | Upload your public key certificate so that the root certificate in use can be checked. |
| `ip_address` | string | no | The fixed IP address which will be used to send webhook requests instead of the IP address resolved through DNS. |
| `max_connections` | integer | no | The maximum allowed number of simultaneous HTTPS connections to the webhook for update delivery, 1-100. Defaults to 40. |
| `allowed_updates` | array of string | no | A JSON-serialized list of the update types you want your bot to receive. |
| `drop_pending_updates` | boolean | no | Pass True to drop all pending updates. |
| `secret_token` | string | no | A secret token to be sent in a header `X-Telegram-Bot-Api-Secret-Token` in every webhook request, 1-256 characters. |

## Response
Returns `True` on success.

## Example
```python
tg_cli("setWebhook", {"url": "https://example.com/tg-webhook", "secret_token": "..."})
```
