#!/usr/bin/env bash
# deploy.sh — full Sien-18 deployment from AWS CloudShell
# Run once from: aws/scripts/deploy.sh
set -euo pipefail

REGION="eu-west-1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$SCRIPT_DIR/../terraform"
LAMBDA_DIR="$SCRIPT_DIR/../lambda"

# ── 1. Install Terraform if absent ────────────────────────────────────────────
if ! command -v terraform &>/dev/null; then
  echo ">>> Installing Terraform..."
  TF_VERSION="1.9.8"
  curl -sO "https://releases.hashicorp.com/terraform/${TF_VERSION}/terraform_${TF_VERSION}_linux_amd64.zip"
  unzip -q "terraform_${TF_VERSION}_linux_amd64.zip"
  mkdir -p "$HOME/.local/bin"
  mv terraform "$HOME/.local/bin/"
  rm "terraform_${TF_VERSION}_linux_amd64.zip"
  export PATH="$HOME/.local/bin:$PATH"
  echo "    Terraform $(terraform version -json | python3 -c 'import sys,json; print(json.load(sys.stdin)["terraform_version"])') ready."
fi
export PATH="$HOME/.local/bin:$PATH"

# ── 2. Phase 1 apply — create ECR repo + Secrets Manager secrets ──────────────
echo ""
echo ">>> Phase 1: Creating ECR repository and Secrets Manager secrets..."
cd "$TF_DIR"
terraform init -upgrade -input=false
terraform apply -auto-approve -input=false \
  -target=aws_ecr_repository.kita_bot \
  -target=aws_ecr_lifecycle_policy.kita_bot \
  -target=aws_secretsmanager_secret.gmail_credentials \
  -target=aws_secretsmanager_secret.gmail_token

ECR_URL=$(terraform output -raw ecr_repository_url)
echo "    ECR: $ECR_URL"

# ── 3. Build and push Docker image ────────────────────────────────────────────
echo ""
echo ">>> Phase 2: Building and pushing Docker image (this takes ~5 min first time)..."
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR_URL"

cd "$LAMBDA_DIR"
docker build --platform linux/amd64 -t kita-bot .
docker tag kita-bot:latest "$ECR_URL:latest"
docker push "$ECR_URL:latest"
echo "    Image pushed: $ECR_URL:latest"

# ── 4. Phase 2 apply — Lambda + Step Functions + Scheduler ───────────────────
echo ""
echo ">>> Phase 3: Deploying Lambda, Step Functions, and EventBridge Scheduler..."
cd "$TF_DIR"
terraform apply -auto-approve -input=false

echo ""
echo "==================================================================="
echo "  Deployment complete!"
echo "==================================================================="
terraform output
echo ""
echo "Next step: upload Gmail secrets"
echo "  cd $SCRIPT_DIR && ./upload_secrets.sh"
echo ""
echo "Test run (trigger Step Functions manually):"
SFN_ARN=$(terraform output -raw state_machine_arn)
echo "  aws stepfunctions start-execution \\"
echo "    --state-machine-arn $SFN_ARN \\"
echo "    --input '{\"attempt\":0,\"url\":\"YOUR_TEST_DOODLE_URL\",\"debug\":false,\"dry_run\":true}'"
