mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:iam::123456789012:user/test"
      user_id    = "AIDATEST"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition          = "aws"
      dns_suffix         = "amazonaws.com"
      reverse_dns_prefix = "com.amazonaws"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"sts:GetCallerIdentity\",\"Resource\":\"*\"}]}"
    }
  }

  override_resource {
    target          = aws_iam_openid_connect_provider.github
    override_during = plan
    values = {
      arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
    }
  }
}

variables {
  budget_notification_emails = ["owner@example.com", "reviewer@example.com"]
  state_bucket_arn           = "arn:aws:s3:::supportflow-123456789012-eu-central-1-tfstate"
  state_kms_key_arn          = "arn:aws:kms:eu-central-1:123456789012:key/00000000-0000-0000-0000-000000000000"
}

run "budget_and_oidc_contract" {
  command = plan

  assert {
    condition     = aws_budgets_budget.portfolio_emergency.limit_amount == "120"
    error_message = "The emergency monthly budget must remain USD 120."
  }

  assert {
    condition = alltrue([
      for threshold in [10, 25, 50, 120] :
      contains([
        for item in aws_budgets_budget.portfolio_emergency.notification : item.threshold
        if item.notification_type == "ACTUAL"
      ], threshold)
    ])
    error_message = "Actual budget notifications must include USD 10/25/50/120."
  }

  assert {
    condition = alltrue([
      for threshold in [10, 25, 50, 120] :
      contains([
        for item in aws_budgets_budget.portfolio_emergency.notification : item.threshold
        if item.notification_type == "FORECASTED"
      ], threshold)
    ])
    error_message = "Forecast budget notifications must include USD 10/25/50/120."
  }

  assert {
    condition = (
      jsondecode(aws_iam_role.deployment.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com" &&
      jsondecode(aws_iam_role.deployment.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:kanfan/supportflow-ai:environment:staging"
    )
    error_message = "GitHub OIDC trust must be exact for audience and protected environment subject."
  }

  assert {
    condition = (
      aws_iam_role.terraform_plan.name != aws_iam_role.terraform_apply.name &&
      aws_iam_role.terraform_apply.name != aws_iam_role.deployment.name
    )
    error_message = "Plan, apply, and deployment roles must remain separated."
  }

  assert {
    condition = (
      output.terraform_apply_role_live_ready == false &&
      output.deployment_role_live_ready == false
    )
    error_message = "Apply and deploy roles must stay non-live until reviewed workload permissions exist."
  }
}
