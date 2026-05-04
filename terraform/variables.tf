variable "aws_region" {
  description = "AWS region to deploy into"
  default     = "eu-west-1"
}

# ── Child / attendee identity ──────────────────────────────────────────────────

variable "child_name" {
  description = "Child's first name — used for the mandatory 'Name Ihres Kind' Doodle question"
}

variable "attendee_name" {
  description = "Full name shown to the Kita organiser in Doodle"
}

variable "attendee_email" {
  description = "Email shown to the Kita organiser in Doodle (also the Gmail account)"
}

variable "notify_recipients" {
  description = "Comma-separated emails that receive the Sien-18 booking notification"
}

# ── Kita email detection ───────────────────────────────────────────────────────

variable "kita_sender" {
  description = "From address of the Kita Notbetreuung email"
}

variable "kita_subject_keywords" {
  description = "Comma-separated keywords that must ALL appear in the Kita email subject"
}

# ── Testing / debug ────────────────────────────────────────────────────────────

variable "test_doodle_url" {
  description = "Optional Doodle URL injected into the Step Functions input for testing. Leave empty for production."
  default     = ""
}

variable "debug_mode" {
  description = "Set true to run Chromium in headed mode (only works with a display — leave false in Lambda)"
  default     = false
}

# ── Lambda sizing ──────────────────────────────────────────────────────────────

variable "lambda_memory_mb" {
  description = "Lambda memory in MB — Chromium needs at least 1024"
  default     = 1024
}

variable "lambda_timeout_sec" {
  description = "Lambda timeout in seconds — covers Playwright + Gmail polling"
  default     = 300
}

# ── Step Functions retry loop ──────────────────────────────────────────────────

variable "watch_max_attempts" {
  description = "Max Lambda invocations before the state machine gives up (07:19 + 11 min = 07:30)"
  default     = 11
}

variable "schedule_cron" {
  description = "EventBridge Scheduler cron (Berlin timezone). Default: 07:19 every weekday."
  default     = "cron(19 7 ? * MON-FRI *)"
}

variable "summary_schedule_cron" {
  description = "EventBridge Scheduler cron for Friday weekly summary (Berlin timezone). Default: 17:00 every Friday."
  default     = "cron(0 17 ? * FRI *)"
}
