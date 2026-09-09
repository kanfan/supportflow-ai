provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.mandatory_tags
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

# IAM policy documents are rendered locally by the real provider, including in
# mock infrastructure tests. This alias performs no AWS API calls.
provider "aws" {
  alias  = "policy"
  region = var.aws_region
}
