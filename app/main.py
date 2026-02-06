import os
import json
import aiohttp
import asyncio
import logging
import discord
from discord import app_commands
from dotenv import load_dotenv
from mcstatus import JavaServer


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

load_dotenv()


def _env(name: str, default: str | None = None) -> str | None:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v


DISCORD_TOKEN = _env('DISCORD_TOKEN')
GUILD_ID = _env('GUILD_ID')

MC_CONTROL_URL = _env('MC_CONTROL_URL')
MC_CONTROL_TOKEN = _env('MC_CONTROL_TOKEN')

DEFAULT_WORLD = _env('DEFAULT_WORLD', 'test')
EPHEMERAL_DEFAULT = (_env('EPHEMERAL_DEFAULT', 'true') or 'true').lower() in ['1', 'true', 'yes', 'y']

ALLOWED_ROLE_IDS = {
    int(x) for x in (_env('ALLOWED_ROLE_IDS', '') or '').split(',')
    if x.strip().isdigit()
}
ALLOWED_USER_IDS = {
    int(x) for x in (_env('ALLOWED_USER_IDS', '') or '').split(',')
    if x.strip().isdigit()
}


if not DISCORD_TOKEN:
    raise RuntimeError('DISCORD_TOKEN is missing')
if not MC_CONTROL_URL:
    raise RuntimeError('MC_CONTROL_URL is missing')


def _is_allowed(interaction: discord.Interaction) -> bool:
    user_id = interaction.user.id
    if ALLOWED_USER_IDS and user_id in ALLOWED_USER_IDS:
        return True

    if not ALLOWED_ROLE_IDS:
        return True

    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False

    return any(role.id in ALLOWED_ROLE_IDS for role in interaction.user.roles)


async def _call_mc_control(action: str, world: str):
    url = MC_CONTROL_URL.rstrip('/')
    headers = {'Content-Type': 'application/json'}
    if MC_CONTROL_TOKEN:
        headers['Authorization'] = f'Bearer {MC_CONTROL_TOKEN}'

    body = {'action': action, 'world': world}

    logger.info(f"Calling Lambda API: action={action}, world={world}")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, data=json.dumps(body)) as resp:
            text = await resp.text()
            try:
                data = json.loads(text) if text else {}
            except Exception:
                data = {'raw': text[:500]}

            if resp.status >= 400:
                msg = data.get('error') if isinstance(data, dict) else None
                msg = msg or (data.get('raw') if isinstance(data, dict) else None) or text[:200]
                logger.error(f"Lambda API error: HTTP {resp.status}, {msg}")
                raise RuntimeError(f"mc-control HTTP {resp.status}: {msg}")

            logger.info(f"Lambda API response: status={resp.status}, data={data}")
            return data


# Japanese message constants
STATUS_MESSAGES = {
    'STARTING': '起動中',
    'RUNNING': '稼働中',
    'STOPPING': '停止中',
    'STOPPED': '停止',
    'SNAPSHOT_REQUESTED': 'スナップショット作成中'
}

HELP_TEXT = {
    'status_stopped': 'サーバーは停止しています。起動するには /mc start を実行してください。',
    'status_starting': 'サーバーを起動中...通常2-3分かかります',
    'status_running': '以下のアドレスでMinecraftに接続できます',
    'status_stopping': 'スナップショット作成中...完了まで1-2分かかります',
    'status_snapshot': 'スナップショットを作成中...サーバーは稼働し続けます',
    'check_status_hint': 'もう一度 /mc status を実行して進行状況を確認してください',
    'stop_complete_hint': '停止完了後、データは自動的にS3に保存されます',
    'snapshot_complete_hint': 'スナップショット完了後、自動的にRUNNINGに戻ります',
    'auto_save_hint': 'データは自動的に保存されます。次回起動時に復元されます',
    'connection_ready': '上記のアドレスで接続可能です',
    'world_data_saved': 'ワールドデータは自動保存されます',
    'ipv6_unavailable': '利用不可',
}

ERROR_MESSAGES = {
    'permission_denied': 'このコマンドを実行する権限がありません',
    'permission_contact': 'サーバー管理者にお問い合わせください',
    'server_error': 'サーバーとの通信中にエラーが発生しました',
    'retry_later': 'しばらく待ってから再試行してください',
    'persistent_issue': '問題が続く場合は管理者に連絡してください',
}


def _status_color(status: str | None) -> int:
    status = (status or '').upper()
    if status == 'RUNNING':
        return 0x2ecc71
    if status in ['STARTING', 'STOPPING', 'SNAPSHOT_REQUESTED']:
        return 0xf1c40f
    return 0x95a5a6


def create_error_embed(title: str, description: str, details: str | None = None) -> discord.Embed:
    """Create a standardized error embed with Japanese messages."""
    embed = discord.Embed(title=title, description=description, color=0xe74c3c)
    if details:
        embed.add_field(name='Details', value=details, inline=False)
    return embed


def format_estimated_time(status: str) -> str:
    """Return Japanese ETA message for given status."""
    eta_map = {
        'STARTING': '約2-3分で起動完了',
        'STOPPING': '約1-2分で停止完了',
        'SNAPSHOT_REQUESTED': '約30秒-1分でスナップショット完了'
    }
    return eta_map.get(status.upper(), '')


async def check_minecraft_server(ip: str, port: int = 25565, max_retries: int = 3) -> bool:
    """Check if Minecraft server is accessible using Minecraft Ping protocol."""
    logger.info(f"Checking Minecraft server at {ip}:{port}")

    for attempt in range(max_retries):
        try:
            server = JavaServer.lookup(f"{ip}:{port}")
            status = await server.async_status()
            # If we get here, server responded successfully
            logger.info(f"Minecraft server is accessible at {ip}:{port}")
            return True
        except Exception as e:
            logger.warning(f"Minecraft ping attempt {attempt + 1}/{max_retries} failed: {e}")
            # Log the attempt but continue retrying
            if attempt < max_retries - 1:
                await asyncio.sleep(2)  # Wait 2 seconds before retry
            continue

    logger.error(f"Minecraft server at {ip}:{port} is not accessible after {max_retries} attempts")
    return False


async def wait_for_server_running(world: str, max_wait: int = 600) -> dict:
    """
    Wait for server to be fully running and accessible.

    Polls Lambda API to check status, then verifies Minecraft server is accessible.
    Returns server data dict when fully ready, or raises TimeoutError.
    """
    logger.info(f"Waiting for server '{world}' to be running (max {max_wait}s)")
    start_time = asyncio.get_event_loop().time()
    poll_interval = 10  # Poll every 10 seconds

    ip_address = None
    minecraft_check_started = False

    while True:
        elapsed = asyncio.get_event_loop().time() - start_time

        if elapsed > max_wait:
            logger.error(f"Server '{world}' startup timed out after {max_wait}s")
            raise TimeoutError(f"サーバー起動がタイムアウトしました（{max_wait}秒）")

        try:
            # Check Lambda API status
            data = await _call_mc_control('status', world)
            status = (data.get('status') or '').upper()

            if status == 'RUNNING':
                ip_address = data.get('ip_address') or data.get('ip')

                if ip_address:
                    # Start Minecraft connectivity check
                    if not minecraft_check_started:
                        minecraft_check_started = True
                        logger.info(f"Server '{world}' is RUNNING, checking Minecraft connectivity...")

                    # Check if Minecraft server is actually accessible
                    if await check_minecraft_server(ip_address):
                        # Success! Server is fully ready
                        logger.info(f"Server '{world}' is fully running and accessible at {ip_address}")
                        return data

            # Not ready yet, continue polling
            logger.debug(f"Server '{world}' status: {status}, elapsed: {elapsed:.1f}s")
            await asyncio.sleep(poll_interval)

        except Exception as e:
            # Continue polling even if there's an error
            logger.warning(f"Error while polling server '{world}': {e}")
            await asyncio.sleep(poll_interval)


async def wait_for_server_stopped(world: str, max_wait: int = 600) -> dict:
    """
    Wait for server to be fully stopped.

    Polls Lambda API to check status.
    Returns server data dict when stopped, or raises TimeoutError.
    """
    logger.info(f"Waiting for server '{world}' to be stopped (max {max_wait}s)")
    start_time = asyncio.get_event_loop().time()
    poll_interval = 10  # Poll every 10 seconds

    while True:
        elapsed = asyncio.get_event_loop().time() - start_time

        if elapsed > max_wait:
            logger.error(f"Server '{world}' stop timed out after {max_wait}s")
            raise TimeoutError(f"サーバー停止がタイムアウトしました（{max_wait}秒）")

        try:
            # Check Lambda API status
            data = await _call_mc_control('status', world)
            status = (data.get('status') or '').upper()

            if status == 'STOPPED':
                # Success! Server is fully stopped
                logger.info(f"Server '{world}' is stopped")
                return data

            # Not stopped yet, continue polling
            logger.debug(f"Server '{world}' status: {status}, elapsed: {elapsed:.1f}s")
            await asyncio.sleep(poll_interval)

        except Exception as e:
            # Continue polling even if there's an error
            logger.warning(f"Error while polling server '{world}': {e}")
            await asyncio.sleep(poll_interval)


class MinecraftBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # If GUILD_ID is set, sync to that guild for instant availability.
        if GUILD_ID and str(GUILD_ID).isdigit():
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            # Global sync can take up to ~1 hour to appear.
            await self.tree.sync()


client = MinecraftBot()


@client.event
async def on_ready():
    logger.info(f'Bot logged in as {client.user} (ID: {client.user.id})')
    print(f'Logged in as {client.user} (ID: {client.user.id})')


mc = app_commands.Group(name='mc', description='Control the Minecraft worlds (mc-control)')


@mc.command(name='status', description='Check world status')
@app_commands.describe(world='World name (default: DEFAULT_WORLD)')
async def mc_status(interaction: discord.Interaction, world: str | None = None):
    world = (world or DEFAULT_WORLD or '').strip()
    user = f"{interaction.user.name}#{interaction.user.discriminator}"
    logger.info(f"Command /mc status executed by {user} for world '{world}'")

    if not _is_allowed(interaction):
        logger.warning(f"User {user} denied access to /mc status")
        embed = create_error_embed(
            'Permission Denied',
            ERROR_MESSAGES['permission_denied'],
            ERROR_MESSAGES['permission_contact']
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    await interaction.response.defer(ephemeral=EPHEMERAL_DEFAULT)

    try:
        data = await _call_mc_control('status', world)
        status = (data.get('status') or 'UNKNOWN').upper()

        embed = discord.Embed(title=f"Minecraft Server Status", color=_status_color(status))
        embed.add_field(name='World', value=world, inline=True)
        embed.add_field(name='Status', value=status, inline=True)

        if status == 'STOPPED':
            embed.add_field(name='Info', value=HELP_TEXT['status_stopped'], inline=False)

        elif status == 'STARTING':
            instance_id = data.get('instance_id')
            if instance_id:
                embed.add_field(name='Instance', value=f"`{instance_id}`", inline=False)
            embed.add_field(name='Progress', value=HELP_TEXT['status_starting'], inline=False)
            embed.set_footer(text=HELP_TEXT['check_status_hint'])

        elif status == 'RUNNING':
            ip_v4 = data.get('ip_address')
            ip_v6 = data.get('ipv6_address')

            if ip_v4:
                embed.add_field(name='IPv4 Address', value=f"`{ip_v4}`", inline=False)
            if ip_v6:
                embed.add_field(name='IPv6 Address', value=f"`{ip_v6}`", inline=False)
            else:
                embed.add_field(name='IPv6 Address', value=HELP_TEXT['ipv6_unavailable'], inline=False)

            embed.add_field(name='Connection', value=HELP_TEXT['status_running'], inline=False)

            instance_id = data.get('instance_id')
            if instance_id:
                embed.set_footer(text=f"Instance ID: {instance_id}")

        elif status == 'STOPPING':
            instance_id = data.get('instance_id')
            if instance_id:
                embed.add_field(name='Instance', value=f"`{instance_id}`", inline=False)
            embed.add_field(name='Progress', value=HELP_TEXT['status_stopping'], inline=False)
            embed.set_footer(text=HELP_TEXT['stop_complete_hint'])

        elif status == 'SNAPSHOT_REQUESTED':
            instance_id = data.get('instance_id')
            if instance_id:
                embed.add_field(name='Instance', value=f"`{instance_id}`", inline=False)
            embed.add_field(name='Progress', value=HELP_TEXT['status_snapshot'], inline=False)
            embed.set_footer(text=HELP_TEXT['snapshot_complete_hint'])

        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(f"Status response sent to {user} for world '{world}': {status}")

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error in /mc status for world '{world}': {error_msg}")
        embed = create_error_embed(
            'Server Error',
            ERROR_MESSAGES['server_error'],
            f"{error_msg[:300]}\n\n{ERROR_MESSAGES['retry_later']}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


@mc.command(name='start', description='Start a world (creates an EC2 instance)')
@app_commands.describe(world='World name (default: DEFAULT_WORLD)')
async def mc_start(interaction: discord.Interaction, world: str | None = None):
    world = (world or DEFAULT_WORLD or '').strip()
    user = f"{interaction.user.name}#{interaction.user.discriminator}"
    logger.info(f"Command /mc start executed by {user} for world '{world}'")

    if not _is_allowed(interaction):
        logger.warning(f"User {user} denied access to /mc start")
        embed = create_error_embed(
            'Permission Denied',
            ERROR_MESSAGES['permission_denied'],
            ERROR_MESSAGES['permission_contact']
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Send immediate acknowledgment (private)
    await interaction.response.send_message(
        f"リクエストを受け付けました。サーバーを起動中...\n最大10分程度かかる場合があります。",
        ephemeral=True
    )

    try:
        # Request server start from Lambda
        await _call_mc_control('start', world)

        # Wait for server to be fully running and accessible
        data = await wait_for_server_running(world, max_wait=600)

        # Server is ready! Send public notification
        ip_v4 = data.get('ip_address')
        ip_v6 = data.get('ipv6_address')

        embed = discord.Embed(
            title="Minecraft Server Ready",
            description=f"**{world}** が起動しました。接続できます！",
            color=0x2ecc71  # Green
        )

        if ip_v4:
            embed.add_field(name='IPv4 Address', value=f"`{ip_v4}`", inline=False)
        if ip_v6:
            embed.add_field(name='IPv6 Address', value=f"`{ip_v6}`", inline=False)

        await interaction.channel.send(embed=embed)

        # Also update the private message
        await interaction.followup.send("サーバーが起動しました！", ephemeral=True)
        logger.info(f"Server '{world}' successfully started by {user} at {ip_v4}")

    except TimeoutError as e:
        # Timeout - server didn't start in time
        logger.error(f"Server '{world}' start timed out for user {user}: {e}")
        error_embed = create_error_embed(
            'Timeout',
            'サーバー起動がタイムアウトしました',
            f"{str(e)}\n\n/mc status で現在の状態を確認してください。"
        )
        await interaction.followup.send(embed=error_embed, ephemeral=True)

    except Exception as e:
        # Other errors
        error_msg = str(e)
        logger.error(f"Error in /mc start for world '{world}' by {user}: {error_msg}")
        embed = create_error_embed(
            'Server Error',
            ERROR_MESSAGES['server_error'],
            f"{error_msg[:300]}\n\n{ERROR_MESSAGES['retry_later']}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


@mc.command(name='stop', description='Stop a world (snapshot + terminate EC2)')
@app_commands.describe(world='World name (default: DEFAULT_WORLD)')
async def mc_stop(interaction: discord.Interaction, world: str | None = None):
    world = (world or DEFAULT_WORLD or '').strip()
    user = f"{interaction.user.name}#{interaction.user.discriminator}"
    logger.info(f"Command /mc stop executed by {user} for world '{world}'")

    if not _is_allowed(interaction):
        logger.warning(f"User {user} denied access to /mc stop")
        embed = create_error_embed(
            'Permission Denied',
            ERROR_MESSAGES['permission_denied'],
            ERROR_MESSAGES['permission_contact']
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Send immediate acknowledgment (private)
    await interaction.response.send_message(
        f"リクエストを受け付けました。サーバーを停止中...\n最大10分程度かかる場合があります。",
        ephemeral=True
    )

    try:
        # Request server stop from Lambda
        await _call_mc_control('stop', world)

        # Wait for server to be fully stopped
        data = await wait_for_server_stopped(world, max_wait=600)

        # Server is stopped! Send public notification
        embed = discord.Embed(
            title="Minecraft Server Stopped",
            description=f"**{world}** が停止しました。",
            color=0x95a5a6  # Gray
        )
        embed.add_field(name='Status', value='STOPPED', inline=False)
        embed.set_footer(text='ワールドデータは自動的にS3に保存されています')

        await interaction.channel.send(embed=embed)

        # Also update the private message
        await interaction.followup.send("サーバーが停止しました。", ephemeral=True)
        logger.info(f"Server '{world}' successfully stopped by {user}")

    except TimeoutError as e:
        # Timeout - server didn't stop in time
        logger.error(f"Server '{world}' stop timed out for user {user}: {e}")
        error_embed = create_error_embed(
            'Timeout',
            'サーバー停止がタイムアウトしました',
            f"{str(e)}\n\n/mc status で現在の状態を確認してください。"
        )
        await interaction.followup.send(embed=error_embed, ephemeral=True)

    except Exception as e:
        # Other errors
        error_msg = str(e)
        logger.error(f"Error in /mc stop for world '{world}' by {user}: {error_msg}")
        embed = create_error_embed(
            'Server Error',
            ERROR_MESSAGES['server_error'],
            f"{error_msg[:300]}\n\n{ERROR_MESSAGES['retry_later']}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


client.tree.add_command(mc)

logger.info("Starting Discord bot...")
client.run(DISCORD_TOKEN)
