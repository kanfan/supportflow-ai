output "state_bucket_name" {
  description = "Non-secret S3 bucket name used by partial backend configuration."
  value       = aws_s3_bucket.terraform_state.id
}

output "state_bucket_arn" {
  description = "Non-secret state bucket ARN used by foundation IAM policies."
  value       = aws_s3_bucket.terraform_state.arn
}

output "state_kms_key_arn" {
  description = "Non-secret KMS key ARN used by the S3 backend."
  value       = aws_kms_key.terraform_state.arn
}

output "aws_account_id" {
  description = "Account identifier retained as sanitized handoff evidence."
  value       = data.aws_caller_identity.current.account_id
}

output "aws_region" {
  description = "Region used by the state foundation."
  value       = var.aws_region
}
