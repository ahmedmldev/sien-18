# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common commands

```bash
# Terraform (always use AWS_PROFILE=kit-bot, run from terraform/)
AWS_PROFILE=kit-bot terraform init
AWS_PROFILE=kit-bot terraform plan
AWS_PROFILE=kit-bot terraform apply

# Docker build — --provenance=false is required on Apple Silicon (prevents multi-arch manifest rejection by Lambda)
docker build --platform linux/amd64 --provenance=false -t kita-bot lambda/

# Manual test execution (dry_run=true skips form submit)
aws stepfunctions start-execution \
  --state-machine-arn $(AWS_PROFILE=kit-bot aws stepfunctions list-state-machines --query "stateMachines[?name=='kita-bot'].stateMachineArn" --output text --region eu-west-1) \
  --input '{"attempt":0,"url":"DOODLE_URL","debug":false,"dry_run":true}' \
  --region eu-west-1 --profile kit-bot

# Upload Gmail OAuth secrets to SSM
AWS_PROFILE=kit-bot bash scripts/upload_secrets.sh credentials.json token.json
```

## Architecture

EventBridge Scheduler → Step Functions → Lambda (loop) → Gmail API + Playwright/Chromium

**Flow:**
1. EventBridge Scheduler fires at 07:19 Berlin time (weekdays) — triggers Step Functions directly, no intermediary Lambda
2. Step Functions runs a retry loop: invoke Lambda → check result → wait 60 s → increment attempt → repeat, up to `watch_max_attempts` (default 11, i.e. 07:19–07:30)
3. Lambda (`kita_doodle_lambda.py`):
   - Pulls Gmail OAuth credentials from SSM Parameter Store (`/kita-bot/gmail-credentials`, `/kita-bot/gmail-token`) into `/tmp/`
   - Searches Gmail for today's Kita email (from `KITA_SENDER`, subject contains `KITA_SUBJECT_KEYWORDS`)
   - Extracts Doodle URL, launches headless Chromium via Playwright, registers the child
   - Takes 3 screenshots to `/tmp/` and attaches them to the notification email
   - Returns an `action` string; Step Functions branches on it:
     - `submitted` / `submitted_unverified` / `already_registered` → **Success**
     - `no_seats` → **Fail** (stop immediately, no point retrying)
     - `no_email` / `no_link` / `form_error` → **retry** (wait 60 s)

**Gmail token refresh:** If the OAuth token expires mid-use, it is refreshed silently and written back to SSM. If it cannot be refreshed (revoked), the Lambda raises — re-run locally to re-authorise and re-upload `token.json`.

## Key implementation details

- **Chromium in Lambda**: `playwright install --with-deps` does not support Amazon Linux 2023 (tries `apt-get`). Dependencies are installed manually via `dnf` in the Dockerfile; then `playwright install chromium` (no `--with-deps`).
- **Session click**: The Doodle checkbox is CSS-hidden; the click is dispatched via `page.evaluate()` on the row container (`data-testid="time-slot-item-container"`).
- **Terraform state**: S3 backend in eu-west-1 — bucket name is in `terraform/main.tf`. `terraform.tfvars` is gitignored — CI/CD injects values from GitHub Actions secrets; locally use `terraform/terraform.tfvars` (copy from `terraform.tfvars.example`).
- **IAM**: The `kita-bot` IAM user must be in a group with both `PowerUserAccess` and `IAMFullAccess` — PowerUserAccess alone blocks all `iam:*` actions, which Terraform needs.

## CI/CD

`.github/workflows/pipeline.yml` — triggers on push to `main` or `develop`, but only for paths `lambda/**`, `terraform/**`, `.github/workflows/**`.

- `ci` job (both branches): terraform plan + docker build validation
- `deploy` job (main only, needs ci): docker push to ECR → `lambda update-function-code` → `terraform apply`

All personal config is injected as GitHub Actions secrets (see README.md for the full list).
