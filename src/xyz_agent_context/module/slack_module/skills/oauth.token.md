# oauth.token

## Description
Exchanges a temporary OAuth verifier code for a workspace token.

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `client_id` | string | no | Issued when you created your application. |
| `client_secret` | string | no | Issued when you created your application. |
| `code` | string | no | The `code` param returned via the OAuth callback. |
| `redirect_uri` | string | no | This must match the originally submitted URI (if one was sent). |
| `single_channel` | boolean | no | Request the user to add your app only to a single channel. |

## Example
```python
slack_cli("oauth.token", {})
```
