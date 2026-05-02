output "ecr_repository_url" {
  description = "Push Docker images here"
  value       = aws_ecr_repository.kita_bot.repository_url
}

output "lambda_function_name" {
  value = aws_lambda_function.kita_bot.function_name
}

output "state_machine_arn" {
  description = "Trigger a test run: aws stepfunctions start-execution --state-machine-arn <this>"
  value       = aws_sfn_state_machine.kita_bot.arn
}

output "scheduler_name" {
  value = aws_scheduler_schedule.kita_morning.name
}

output "ssm_param_credentials" {
  description = "Upload credentials.json content here"
  value       = aws_ssm_parameter.gmail_credentials.name
}

output "ssm_param_token" {
  description = "Upload token.json content here"
  value       = aws_ssm_parameter.gmail_token.name
}
