# EventBridge Scheduler (newer API — supports native timezone, no UTC math needed)
resource "aws_scheduler_schedule" "kita_weekly_summary" {
  name        = "kita-bot-weekly-summary"
  group_name  = "default"
  description = "Trigger Sien-18 weekly summary every Friday at 17:00 Berlin time"
  state       = "ENABLED"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = var.summary_schedule_cron
  schedule_expression_timezone = "Europe/Berlin"

  target {
    arn      = aws_lambda_function.kita_bot.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = jsonencode({ weekly_summary = true })
  }
}

resource "aws_scheduler_schedule" "kita_morning" {
  name        = "kita-bot-morning"
  group_name  = "default"
  description = "Trigger Sien-18 every weekday at 07:19 Berlin time"
  state       = "ENABLED"

  flexible_time_window {
    mode = "OFF"
  }

  # Berlin timezone — automatically follows CET/CEST switch
  schedule_expression          = var.schedule_cron
  schedule_expression_timezone = "Europe/Berlin"

  target {
    arn      = aws_sfn_state_machine.kita_bot.arn
    role_arn = aws_iam_role.scheduler.arn

    # Initial state passed into the Step Functions execution
    input = jsonencode({
      attempt = 0
      url     = var.test_doodle_url
      debug   = var.debug_mode
      dry_run = false
    })
  }
}
