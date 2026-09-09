variable "aws_region" {
  description = "AWS region for the SupportFlow portfolio evidence environment."
  type        = string
  default     = "eu-central-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be a valid AWS region identifier."
  }
}

variable "project_name" {
  description = "Lowercase prefix for SupportFlow AWS resources."
  type        = string
  default     = "supportflow"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.project_name))
    error_message = "project_name must be 3-20 lowercase alphanumeric/hyphen characters."
  }
}

variable "environment" {
  description = "Protected GitHub and AWS evidence environment."
  type        = string
  default     = "staging"

  validation {
    condition     = var.environment == "staging"
    error_message = "The Week 5 evidence environment must be staging."
  }
}

variable "owner" {
  description = "Owner tag for cost allocation and incident response."
  type        = string
  default     = "Eray"

  validation {
    condition     = length(trimspace(var.owner)) > 0
    error_message = "owner must not be empty."
  }
}

variable "cost_center" {
  description = "CostCenter tag for all foundation resources."
  type        = string
  default     = "portfolio"

  validation {
    condition     = length(trimspace(var.cost_center)) > 0
    error_message = "cost_center must not be empty."
  }
}

variable "budget_notification_emails" {
  description = "Email subscribers for actual and forecast budget notifications. Kept in uncommitted tfvars/state."
  type        = set(string)
  sensitive   = true

  validation {
    condition = (
      length(var.budget_notification_emails) >= 1 &&
      length(var.budget_notification_emails) <= 5 &&
      alltrue([
        for email in var.budget_notification_emails :
        can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", email))
      ])
    )
    error_message = "Provide between one and five syntactically valid notification email addresses."
  }
}

variable "monthly_emergency_budget_usd" {
  description = "Emergency monthly ceiling; this is not the expected operating spend."
  type        = number
  default     = 120

  validation {
    condition     = var.monthly_emergency_budget_usd == 120
    error_message = "ADR 0007 fixes the emergency ceiling at USD 120."
  }
}

variable "github_repository" {
  description = "Exact GitHub owner/repository allowed to assume OIDC roles."
  type        = string
  default     = "kanfan/supportflow-ai"

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must use owner/repository format."
  }
}

variable "state_bucket_arn" {
  description = "ARN output from the retained bootstrap state bucket."
  type        = string

  validation {
    condition     = can(regex("^arn:[^:]+:s3:::[a-z0-9.-]+$", var.state_bucket_arn))
    error_message = "state_bucket_arn must be an S3 bucket ARN."
  }
}

variable "state_kms_key_arn" {
  description = "KMS key ARN output from the retained bootstrap stack."
  type        = string

  validation {
    condition     = can(regex("^arn:[^:]+:kms:[^:]+:[0-9]{12}:key/.+$", var.state_kms_key_arn))
    error_message = "state_kms_key_arn must be a KMS key ARN."
  }
}

variable "foundation_state_key" {
  description = "Exact S3 state key granted to foundation plan/apply roles."
  type        = string
  default     = "foundation/terraform.tfstate"

  validation {
    condition     = var.foundation_state_key == "foundation/terraform.tfstate"
    error_message = "The reviewed foundation state key must not drift."
  }
}

variable "ecs_pass_role_arns" {
  description = "Explicit execution/task role ARNs the deploy role may pass after the workload slice creates them."
  type        = set(string)
  default     = []

  validation {
    condition = alltrue([
      for arn in var.ecs_pass_role_arns :
      can(regex("^arn:[^:]+:iam::[0-9]{12}:role/.+$", arn))
    ])
    error_message = "Every ecs_pass_role_arns value must be an IAM role ARN."
  }
}
