data "aws_availability_zones" "available" {}

data "aws_vpc" "default" {
  filter {
    name   = "isDefault"
    values = ["true"]
  }
}

resource "aws_default_vpc" "default" {
  force_destroy                    = false
  assign_generated_ipv6_cidr_block = true

  tags = {
    Name = "Default VPC"
  }
}

resource "aws_default_subnet" "default" {
  for_each          = toset(data.aws_availability_zones.available.names)
  availability_zone = each.value

  assign_ipv6_address_on_creation = true

  tags = {
    Name = "Default Subnet ${each.value}"
  }
}

resource "aws_security_group" "mc_sg" {
  name        = "${var.project_name}-sg"
  description = "Security group for Minecraft server"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    from_port        = var.minecraft_port
    to_port          = var.minecraft_port
    protocol         = "tcp"
    cidr_blocks      = var.allowed_cidr_blocks
    ipv6_cidr_blocks = var.allowed_ipv6_cidr_blocks
  }

  egress {
    from_port        = 0
    to_port          = 0
    protocol         = "-1"
    cidr_blocks      = ["0.0.0.0/0"]
    ipv6_cidr_blocks = ["::/0"]
  }
}
