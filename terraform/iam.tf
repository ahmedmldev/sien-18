# ── Lambda execution role ──────────────────────────────────────────────────────

resource "aws_iam_role" "lambda" {
  name = "kita-bot-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "lambda_ssm" {
  name = "kita-bot-ssm-access"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter", "ssm:PutParameter"]
        Resource = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/kita-bot/*"
      },
      {
        # Required to decrypt/encrypt SecureString parameters using the AWS-managed SSM key
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:GenerateDataKey"]
        Resource = "arn:aws:kms:${var.aws_region}:${data.aws_caller_identity.current.account_id}:alias/aws/ssm"
      }
    ]
  })
}

# ── Step Functions execution role ──────────────────────────────────────────────

resource "aws_iam_role" "sfn" {
  name = "kita-bot-sfn"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sfn_invoke_lambda" {
  name = "invoke-kita-bot-lambda"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.kita_bot.arn
    }]
  })
}

# ── EventBridge Scheduler execution role ───────────────────────────────────────

resource "aws_iam_role" "scheduler" {
  name = "kita-bot-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_start_sfn" {
  name = "start-kita-bot-sfn"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "states:StartExecution"
        Resource = aws_sfn_state_machine.kita_bot.arn
      },
      {
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = aws_lambda_function.kita_bot.arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "lambda_sfn_read" {
  name = "kita-bot-sfn-read"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "states:ListExecutions"
        Resource = aws_sfn_state_machine.kita_bot.arn
      },
      {
        # DescribeExecution requires execution ARN (not state machine ARN)
        Effect   = "Allow"
        Action   = "states:DescribeExecution"
        Resource = "${replace(aws_sfn_state_machine.kita_bot.arn, ":stateMachine:", ":execution:")}:*"
      },
      {
        Effect   = "Allow"
        Action   = "sts:GetCallerIdentity"
        Resource = "*"
      }
    ]
  })
}
