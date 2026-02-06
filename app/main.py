import os
import json
import aiohttp
import asyncio
import discord
from discord import app_commands
from dotenv import load_dotenv
from mcstatus import JavaServer


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
                raise RuntimeError(f"mc-control HTTP {resp.status}: {msg}")

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
    print(f'Logged in as {client.user} (ID: {client.user.id})')


mc = app_commands.Group(name='mc', description='Control the Minecraft worlds (mc-control)')


@mc.command(name='status', description='Check world status')
@app_commands.describe(world='World name (default: DEFAULT_WORLD)')
async def mc_status(interaction: discord.Interaction, world: str | None = None):
    if not _is_allowed(interaction):
        embed = create_error_embed(
            'Permission Denied',
            ERROR_MESSAGES['permission_denied'],
            ERROR_MESSAGES['permission_contact']
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    world = (world or DEFAULT_WORLD or '').strip()
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

    except Exception as e:
        error_msg = str(e)
        embed = create_error_embed(
            'Server Error',
            ERROR_MESSAGES['server_error'],
            f"{error_msg[:300]}\n\n{ERROR_MESSAGES['retry_later']}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


@mc.command(name='start', description='Start a world (creates an EC2 instance)')
@app_commands.describe(world='World name (default: DEFAULT_WORLD)')
async def mc_start(interaction: discord.Interaction, world: str | None = None):
    if not _is_allowed(interaction):
        embed = create_error_embed(
            'Permission Denied',
            ERROR_MESSAGES['permission_denied'],
            ERROR_MESSAGES['permission_contact']
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    world = (world or DEFAULT_WORLD or '').strip()
    await interaction.response.defer(ephemeral=True)

    try:
        data = await _call_mc_control('start', world)
        status = (data.get('status') or 'UNKNOWN').upper()
        instance_id = data.get('instance_id')

        # Check if server was already running
        is_already_running = status == 'RUNNING' and data.get('ip')

        # Private response
        embed = discord.Embed(title=f"Server Start", color=_status_color(status))
        embed.add_field(name='World', value=world, inline=True)
        embed.add_field(name='Status', value=status, inline=True)

        if is_already_running:
            # Already running - show connection info
            ip_v4 = data.get('ip') or data.get('ip_address')
            ip_v6 = data.get('ipv6') or data.get('ipv6_address')

            if ip_v4:
                embed.add_field(name='IPv4 Address', value=f"`{ip_v4}`", inline=False)
            if ip_v6:
                embed.add_field(name='IPv6 Address', value=f"`{ip_v6}`", inline=False)

            embed.add_field(name='Connection', value=HELP_TEXT['connection_ready'], inline=False)

            if instance_id:
                embed.set_footer(text=f"Instance ID: {instance_id}")

        else:
            # Starting now
            if instance_id:
                embed.add_field(name='Instance', value=f"`{instance_id}`", inline=False)

            eta = format_estimated_time('STARTING')
            if eta:
                embed.add_field(name='Estimated Time', value=eta, inline=False)

            embed.add_field(name='Next Step', value='/mc status で進行状況を確認できます', inline=False)
            embed.set_footer(text='サーバーが起動したら接続情報が表示されます')

        await interaction.followup.send(embed=embed, ephemeral=True)

        # Public announcement only if starting (not if already running)
        if not is_already_running:
            public_embed = discord.Embed(
                title=f"Minecraft Server Starting",
                description=f"{interaction.user.mention} がサーバーを起動しました",
                color=_status_color(status)
            )
            public_embed.add_field(name='World', value=world, inline=True)
            public_embed.add_field(name='Status', value=status, inline=True)
            eta = format_estimated_time('STARTING')
            if eta:
                public_embed.add_field(name='ETA', value=eta, inline=False)

            await interaction.channel.send(embed=public_embed)

    except Exception as e:
        error_msg = str(e)
        embed = create_error_embed(
            'Server Error',
            ERROR_MESSAGES['server_error'],
            f"{error_msg[:300]}\n\n{ERROR_MESSAGES['retry_later']}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


@mc.command(name='stop', description='Stop a world (snapshot + terminate EC2)')
@app_commands.describe(world='World name (default: DEFAULT_WORLD)')
async def mc_stop(interaction: discord.Interaction, world: str | None = None):
    if not _is_allowed(interaction):
        embed = create_error_embed(
            'Permission Denied',
            ERROR_MESSAGES['permission_denied'],
            ERROR_MESSAGES['permission_contact']
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    world = (world or DEFAULT_WORLD or '').strip()
    await interaction.response.defer(ephemeral=True)

    try:
        data = await _call_mc_control('stop', world)
        status = (data.get('status') or 'UNKNOWN').upper()

        # Private response
        embed = discord.Embed(title=f"Server Stop", color=_status_color(status))
        embed.add_field(name='World', value=world, inline=True)
        embed.add_field(name='Status', value=status, inline=True)

        # Add process flow
        process_flow = """1. Minecraftサーバーを安全に停止
2. ワールドデータをスナップショット
3. スナップショットをS3にアップロード
4. EC2インスタンスを終了"""
        embed.add_field(name='Process', value=process_flow, inline=False)

        eta = format_estimated_time('STOPPING')
        if eta:
            embed.add_field(name='Estimated Time', value=eta, inline=False)

        embed.set_footer(text=HELP_TEXT['auto_save_hint'])
        await interaction.followup.send(embed=embed, ephemeral=True)

        # Public announcement
        public_embed = discord.Embed(
            title=f"Minecraft Server Stopping",
            description=f"{interaction.user.mention} がサーバーを停止しました",
            color=_status_color(status)
        )
        public_embed.add_field(name='World', value=world, inline=True)
        public_embed.add_field(name='Status', value=status, inline=True)
        public_embed.add_field(name='Note', value=HELP_TEXT['world_data_saved'], inline=False)
        public_embed.set_footer(text='停止完了まで約1-2分かかります')
        await interaction.channel.send(embed=public_embed)

    except Exception as e:
        error_msg = str(e)
        embed = create_error_embed(
            'Server Error',
            ERROR_MESSAGES['server_error'],
            f"{error_msg[:300]}\n\n{ERROR_MESSAGES['retry_later']}"
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


@mc.command(name='snapshot', description='Create a snapshot (does not terminate EC2)')
@app_commands.describe(world='World name (default: DEFAULT_WORLD)')
async def mc_snapshot(interaction: discord.Interaction, world: str | None = None):
    if not _is_allowed(interaction):
        embed = create_error_embed(
            'Permission Denied',
            ERROR_MESSAGES['permission_denied'],
            ERROR_MESSAGES['permission_contact']
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    world = (world or DEFAULT_WORLD or '').strip()
    await interaction.response.defer(ephemeral=True)

    try:
        data = await _call_mc_control('snapshot', world)
        status = (data.get('status') or 'UNKNOWN').upper()

        # Private response
        embed = discord.Embed(title=f"Snapshot Request", color=_status_color(status))
        embed.add_field(name='World', value=world, inline=True)
        embed.add_field(name='Status', value=status, inline=True)

        if status == 'SNAPSHOT_REQUESTED':
            embed.add_field(name='Process', value='バックグラウンドでスナップショットを作成中', inline=False)
            embed.add_field(name='Note', value='サーバーは稼働し続けます。プレイ可能です', inline=False)
            embed.add_field(name='Storage', value='S3の履歴フォルダとメインフォルダの両方に保存', inline=False)

            eta = format_estimated_time('SNAPSHOT_REQUESTED')
            if eta:
                embed.set_footer(text=f"{eta}")

            await interaction.followup.send(embed=embed, ephemeral=True)

            # Public announcement
            public_embed = discord.Embed(
                title=f"Snapshot Requested",
                description=f"{interaction.user.mention} がスナップショットを作成しました",
                color=_status_color(status)
            )
            public_embed.add_field(name='World', value=world, inline=True)
            public_embed.add_field(name='Status', value=status, inline=True)
            await interaction.channel.send(embed=public_embed)

        else:
            # Failed (server not running)
            embed = create_error_embed(
                'Snapshot Error',
                'スナップショットを作成できません',
                f"現在のステータス: {status}\n\n理由: サーバーが起動している必要があります\n\n解決方法: /mc start でサーバーを起動してから再試行してください"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    except Exception as e:
        error_msg = str(e)
        # Check if it's a "not running" error
        if 'not running' in error_msg.lower() or 'not RUNNING' in error_msg:
            embed = create_error_embed(
                'Snapshot Error',
                'スナップショットを作成できません',
                "理由: サーバーが起動していません\n\n解決方法: /mc start でサーバーを起動してから再試行してください"
            )
        else:
            embed = create_error_embed(
                'Server Error',
                ERROR_MESSAGES['server_error'],
                f"{error_msg[:300]}\n\n{ERROR_MESSAGES['retry_later']}"
            )
        await interaction.followup.send(embed=embed, ephemeral=True)


client.tree.add_command(mc)

client.run(DISCORD_TOKEN)
