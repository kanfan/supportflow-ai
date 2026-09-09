data "aws_iam_policy_document" "state_access" {
  statement {
    sid       = "ListFoundationState"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.state_bucket_arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        var.foundation_state_key,
        "${var.foundation_state_key}.tflock",
      ]
    }
  }

  statement {
    sid    = "ReadWriteFoundationState"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
    ]
    resources = [local.state_file_arn]
  }

  statement {
    sid    = "ManageFoundationStateLock"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
    ]
    resources = [local.lock_file_arn]
  }

  statement {
    sid    = "UseStateKMSKey"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:Encrypt",
      "kms:GenerateDataKey",
    ]
    resources = [var.state_kms_key_arn]
  }
}

resource "aws_iam_policy" "state_access" {
  name        = "${local.name_prefix}-terraform-state"
  description = "Access only to the SupportFlow foundation state and native S3 lockfile"
  policy      = data.aws_iam_policy_document.state_access.json

  tags = local.mandatory_tags
}

resource "aws_iam_role_policy_attachment" "plan_state" {
  role       = aws_iam_role.terraform_plan.name
  policy_arn = aws_iam_policy.state_access.arn
}

resource "aws_iam_role_policy_attachment" "apply_state" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = aws_iam_policy.state_access.arn
}

resource "aws_iam_role_policy_attachment" "plan_read_only" {
  role       = aws_iam_role.terraform_plan.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy_attachment" "apply_read_only" {
  role       = aws_iam_role.terraform_apply.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/ReadOnlyAccess"
}

data "aws_iam_policy_document" "deployment" {
  statement {
    sid       = "AuthenticateToECR"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PublishAndReadSupportFlowImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/${var.project_name}",
    ]
  }

  statement {
    sid    = "ReadSupportFlowECSRelease"
    effect = "Allow"
    actions = [
      "ecs:DescribeClusters",
      "ecs:DescribeServices",
      "ecs:DescribeTaskDefinition",
      "ecs:DescribeTasks",
      "ecs:ListTasks",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid       = "RegisterSupportFlowTaskDefinitions"
    effect    = "Allow"
    actions   = ["ecs:RegisterTaskDefinition"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid       = "RunSupportFlowMigrationTask"
    effect    = "Allow"
    actions   = ["ecs:RunTask"]
    resources = local.deployment_task_definition_arns

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [local.deployment_cluster_arn]
    }
  }

  statement {
    sid       = "UpdateSupportFlowServices"
    effect    = "Allow"
    actions   = ["ecs:UpdateService"]
    resources = local.deployment_service_arns
  }

  statement {
    sid     = "ReadSupportFlowReleaseLogs"
    effect  = "Allow"
    actions = ["logs:FilterLogEvents"]
    resources = [
      "arn:${data.aws_partition.current.partition}:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/supportflow/${var.environment}/*:*",
    ]
  }

  dynamic "statement" {
    for_each = length(var.ecs_pass_role_arns) == 0 ? [] : [1]

    content {
      sid       = "PassReviewedECSTaskRoles"
      effect    = "Allow"
      actions   = ["iam:PassRole"]
      resources = var.ecs_pass_role_arns

      condition {
        test     = "StringEquals"
        variable = "iam:PassedToService"
        values   = ["ecs-tasks.amazonaws.com"]
      }
    }
  }
}

resource "aws_iam_policy" "deployment" {
  name        = "${local.name_prefix}-deployment"
  description = "Release-only access for the SupportFlow immutable ECS pipeline"
  policy      = data.aws_iam_policy_document.deployment.json

  tags = local.mandatory_tags
}

resource "aws_iam_role_policy_attachment" "deployment" {
  role       = aws_iam_role.deployment.name
  policy_arn = aws_iam_policy.deployment.arn
}
