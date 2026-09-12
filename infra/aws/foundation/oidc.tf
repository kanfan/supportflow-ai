resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]

  tags = local.mandatory_tags
}

resource "aws_iam_role" "terraform_plan" {
  name                 = "${local.name_prefix}-terraform-plan"
  description          = "Read-only Terraform plan role for reviewed SupportFlow changes"
  assume_role_policy   = local.github_oidc_trust_policy
  max_session_duration = 3600

  tags = local.mandatory_tags
}

resource "aws_iam_role" "terraform_apply" {
  name                 = "${local.name_prefix}-terraform-apply"
  description          = "Protected Terraform apply role for SupportFlow foundation changes"
  assume_role_policy   = local.github_oidc_trust_policy
  max_session_duration = 3600

  tags = local.mandatory_tags
}

resource "aws_iam_role" "deployment" {
  name                 = "${local.name_prefix}-deployment"
  description          = "Protected immutable release role consumed by the #39 pipeline"
  assume_role_policy   = local.github_oidc_trust_policy
  max_session_duration = 3600

  tags = local.mandatory_tags
}
