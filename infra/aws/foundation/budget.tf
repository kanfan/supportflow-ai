resource "aws_budgets_budget" "portfolio_emergency" {
  for_each = {
    actual   = "ACTUAL"
    forecast = "FORECASTED"
  }

  name         = "${local.name_prefix}-monthly-emergency-${each.key}"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_emergency_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = local.actual_budget_thresholds

    content {
      comparison_operator        = "GREATER_THAN"
      notification_type          = each.value
      threshold                  = notification.value
      threshold_type             = "ABSOLUTE_VALUE"
      subscriber_email_addresses = var.budget_notification_emails
    }
  }

  tags = local.mandatory_tags
}
