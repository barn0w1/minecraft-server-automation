# Minecraft Server on AWS (Serverless-ish)

This project deploys a Minecraft server infrastructure on AWS where the compute (EC2) is ephemeral and on-demand, while the state is persisted in S3.

## Architecture

- **Terraform**: Manages persistent resources (S3, DynamoDB, Lambda, IAM, Security Groups).
- **Lambda**: Orchestrates the server lifecycle (Start/Stop/Monitor).
- **EC2**: Ephemeral instances that run the Minecraft server using Docker.
- **S3**: Stores world data, configuration, and scripts.
- **DynamoDB**: Tracks the state of the worlds (Running/Stopped, IP address).

## Setup

1.  **Deploy Terraform**:
    ```bash
    cd terraform
    terraform init
    terraform apply
    ```
    Note the `function_url` output.

2.  **Prepare S3**:
    The `server_lifecycle.sh` script is automatically uploaded to `s3://<bucket>/scripts/`.
    
    To create a new world (e.g., "survival"):
    - Create a folder `worlds/survival/` in the S3 bucket.
    - (Optional) Upload a `compose.yaml` to `worlds/survival/compose.yaml`. If not provided, a default one will be created on first launch.

## Usage

### API (Function URL)

**Start Server:**
```bash
curl -X POST "https://<function_url>/" \
     -H "Content-Type: application/json" \
     -d '{"action": "start", "world": "survival"}'
```

### Local CLI (recommended)

The repo includes a small helper that reads Terraform outputs automatically, so you don't need to hard-code the Function URL or bucket names.

```bash
./mcctl.sh start  survival
./mcctl.sh status survival
./mcctl.sh stop   survival
```

You can also keep using wrappers:

```bash
./start.sh survival
./status.sh survival
./stop.sh survival
```

### Emergency access to the EC2 (no SSH)

If the server is running and you need to inspect the instance, you can open a Session Manager shell via SSM:

```bash
./mcctl.sh connect survival
```

Requirements:
- Your AWS CLI identity must have permissions for `ssm:StartSession` (and related actions).
- The instance role already includes `AmazonSSMManagedInstanceCore`.

## S3 layout


- `data_bucket_name` / `config_bucket_name` (latest state)
    - `scripts/server_lifecycle.sh`
    - `worlds/<world>/compose.yaml`
    - `worlds/<world>/data.tar`

- `snapshot_bucket_name` (history)
    - `worlds/<world>/snapshots/<UTC>.tar`

Snapshots are deleted automatically by S3 Lifecycle after `snapshot_retention_days` (default: 3 days).

### Manual snapshot

If you want to snapshot without stopping the server:

```bash
./mcctl.sh snapshot survival
```

This creates:
- `s3://<snapshot_bucket>/worlds/survival/snapshots/<UTC>.tar` (history)
- `s3://<data_bucket>/worlds/survival/data.tar` (overwrites latest)

**Stop Server:**
```bash
curl -X POST "https://<function_url>/" \
     -H "Content-Type: application/json" \
     -d '{"action": "stop", "world": "survival"}'
```

**Check Status:**
```bash
curl "https://<function_url>/?action=status&world=survival"
```

### Auto-Shutdown
An EventBridge rule triggers the Lambda every 10 minutes to check for idle servers (0 players). If a server is idle for more than 30 minutes, it will be automatically stopped and terminated.

## Customization
- **Instance Type**: Change `instance_type` in `terraform/variables.tf`.
- **Minecraft Version**: Edit the `compose.yaml` in S3 or the default generation logic in `scripts/server_lifecycle.sh`.
