# SSM Parameter Store (free tier) — replaces Secrets Manager
# Values are placeholders; upload_secrets.sh writes the real content after terraform apply.

resource "aws_ssm_parameter" "gmail_credentials" {
  name        = "/kita-bot/gmail-credentials"
  description = "Gmail OAuth2 client credentials (contents of credentials.json)"
  type        = "SecureString"
  value       = "PLACEHOLDER"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "gmail_token" {
  name        = "/kita-bot/gmail-token"
  description = "Gmail OAuth2 token (contents of token.json — auto-refreshed by Lambda)"
  type        = "SecureString"
  value       = "PLACEHOLDER"

  lifecycle {
    ignore_changes = [value]
  }
}
