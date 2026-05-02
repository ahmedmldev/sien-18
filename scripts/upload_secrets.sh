#!/usr/bin/env bash
# upload_secrets.sh — push Gmail OAuth files to SSM Parameter Store (free tier)
#
# Usage (from CloudShell after uploading the two files):
#   ./upload_secrets.sh [credentials.json] [token.json]
#
# Defaults assume both files are in the KITA-BOT root directory (one level above aws/).
set -euo pipefail

REGION="eu-west-1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

CREDS_FILE="${1:-$PROJECT_ROOT/credentials.json}"
TOKEN_FILE="${2:-$PROJECT_ROOT/token.json}"

echo "Using credentials: $CREDS_FILE"
echo "Using token:       $TOKEN_FILE"

if [[ ! -f "$CREDS_FILE" ]]; then
  echo "ERROR: $CREDS_FILE not found."
  echo "  Download it from Google Cloud Console → APIs & Services → Credentials"
  exit 1
fi

if [[ ! -f "$TOKEN_FILE" ]]; then
  echo "ERROR: $TOKEN_FILE not found."
  echo "  Run locally first:  python kita_doodle.py --dry-run"
  echo "  That generates token.json via the browser OAuth flow."
  exit 1
fi

echo ""
echo ">>> Uploading credentials.json to /kita-bot/gmail-credentials (SSM SecureString)..."
aws ssm put-parameter \
  --name "/kita-bot/gmail-credentials" \
  --value "$(cat "$CREDS_FILE")" \
  --type "SecureString" \
  --overwrite \
  --region "$REGION"

echo ">>> Uploading token.json to /kita-bot/gmail-token (SSM SecureString)..."
aws ssm put-parameter \
  --name "/kita-bot/gmail-token" \
  --value "$(cat "$TOKEN_FILE")" \
  --type "SecureString" \
  --overwrite \
  --region "$REGION"

echo ""
echo "Parameters uploaded. Removing local copies from CloudShell..."
shred -u "$CREDS_FILE" "$TOKEN_FILE" 2>/dev/null \
  || rm -f "$CREDS_FILE" "$TOKEN_FILE"

echo "Done. Credential files removed."
