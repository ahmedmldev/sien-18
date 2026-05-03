resource "aws_cloudwatch_log_group" "lambda" {
  name              = "/aws/lambda/kita-bot"
  retention_in_days = 30
}

resource "aws_lambda_function" "kita_bot" {
  function_name = "kita-bot"
  description   = "Sien-18 — automated Kita Notbetreuung registration for Child"
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.kita_bot.repository_url}:latest"
  role          = aws_iam_role.lambda.arn
  timeout       = var.lambda_timeout_sec
  memory_size   = var.lambda_memory_mb

  environment {
    variables = {
      CHILD_NAME               = var.child_name
      ATTENDEE_NAME            = var.attendee_name
      ATTENDEE_EMAIL           = var.attendee_email
      NOTIFY_RECIPIENTS        = var.notify_recipients
      KITA_SENDER              = var.kita_sender
      KITA_SUBJECT_KEYWORDS    = var.kita_subject_keywords
      GMAIL_CREDENTIALS_PARAM = aws_ssm_parameter.gmail_credentials.name
      GMAIL_TOKEN_PARAM       = aws_ssm_parameter.gmail_token.name
      URL                     = var.test_doodle_url
      DRY_RUN                 = "false"
    }
  }

  depends_on = [aws_cloudwatch_log_group.lambda]
}
