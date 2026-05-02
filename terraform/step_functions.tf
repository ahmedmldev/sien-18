resource "aws_sfn_state_machine" "kita_bot" {
  name     = "kita-bot"
  role_arn = aws_iam_role.sfn.arn
  type     = "STANDARD"

  # ASL definition — retry loop 07:19 → 07:30 (max 11 attempts, 60 s apart)
  definition = jsonencode({
    Comment = "Sien-18: try Kita registration every 60 s until success or deadline"
    StartAt = "Register"
    States = {

      Register = {
        Type     = "Task"
        Resource = aws_lambda_function.kita_bot.arn
        # Forward only the fields Lambda cares about; keep result separate
        Parameters = {
          "attempt.$"  = "$.attempt"
          "url.$"      = "$.url"
          "debug.$"    = "$.debug"
          "dry_run.$"  = "$.dry_run"
        }
        ResultPath = "$.result"
        # Lambda errors (timeout, crash) → retry path, not failure
        Catch = [{
          ErrorEquals = ["States.ALL"]
          ResultPath  = "$.error"
          Next        = "MaybeRetry"
        }]
        Next = "CheckResult"
      }

      CheckResult = {
        Type = "Choice"
        Choices = [
          {
            # Both "submitted" and "submitted_unverified" count as success
            Or = [
              { Variable = "$.result.action", StringEquals = "submitted" },
              { Variable = "$.result.action", StringEquals = "submitted_unverified" },
              { Variable = "$.result.action", StringEquals = "already_registered" }
            ]
            Next = "Success"
          },
          {
            # No seats — pointless to retry, stop immediately
            Variable    = "$.result.action"
            StringEquals = "no_seats"
            Next        = "NoSeats"
          }
        ]
        # no_email / no_link / form_error / dry_run → retry
        Default = "MaybeRetry"
      }

      MaybeRetry = {
        Type = "Choice"
        Choices = [{
          Variable                 = "$.attempt"
          NumericGreaterThanEquals = var.watch_max_attempts
          Next                     = "DeadlineReached"
        }]
        Default = "Wait"
      }

      Wait = {
        Type    = "Wait"
        Seconds = 60
        Next    = "Increment"
      }

      Increment = {
        Type = "Pass"
        Parameters = {
          "attempt.$"  = "States.MathAdd($.attempt, 1)"
          "url.$"      = "$.url"
          "debug.$"    = "$.debug"
          "dry_run.$"  = "$.dry_run"
        }
        Next = "Register"
      }

      Success = { Type = "Succeed" }

      NoSeats = {
        Type  = "Fail"
        Error = "NoSeats"
        Cause = "All seats taken — no point retrying"
      }

      DeadlineReached = {
        Type  = "Fail"
        Error = "DeadlineReached"
        Cause = "Max attempts reached (07:30 deadline)"
      }
    }
  })
}
