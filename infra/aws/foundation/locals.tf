locals {
  name_prefix  = "${var.project_name}-${var.environment}"
  oidc_subject = "repo:${var.github_repository}:environment:${var.environment}"

  mandatory_tags = {
    Project            = var.project_name
    Environment        = var.environment
    Owner              = var.owner
    CostCenter         = var.cost_center
    ManagedBy          = "Terraform"
    DataClassification = "synthetic-only"
    Ephemeral          = "true"
  }

  actual_budget_thresholds   = toset([10, 25, 50, 120])
  forecast_budget_thresholds = toset([10, 25, 50, 120])

  state_bucket_name = trimprefix(var.state_bucket_arn, "arn:${data.aws_partition.current.partition}:s3:::")
  state_file_arn    = "${var.state_bucket_arn}/${var.foundation_state_key}"
  lock_file_arn     = "${local.state_file_arn}.tflock"

  deployment_cluster_arn = "arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:cluster/${local.name_prefix}"
  deployment_service_arns = [
    "arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:service/${local.name_prefix}/${local.name_prefix}-api",
    "arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:service/${local.name_prefix}/${local.name_prefix}-worker",
  ]
  deployment_task_definition_arns = [
    "arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task-definition/${local.name_prefix}-api:*",
    "arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task-definition/${local.name_prefix}-worker:*",
    "arn:${data.aws_partition.current.partition}:ecs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:task-definition/${local.name_prefix}-migration:*",
  ]

  github_oidc_trust_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "GitHubEnvironmentOnly"
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub" = local.oidc_subject
          }
        }
      },
    ]
  })
}
