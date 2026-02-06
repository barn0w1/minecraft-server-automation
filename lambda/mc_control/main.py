import boto3
import os
import json
import time
import logging
import re
import urllib.request
import urllib.error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ec2 = boto3.client('ec2')
ssm = boto3.client('ssm')
dynamodb = boto3.resource('dynamodb')
table = dynamodb.Table(os.environ['DYNAMODB_TABLE'])

INSTANCE_PROFILE_ARN = os.environ['INSTANCE_PROFILE_ARN']
SECURITY_GROUP_ID = os.environ['SECURITY_GROUP_ID']
S3_BUCKET_NAME = os.environ['S3_BUCKET_NAME']
CONFIG_BUCKET_NAME = os.environ.get('CONFIG_BUCKET_NAME', S3_BUCKET_NAME)
SNAPSHOT_BUCKET_NAME = os.environ.get('SNAPSHOT_BUCKET_NAME', S3_BUCKET_NAME)
INSTANCE_TYPE = os.environ['INSTANCE_TYPE']
EBS_VOLUME_SIZE = int(os.environ.get('EBS_VOLUME_SIZE', '20'))
EBS_VOLUME_TYPE = os.environ.get('EBS_VOLUME_TYPE', 'gp3')
REGION = os.environ['REGION']
IDLE_TIMEOUT = int(os.environ.get('IDLE_TIMEOUT', '1800'))

CLOUDFLARE_API_TOKEN = os.environ.get('CLOUDFLARE_API_TOKEN')
CLOUDFLARE_ZONE_ID = os.environ.get('CLOUDFLARE_ZONE_ID')
DNS_RECORD_NAME = os.environ.get('DNS_RECORD_NAME')

USER_DATA_TEMPLATE = """#!/bin/bash
set -euo pipefail

# Amazon Linux 2023
# Install only what's needed to keep boot fast.
dnf -y install docker htop
systemctl enable --now docker

mkdir -p /opt/minecraft

# Install Docker Compose v2 as a Docker CLI plugin (works reliably on AL2023)
if ! command -v curl >/dev/null 2>&1; then
    dnf -y install curl-minimal
fi
mkdir -p /usr/local/lib/docker/cli-plugins/
curl -fsSL https://github.com/docker/compose/releases/latest/download/docker-compose-linux-x86_64 \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

aws s3 cp s3://{config_bucket_name}/scripts/server_lifecycle.sh /opt/minecraft/server_lifecycle.sh
chmod +x /opt/minecraft/server_lifecycle.sh

/opt/minecraft/server_lifecycle.sh start {world_name} {config_bucket_name} {snapshot_bucket_name}
"""


def _json(status_code, body_obj):
    return {
        'statusCode': status_code,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body_obj, default=str)
    }


_PLAYERS_RE = re.compile(r"There are (\d+) of a max of")


def _extract_player_count(stdout: str):
    if not stdout:
        return None
    m = _PLAYERS_RE.search(stdout)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    if "There are 0" in stdout or "0 of a max" in stdout:
        return 0
    return None


def _mark_stopped(world: str):
    # Keep instance_id for traceability, but remove IPs so clients don't use stale addresses.
    table.update_item(
        Key={'world': world},
        UpdateExpression="SET #s = :s REMOVE ip_address, ipv6_address",
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'STOPPED'}
    )


def _get_instance_state(instance_id: str):
    try:
        inst_desc = ec2.describe_instances(InstanceIds=[instance_id])
        reservations = inst_desc.get('Reservations', [])
        if not reservations:
            return None
        return reservations[0]['Instances'][0]['State']['Name']
    except Exception as e:
        # InvalidInstanceID.NotFound etc.
        logger.info(f"describe_instances failed for {instance_id}: {e}")
        return None

def lambda_handler(event, context):
    logger.info(json.dumps(event))
    
    # Determine source of event (Function URL or EventBridge)
    if 'requestContext' in event and 'http' in event['requestContext']:
        # Function URL
        method = event['requestContext']['http']['method']
        if method == 'GET':
            params = event.get('queryStringParameters', {})
            action = params.get('action')
            world = params.get('world')
        elif method == 'POST':
            body = json.loads(event.get('body', '{}'))
            action = body.get('action')
            world = body.get('world')
        else:
            return {'statusCode': 405, 'body': 'Method Not Allowed'}
    else:
        # EventBridge or direct invocation
        action = event.get('action')
        world = event.get('world')

    if not action:
        return _json(400, {'error': 'Missing action'})

    if action == 'start':
        return handle_start(world)
    elif action == 'stop':
        return handle_stop(world)
    elif action == 'snapshot':
        return handle_snapshot(world)
    elif action == 'status':
        return handle_status(world)
    elif action == 'monitor':
        return handle_monitor()
    else:
        return _json(400, {'error': 'Invalid action'})


def monitor_handler(event, context):
    """Scheduled entrypoint (EventBridge) for monitoring/idle-stop."""
    logger.info(json.dumps(event))
    return handle_monitor()

def update_dns(ip_address, ipv6_address):
    if not CLOUDFLARE_API_TOKEN or not CLOUDFLARE_ZONE_ID or not DNS_RECORD_NAME:
        logger.warning("Cloudflare settings missing. Skipping DNS update.")
        return

    headers = {
        'Authorization': f'Bearer {CLOUDFLARE_API_TOKEN}',
        'Content-Type': 'application/json'
    }

    # Helper to update/create record
    def set_record(type, content):
        # 1. Get existing record
        url = f"https://api.cloudflare.com/client/v4/zones/{CLOUDFLARE_ZONE_ID}/dns_records?type={type}&name={DNS_RECORD_NAME}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as res:
                data = json.loads(res.read())
                records = data.get('result', [])
        except Exception as e:
            logger.error(f"Failed to list DNS records: {e}")
            return

        payload = {
            'type': type,
            'name': DNS_RECORD_NAME,
            'content': content,
            'ttl': 1, # Auto
            'proxied': False
        }
        
        if records:
            # Update
            record_id = records[0]['id']
            url = f"https://api.cloudflare.com/client/v4/zones/{CLOUDFLARE_ZONE_ID}/dns_records/{record_id}"
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='PUT')
        else:
            # Create
            url = f"https://api.cloudflare.com/client/v4/zones/{CLOUDFLARE_ZONE_ID}/dns_records"
            req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')

        try:
            with urllib.request.urlopen(req) as res:
                logger.info(f"Updated {type} record for {DNS_RECORD_NAME} to {content}")
        except Exception as e:
            logger.error(f"Failed to update {type} record: {e}")

    if ip_address:
        set_record('A', ip_address)
    if ipv6_address:
        set_record('AAAA', ipv6_address)

def handle_start(world):
    if not world:
        return _json(400, {'error': 'Missing world'})

    # Check DB
    response = table.get_item(Key={'world': world})
    item = response.get('Item')
    
    if item and item.get('status') in ['RUNNING', 'STARTING']:
        return _json(200, {'status': item['status'], 'ip': item.get('ip_address'), 'ipv6': item.get('ipv6_address')})

    # Get AMI (Amazon Linux 2023)
    ami_response = ec2.describe_images(
        Owners=['amazon'],
        Filters=[
            {'Name': 'name', 'Values': ['al2023-ami-2023.*-x86_64']},
            {'Name': 'state', 'Values': ['available']}
        ]
    )
    # Sort by CreationDate
    images = sorted(ami_response['Images'], key=lambda x: x['CreationDate'], reverse=True)
    image_id = images[0]['ImageId']

    user_data = USER_DATA_TEMPLATE.format(
        config_bucket_name=CONFIG_BUCKET_NAME,
        snapshot_bucket_name=SNAPSHOT_BUCKET_NAME,
        world_name=world,
    )

    # Find a subnet with IPv6 if possible
    subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [ec2.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])['Vpcs'][0]['VpcId']]}])
    subnet_id = None
    ipv6_count = 0
    
    # Try to find a subnet with IPv6 CIDR
    for sn in subnets['Subnets']:
        if sn.get('Ipv6CidrBlockAssociationSet'):
            subnet_id = sn['SubnetId']
            ipv6_count = 1
            break
    
    # Fallback to any subnet if no IPv6 found (or just pick the first one if we want to force failure? No, better to launch)
    if not subnet_id and subnets['Subnets']:
        subnet_id = subnets['Subnets'][0]['SubnetId']
        # If we really want IPv6, we might want to log a warning here
        logger.warning("No IPv6 subnet found. Launching with IPv4 only.")

    network_interface = {
        'DeviceIndex': 0,
        'AssociatePublicIpAddress': True,
        'Groups': [SECURITY_GROUP_ID],
        'SubnetId': subnet_id
    }
    
    if ipv6_count > 0:
        network_interface['Ipv6AddressCount'] = 1

    run_instances = ec2.run_instances(
        ImageId=image_id,
        InstanceType=INSTANCE_TYPE,
        MinCount=1,
        MaxCount=1,
        IamInstanceProfile={'Arn': INSTANCE_PROFILE_ARN},
        UserData=user_data,
        NetworkInterfaces=[network_interface],
        BlockDeviceMappings=[
            {
                'DeviceName': '/dev/xvda',
                'Ebs': {
                    'VolumeSize': EBS_VOLUME_SIZE,
                    'VolumeType': EBS_VOLUME_TYPE,
                    'DeleteOnTermination': True
                }
            }
        ],
        TagSpecifications=[
            {
                'ResourceType': 'instance',
                'Tags': [
                    {'Key': 'Name', 'Value': f'mc-{world}'},
                    {'Key': 'World', 'Value': world}
                ]
            }
        ]
    )
    
    instance_id = run_instances['Instances'][0]['InstanceId']
    
    table.put_item(Item={
        'world': world,
        'instance_id': instance_id,
        'status': 'STARTING',
        'last_active': int(time.time())
    })

    return _json(200, {'status': 'STARTING', 'instance_id': instance_id})

def handle_stop(world):
    if not world:
        return _json(400, {'error': 'Missing world'})

    response = table.get_item(Key={'world': world})
    item = response.get('Item')
    
    if not item or item.get('status') == 'STOPPED':
        return _json(200, {'status': 'STOPPED'})

    instance_id = item.get('instance_id')
    
    # Send Stop Command
    try:
        ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={'commands': [f'/opt/minecraft/server_lifecycle.sh stop {world} {CONFIG_BUCKET_NAME} {SNAPSHOT_BUCKET_NAME}']}
        )
    except Exception as e:
        logger.error(f"Failed to send stop command: {e}")
        # If instance is already gone, just update DB
        pass

    table.update_item(
        Key={'world': world},
        UpdateExpression="set #s = :s",
        ExpressionAttributeNames={'#s': 'status'},
        ExpressionAttributeValues={':s': 'STOPPING'}
    )

    return _json(200, {'status': 'STOPPING'})


def handle_snapshot(world):
    if not world:
        return _json(400, {'error': 'Missing world'})

    response = table.get_item(Key={'world': world})
    item = response.get('Item')

    if not item or item.get('status') in ['STOPPED', 'STARTING']:
        return _json(400, {'error': 'World is not running'})

    instance_id = item.get('instance_id')
    if not instance_id:
        return _json(400, {'error': 'Missing instance_id'})

    try:
        ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName="AWS-RunShellScript",
            Parameters={
                'commands': [
                    f'/opt/minecraft/server_lifecycle.sh snapshot {world} {CONFIG_BUCKET_NAME} {SNAPSHOT_BUCKET_NAME}'
                ]
            }
        )
    except Exception as e:
        logger.error(f"Failed to send snapshot command: {e}")
        return _json(500, {'error': 'Failed to request snapshot'})

    table.update_item(
        Key={'world': world},
        UpdateExpression="set last_active = :t",
        ExpressionAttributeValues={':t': int(time.time())}
    )

    return _json(200, {'status': 'SNAPSHOT_REQUESTED'})

def handle_status(world):
    if not world:
        return _json(400, {'error': 'Missing world'})

    response = table.get_item(Key={'world': world})
    item = response.get('Item')
    
    if not item:
        return _json(200, {'status': 'STOPPED'})
        
    # If STOPPING, reconcile to STOPPED when the instance is already gone.
    if item.get('status') == 'STOPPING':
        instance_id = item.get('instance_id')
        if not instance_id:
            _mark_stopped(world)
            item['status'] = 'STOPPED'
            item.pop('ip_address', None)
            item.pop('ipv6_address', None)
            return _json(200, item)

        state = _get_instance_state(instance_id)
        if state in [None, 'terminated', 'shutting-down', 'stopped']:
            _mark_stopped(world)
            item['status'] = 'STOPPED'
            item.pop('ip_address', None)
            item.pop('ipv6_address', None)
            return _json(200, item)

    # If STARTING, check if it has an IP yet
    if item.get('status') == 'STARTING':
        inst_desc = ec2.describe_instances(InstanceIds=[item['instance_id']])
        if inst_desc['Reservations']:
            inst = inst_desc['Reservations'][0]['Instances'][0]
            if inst.get('PublicIpAddress'):
                ipv6_addr = None
                if inst.get('NetworkInterfaces') and inst['NetworkInterfaces'][0].get('Ipv6Addresses'):
                    ipv6_addr = inst['NetworkInterfaces'][0]['Ipv6Addresses'][0]['Ipv6Address']

                update_expr = "set ip_address = :ip, #s = :s"
                expr_attr_vals = {':ip': inst['PublicIpAddress'], ':s': 'RUNNING'}
                
                if ipv6_addr:
                    update_expr += ", ipv6_address = :ipv6"
                    expr_attr_vals[':ipv6'] = ipv6_addr

                # Update DNS
                try:
                    update_dns(inst['PublicIpAddress'], ipv6_addr)
                except Exception as e:
                    logger.error(f"DNS Update failed: {e}")

                table.update_item(
                    Key={'world': world},
                    UpdateExpression=update_expr,
                    ExpressionAttributeNames={'#s': 'status'},
                    ExpressionAttributeValues=expr_attr_vals
                )
                item['status'] = 'RUNNING'
                item['ip_address'] = inst['PublicIpAddress']
                item['ipv6_address'] = ipv6_addr

    return _json(200, item)

def handle_monitor():
    # Scan for RUNNING and STOPPING instances (paginate)
    items = []
    eks = None
    while True:
        kwargs = {
            'FilterExpression': "#s = :r OR #s = :st",
            'ExpressionAttributeNames': {'#s': 'status'},
            'ExpressionAttributeValues': {':r': 'RUNNING', ':st': 'STOPPING'}
        }
        if eks:
            kwargs['ExclusiveStartKey'] = eks
        scan = table.scan(**kwargs)
        items.extend(scan.get('Items', []))
        eks = scan.get('LastEvaluatedKey')
        if not eks:
            break

    for item in items:
        world = item['world']
        instance_id = item['instance_id']

        # If STOPPING, just reconcile and continue.
        if item.get('status') == 'STOPPING':
            state = _get_instance_state(instance_id)
            if state in [None, 'terminated', 'shutting-down', 'stopped']:
                _mark_stopped(world)
            continue
        
        # Check if instance exists
        try:
            inst_desc = ec2.describe_instances(InstanceIds=[instance_id])
            state = inst_desc['Reservations'][0]['Instances'][0]['State']['Name']
            if state in ['terminated', 'shutting-down', 'stopped']:
                _mark_stopped(world)
                continue
        except Exception:
             _mark_stopped(world)
             continue

        # Check players
        try:
            cmd = ssm.send_command(
                InstanceIds=[instance_id],
                DocumentName="AWS-RunShellScript",
                Parameters={'commands': [f'/opt/minecraft/server_lifecycle.sh status {world} {CONFIG_BUCKET_NAME} {SNAPSHOT_BUCKET_NAME}']}
            )
            command_id = cmd['Command']['CommandId']
            
            # Wait for result
            for _ in range(10):
                time.sleep(1)
                output = ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id
                )
                if output['Status'] in ['Success', 'Failed', 'Cancelled', 'TimedOut']:
                    break
            
            stdout = output['StandardOutputContent']
            logger.info(f"Status output for {world}: {stdout}")

            players = _extract_player_count(stdout)
            if players is None:
                continue

            if players > 0:
                table.update_item(
                    Key={'world': world},
                    UpdateExpression="set last_active = :t",
                    ExpressionAttributeValues={':t': int(time.time())}
                )
                continue

            last_active = item.get('last_active', 0)
            try:
                last_active = float(last_active)
            except Exception:
                last_active = 0

            if time.time() - last_active > IDLE_TIMEOUT:
                logger.info(f"World {world} idle for too long. Stopping.")
                handle_stop(world)
                
        except Exception as e:
            logger.error(f"Error monitoring {world}: {e}")

    return _json(200, {'status': 'Monitor complete'})
