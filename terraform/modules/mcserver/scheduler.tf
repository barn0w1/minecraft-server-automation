resource "aws_cloudwatch_event_rule" "idle_check" {
  name                = "${var.project_name}-idle-check"
  description         = "Check for idle Minecraft servers"
  schedule_expression = var.idle_check_schedule
}

resource "aws_cloudwatch_event_target" "check_lambda" {
  rule      = aws_cloudwatch_event_rule.idle_check.name
  target_id = "SendToLambda"
  arn       = aws_lambda_function.mc_monitor.arn
  input     = jsonencode({ action = "monitor" })
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.mc_monitor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.idle_check.arn
}
