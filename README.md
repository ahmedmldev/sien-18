# Sien-18

## Prerequisites

| Tool | Minimum version |
|------|----------------|
| AWS CLI | 2.x |
| Terraform | 1.6 |
| Docker | 24 (with BuildKit) |
| Python | 3.12 (OAuth flow only) |

---

## One-time local setup

### 1. AWS IAM user

Create a dedicated IAM user (`kita-bot`), attach **PowerUserAccess** + **IAMFullAccess**, generate access keys, then configure a local profile:

```bash
aws configure --profile kit-bot
# AWS Access Key ID:     <from IAM console>
# AWS Secret Access Key: <from IAM console>
# Default region:        eu-west-1
# Default output:        json
```

### 2. Terraform state bucket

```bash
aws s3api create-bucket \
  --bucket sien-18-tf-207844652117 \
  --region eu-west-1 \
  --create-bucket-configuration LocationConstraint=eu-west-1 \
  --profile kit-bot

aws s3api put-bucket-versioning \
  --bucket sien-18-tf-207844652117 \
  --versioning-configuration Status=Enabled \
  --profile kit-bot

aws s3api put-bucket-encryption \
  --bucket sien-18-tf-207844652117 \
  --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}' \
  --profile kit-bot
```

### 3. Gmail OAuth credentials

1. Go to **Google Cloud Console → APIs & Services → Credentials**
2. Create a project, enable the **Gmail API**
3. Create an **OAuth 2.0 Client ID** (type: Desktop app), download `credentials.json`
4. Place `credentials.json` in the repo root (it is gitignored), then run the local OAuth flow to generate `token.json`:

```bash
pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client
python -c "
import json, os
from google_auth_oauthlib.flow import InstalledAppFlow
flow = InstalledAppFlow.from_client_secrets_file(
    'credentials.json',
    scopes=['https://www.googleapis.com/auth/gmail.readonly',
            'https://www.googleapis.com/auth/gmail.send']
)
creds = flow.run_local_server(port=0)
with open('token.json', 'w') as f:
    f.write(creds.to_json())
print('token.json written')
"
```

5. Upload both files to SSM and delete the local copies:

```bash
AWS_PROFILE=kit-bot bash scripts/upload_secrets.sh credentials.json token.json
```

### 4. GitHub Actions secrets

In the repository **Settings → Secrets and variables → Actions**, add:

| Secret | Value |
|--------|-------|
| `AWS_ACCESS_KEY_ID` | kita-bot IAM access key |
| `AWS_SECRET_ACCESS_KEY` | kita-bot IAM secret key |
| `CHILD_NAME` | Child's first name |
| `ATTENDEE_NAME` | Full name shown to Kita organiser |
| `ATTENDEE_EMAIL` | Gmail address used for Doodle |
| `NOTIFY_RECIPIENTS` | Comma-separated notification email(s) — used for booking confirmations **and** weekly summaries |
| `KITA_SENDER` | From address of the Kita Notbetreuung email |
| `KITA_SUBJECT_KEYWORDS` | Comma-separated subject keywords (e.g. `Notbetreuung,Doodle`) |
| `SCHEDULE_CRON` | EventBridge cron for daily registration (default: `cron(19 7 ? * MON-FRI *)`) |
| `SUMMARY_SCHEDULE_CRON` | EventBridge cron for weekly summary trigger (default: `cron(0 17 ? * MON-FRI *)` — Lambda skips non-Fridays) |

---

## Terraform variables

All variables without defaults **must** be supplied — either via `terraform.tfvars` locally or via GitHub Actions secrets in CI/CD. `terraform.tfvars` is gitignored; use `terraform/terraform.tfvars.example` as a template.

| Variable | Default | Description |
|----------|---------|-------------|
| `aws_region` | `eu-west-1` | AWS region to deploy into |
| `child_name` | — | Child's first name — used for the Doodle "Name Ihres Kind" field |
| `attendee_name` | — | Full name shown to the Kita organiser in Doodle |
| `attendee_email` | — | Email shown to the Kita organiser in Doodle (must be the Gmail OAuth account) |
| `notify_recipients` | — | Comma-separated emails that receive the booking notification |
| `kita_sender` | — | `From` address of the Kita Notbetreuung email |
| `kita_subject_keywords` | — | Comma-separated keywords that must ALL appear in the Kita email subject |
| `lambda_memory_mb` | `1024` | Lambda memory — Chromium needs at least 1024 MB |
| `lambda_timeout_sec` | `300` | Lambda timeout in seconds |
| `watch_max_attempts` | `11` | Max Lambda invocations before Step Functions gives up (07:19 + 11 min = 07:30) |
| `schedule_cron` | `cron(19 7 ? * MON-FRI *)` | EventBridge cron for daily registration (Europe/Berlin timezone) |
| `summary_schedule_cron` | `cron(0 17 ? * MON-FRI *)` | EventBridge cron for weekly summary trigger — Lambda skips non-Fridays internally (Europe/Berlin timezone) |
| `test_doodle_url` | `""` | Optional Doodle URL for test executions — leave empty in production |
| `debug_mode` | `false` | Run Chromium in headed mode — only works with a display, keep false in Lambda |

---

## Manual test run

```bash
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:eu-west-1:$(aws sts get-caller-identity --query Account --output text):stateMachine:kita-bot \
  --input '{"attempt":0,"url":"https://doodle.com/meeting/participate/id/YOUR_POLL_ID","debug":false,"dry_run":true}' \
  --region eu-west-1 \
  --profile kit-bot
```

Set `"dry_run": true` to exercise the full flow without submitting the Doodle form.

To trigger the weekly summary immediately (e.g. for testing):

```bash
aws lambda invoke \
  --function-name kita-bot \
  --payload '{"weekly_summary": true}' \
  --cli-binary-format raw-in-base64-out \
  --region eu-west-1 \
  --profile kit-bot \
  /tmp/summary-response.json && cat /tmp/summary-response.json
```
