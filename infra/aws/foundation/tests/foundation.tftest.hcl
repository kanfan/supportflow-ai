mock_provider "aws" {
  override_resource {
    target = aws_iam_policy.state_access["plan"]
    values = {
      arn = "arn:aws:iam::123456789012:policy/supportflow-staging-plan-state"
    }
  }

  override_resource {
    target = aws_iam_policy.state_access["apply"]
    values = {
      arn = "arn:aws:iam::123456789012:policy/supportflow-staging-apply-state"
    }
  }

  override_resource {
    target = aws_iam_policy.deployment
    values = {
      arn = "arn:aws:iam::123456789012:policy/supportflow-staging-deployment"
    }
  }

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

  override_resource {
    target = aws_iam_openid_connect_provider.github
    values = {
      arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
    }
  }
}

provider "aws" {
  alias                       = "policy"
  region                      = "eu-central-1"
  access_key                  = "test-only"
  secret_key                  = "test-only"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
}

variables {
  budget_notification_emails = ["owner@example.com", "reviewer@example.com"]
  state_bucket_arn           = "arn:aws:s3:::supportflow-123456789012-eu-central-1-tfstate"
  state_kms_key_arn          = "arn:aws:kms:eu-central-1:123456789012:key/00000000-0000-0000-0000-000000000000"
}

run "budget_and_oidc_contract" {
  command = apply

  assert {
    condition     = alltrue([for budget in aws_budgets_budget.portfolio_emergency : budget.limit_amount == "120"])
    error_message = "The emergency monthly budget must remain USD 120."
  }

  assert {
    condition = alltrue([
      for threshold in [10, 25, 50, 120] :
      contains([
        for item in aws_budgets_budget.portfolio_emergency["actual"].notification : item.threshold
        if item.notification_type == "ACTUAL"
      ], threshold)
    ])
    error_message = "Actual budget notifications must include USD 10/25/50/120."
  }

  assert {
    condition = alltrue([
      for threshold in [10, 25, 50, 120] :
      contains([
        for item in aws_budgets_budget.portfolio_emergency["forecast"].notification : item.threshold
        if item.notification_type == "FORECASTED"
      ], threshold)
    ])
    error_message = "Forecast budget notifications must include USD 10/25/50/120."
  }

  assert {
    condition = alltrue([
      for kind, budget in aws_budgets_budget.portfolio_emergency :
      length(budget.notification) == 4 && length(budget.notification) <= 5 &&
      alltrue([for notice in budget.notification :
        notice.notification_type == (kind == "actual" ? "ACTUAL" : "FORECASTED") &&
        notice.threshold_type == "ABSOLUTE_VALUE" &&
        notice.subscriber_email_addresses == var.budget_notification_emails
      ])
    ])
    error_message = "Each budget must stay within AWS's five-notification limit and notify both owners."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.state_access["plan"].policy).Statement :
      statement.Sid != "AccessFoundationState" || (
        statement.Action == "s3:GetObject" &&
        statement.Resource == "${var.state_bucket_arn}/${var.foundation_state_key}"
      )
    ])
    error_message = "Plan must only read the state object, never write or delete it."
  }

  assert {
    condition = alltrue([
      for statement in jsondecode(aws_iam_policy.state_access["plan"].policy).Statement :
      statement.Sid == "ManageFoundationStateLock" ||
      !anytrue([for action in flatten([statement.Action]) : contains(["s3:PutObject", "s3:DeleteObject", "s3:*", "*"], action)])
    ])
    error_message = "Only the exact lockfile statement may grant plan S3 writes."
  }

  assert {
    condition = alltrue([
      for kind in ["plan", "apply"] : anytrue([
        for statement in jsondecode(aws_iam_policy.state_access[kind].policy).Statement :
        statement.Sid == "ManageFoundationStateLock" &&
        statement.Resource == "${var.state_bucket_arn}/${var.foundation_state_key}.tflock" &&
        toset(flatten([statement.Action])) == toset(["s3:GetObject", "s3:PutObject", "s3:DeleteObject"])
      ])
    ])
    error_message = "Both roles need read/write/delete on only the native S3 lockfile."
  }

  assert {
    condition = anytrue([
      for statement in jsondecode(aws_iam_policy.state_access["apply"].policy).Statement :
      statement.Sid == "AccessFoundationState" &&
      statement.Resource == "${var.state_bucket_arn}/${var.foundation_state_key}" &&
      toset(flatten([statement.Action])) == toset(["s3:GetObject", "s3:PutObject"])
    ])
    error_message = "Apply must retain state read/write without state deletion."
  }

  assert {
    condition = (
      aws_iam_role_policy_attachment.plan_state.role == aws_iam_role.terraform_plan.name &&
      aws_iam_role_policy_attachment.plan_state.policy_arn == aws_iam_policy.state_access["plan"].arn &&
      aws_iam_role_policy_attachment.apply_state.role == aws_iam_role.terraform_apply.name &&
      aws_iam_role_policy_attachment.apply_state.policy_arn == aws_iam_policy.state_access["apply"].arn &&
      aws_iam_policy.state_access["plan"].arn != aws_iam_policy.state_access["apply"].arn
    )
    error_message = "Plan and apply must attach their distinct state policies."
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
