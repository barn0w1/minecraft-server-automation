resource "aws_s3_bucket" "mc_data" {
  bucket_prefix = "${var.project_name}-data-"
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "mc_data" {
  bucket = aws_s3_bucket.mc_data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket" "mc_snapshots" {
  bucket_prefix = "${var.project_name}-snapshots-"
  force_destroy = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "mc_snapshots" {
  bucket = aws_s3_bucket.mc_snapshots.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "mc_data" {
  bucket = aws_s3_bucket.mc_data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "mc_snapshots" {
  bucket = aws_s3_bucket.mc_snapshots.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "mc_snapshots" {
  bucket = aws_s3_bucket.mc_snapshots.id

  rule {
    id     = "expire-snapshots"
    status = "Enabled"

    filter {
      prefix = "worlds/"
    }

    expiration {
      days = var.snapshot_retention_days
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_s3_object" "lifecycle_script" {
  bucket                 = aws_s3_bucket.mc_data.id
  key                    = "scripts/server_lifecycle.sh"
  source                 = "${path.root}/../scripts/server_lifecycle.sh"
  content_type           = "application/octet-stream"
  server_side_encryption = "AES256"
}
