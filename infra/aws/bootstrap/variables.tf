variable "aws_region" {
  description = "AWS region that owns the retained Terraform state foundation."
  type        = string
  default     = "eu-central-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be a valid AWS region identifier."
  }
}

variable "project_name" {
  description = "Lowercase project prefix used in retained bootstrap resources."
  type        = string
  default     = "supportflow"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.project_name))
    error_message = "project_name must be 3-20 lowercase alphanumeric/hyphen characters."
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
  description = "CostCenter tag for all retained bootstrap resources."
  type        = string
  default     = "portfolio"

  validation {
    condition     = length(trimspace(var.cost_center)) > 0
    error_message = "cost_center must not be empty."
  }
}

variable "state_version_retention_days" {
  description = "Days to retain noncurrent Terraform state object versions."
  type        = number
  default     = 90

  validation {
    condition     = var.state_version_retention_days >= 30
    error_message = "state versions must be retained for at least 30 days."
  }
}
