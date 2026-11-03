variable "env" {
  default = "prd"
}

locals {
  prefix = "bank-${var.env}"
}
