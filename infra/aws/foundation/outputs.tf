output "aws_account_id" {
  description = "Account identifier safe for sanitized handoff evidence."
  value       = data.aws_caller_identity.current.account_id
}

output "aws_region" {
  description = "Approved evidence region."
  value       = var.aws_region
}

output "github_oidc_subject" {
  description = "Exact GitHub environment subject allowed by the role trust policies."
  value       = local.oidc_subject
}

output "terraform_plan_role_arn" {
  description = "Read-only OIDC role for reviewed Terraform plans."
  value       = aws_iam_role.terraform_plan.arn
}

output "terraform_apply_role_arn" {
  description = "Protected OIDC role reserved for explicit workload permissions in the next slice."
  value       = aws_iam_role.terraform_apply.arn
}

output "terraform_apply_role_live_ready" {
  description = "False until the workload slice adds reviewed, non-IAM mutation permissions."
  value       = false
}

output "deployment_role_arn" {
  description = "Value for SUPPORTFLOW_AWS_ROLE_ARN after workload pass-role ARNs are added."
  value       = aws_iam_role.deployment.arn
}

output "deployment_role_live_ready" {
  description = "False until the workload slice supplies exact ECS execution/task role ARNs."
  value       = length(var.ecs_pass_role_arns) > 0
}

output "monthly_budget_names" {
  description = "Separate actual/forecast budget identifiers; each has four notifications."
  value       = { for kind, budget in aws_budgets_budget.portfolio_emergency : kind => budget.name }
}

output "budget_thresholds_usd" {
  description = "Actual and forecast notification thresholds."
  value       = sort(tolist(local.actual_budget_thresholds))
}

output "foundation_state_location" {
  description = "Sanitized remote-state location; contains no state data."
  value       = "s3://${local.state_bucket_name}/${var.foundation_state_key}"
}
