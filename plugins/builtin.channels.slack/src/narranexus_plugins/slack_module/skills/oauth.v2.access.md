# oauth.v2.access

## Description
Exchanges a temporary OAuth verifier code for an access token.

## Required scope
`none`

## Arguments
| name | type | required | description |
|------|------|----------|-------------|
| `client_id` | string | no | Issued when you created your application. |
| `client_secret` | string | no | Issued when you created your application. |
| `code` | string | yes | The `code` param returned via the OAuth callback. |
| `redirect_uri` | string | no | This must match the originally submitted URI (if one was sent). |

## Example
```python
slack_cli("oauth.v2.access", {"code": "..."})
```
