import os
import json
import aiohttp
import discord
from discord import app_commands
from dotenv import load_dotenv


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


def _status_color(status: str | None) -> int:
    status = (status or '').upper()
    if status == 'RUNNING':
        return 0x2ecc71
    if status in ['STARTING', 'STOPPING', 'SNAPSHOT_REQUESTED']:
        return 0xf1c40f
    return 0x95a5a6


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
        await interaction.response.send_message('Not allowed.', ephemeral=True)
        return

    world = (world or DEFAULT_WORLD or '').strip()
    await interaction.response.defer(ephemeral=EPHEMERAL_DEFAULT)

    try:
        data = await _call_mc_control('status', world)
        status = (data.get('status') or 'UNKNOWN')
        embed = discord.Embed(title=f"World: {world}", color=_status_color(status))
        embed.add_field(name='Status', value=status, inline=True)

        # IP address is hidden as requested
        # ip4 = data.get('ip_address') or data.get('ip')
        # ip6 = data.get('ipv6_address') or data.get('ipv6')
        # ...

        instance_id = data.get('instance_id')
        if instance_id:
            embed.set_footer(text=f"instance_id: {instance_id}")

        await interaction.followup.send(embed=embed, ephemeral=True)
    except Exception as e:
        await interaction.followup.send(
            embed=discord.Embed(title='Error', description=str(e)[:1800], color=0xe74c3c),
            ephemeral=True
        )


@mc.command(name='start', description='Start a world (creates an EC2 instance)')
@app_commands.describe(world='World name (default: DEFAULT_WORLD)')
async def mc_start(interaction: discord.Interaction, world: str | None = None):
    if not _is_allowed(interaction):
        await interaction.response.send_message('Not allowed.', ephemeral=True)
        return

    world = (world or DEFAULT_WORLD or '').strip()
    await interaction.response.defer(ephemeral=True)
    try:
        data = await _call_mc_control('start', world)
        status = data.get('status') or 'UNKNOWN'
        instance_id = data.get('instance_id')

        # Private response
        embed = discord.Embed(title=f"Start: {world}", color=_status_color(status))
        embed.add_field(name='Status', value=status, inline=True)
        if instance_id:
            embed.add_field(name='Instance', value=f"`{instance_id}`", inline=False)
        embed.set_footer(text='Hint: /mc status to check progress')
        await interaction.followup.send(embed=embed, ephemeral=True)

        # Public announcement
        public_embed = discord.Embed(
            title=f"Server Starting: {world}",
            description=f"Initiated by {interaction.user.mention}",
            color=_status_color(status)
        )
        public_embed.add_field(name='Status', value=status, inline=True)
        await interaction.channel.send(embed=public_embed)

    except Exception as e:
        await interaction.followup.send(
            embed=discord.Embed(title='Error', description=str(e)[:1800], color=0xe74c3c),
            ephemeral=True
        )


@mc.command(name='stop', description='Stop a world (snapshot + terminate EC2)')
@app_commands.describe(world='World name (default: DEFAULT_WORLD)')
async def mc_stop(interaction: discord.Interaction, world: str | None = None):
    if not _is_allowed(interaction):
        await interaction.response.send_message('Not allowed.', ephemeral=True)
        return

    world = (world or DEFAULT_WORLD or '').strip()
    await interaction.response.defer(ephemeral=True)
    try:
        data = await _call_mc_control('stop', world)
        status = data.get('status') or 'UNKNOWN'
        
        # Private response
        embed = discord.Embed(title=f"Stop: {world}", color=_status_color(status))
        embed.add_field(name='Status', value=status, inline=True)
        embed.set_footer(text='It may take a bit: snapshot upload + terminate')
        await interaction.followup.send(embed=embed, ephemeral=True)

        # Public announcement
        public_embed = discord.Embed(
            title=f"Server Stopping: {world}",
            description=f"Initiated by {interaction.user.mention}",
            color=_status_color(status)
        )
        public_embed.add_field(name='Status', value=status, inline=True)
        public_embed.set_footer(text="Snapshot will be saved automatically.")
        await interaction.channel.send(embed=public_embed)

    except Exception as e:
        await interaction.followup.send(
            embed=discord.Embed(title='Error', description=str(e)[:1800], color=0xe74c3c),
            ephemeral=True
        )


@mc.command(name='snapshot', description='Create a snapshot (does not terminate EC2)')
@app_commands.describe(world='World name (default: DEFAULT_WORLD)')
async def mc_snapshot(interaction: discord.Interaction, world: str | None = None):
    if not _is_allowed(interaction):
        await interaction.response.send_message('Not allowed.', ephemeral=True)
        return

    world = (world or DEFAULT_WORLD or '').strip()
    await interaction.response.defer(ephemeral=True)
    try:
        data = await _call_mc_control('snapshot', world)
        status = data.get('status') or 'UNKNOWN'
        
        # Private response
        embed = discord.Embed(title=f"Snapshot: {world}", color=_status_color(status))
        embed.add_field(name='Status', value=status, inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

        # Public announcement
        public_embed = discord.Embed(
            title=f"Snapshot Requested: {world}",
            description=f"Initiated by {interaction.user.mention}",
            color=_status_color(status)
        )
        public_embed.add_field(name='Status', value=status, inline=True)
        await interaction.channel.send(embed=public_embed)

    except Exception as e:
        await interaction.followup.send(
            embed=discord.Embed(title='Error', description=str(e)[:1800], color=0xe74c3c),
            ephemeral=True
        )


client.tree.add_command(mc)

client.run(DISCORD_TOKEN)
