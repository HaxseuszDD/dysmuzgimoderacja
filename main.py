import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta
import asyncio
import aiohttp
import json
import os

from keep_alive import keep_alive

keep_alive()

# --------- KONFIGURACJA ---------
MUTE_ROLE_ID = 1389325433161646241  # ID roli "Wyciszony"
LOG_CHANNEL_ID = 1388833060933337129  # ID kanału logów

UPTIME_ROBOT_URL = "https://10f7dc3b-c58d-4bd9-a7f5-0007e7a53bbb-00-3i9bf2ihu3ras.riker.replit.dev/"  # Podmień na swój URL
MUTE_LOG_FILE = "mute_logi_role.txt"  # Plik do zapisywania backupu ról

# Mapowanie komend na listę ID ról, które mogą ich używać
PERMISSIONS = {
    "mute": [1388937014379810916, 1388937017185800375, 1388938738574557305, 1388939460372070510, 1386884881363243140, 1386884886341750886],
    "unmute": [1388937014379810916, 1388937017185800375, 1388938738574557305, 1388939460372070510, 1386884881363243140],
    "ban": [1389326265063837706, 1389326194079567912, 1388939460372070510],
    "warn": [1386884859502399520, 1386884865265369119, 1386884871062028480, 1386884876233474089, 1386884881363243140, 1386884886341750886],
}

# Pobieramy token z secret environment variable
def get_token():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Brak zmiennej środowiskowej DISCORD_TOKEN!")
    return token

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

def load_mute_log():
    if not os.path.isfile(MUTE_LOG_FILE):
        return {}
    with open(MUTE_LOG_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_mute_log(data):
    with open(MUTE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def has_permission(member: discord.Member, command_name: str) -> bool:
    allowed_roles = PERMISSIONS.get(command_name, [])
    if not allowed_roles:
        return False
    return any(role.id in allowed_roles for role in member.roles)

async def send_log_embed(title: str, user: discord.Member, moderator: discord.Member, reason: str, extra: dict = None):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        print("Nie znaleziono kanału logów!")
        return

    description = f"Użytkownik: {user} ({user.id})\nModerator: {moderator.display_name}\nPowód: {reason}"
    if extra:
        for key, value in extra.items():
            description += f"\n{key}: {value}"

    embed = discord.Embed(title=title, description=description, color=discord.Color.orange(), timestamp=datetime.utcnow())
    await channel.send(embed=embed)

@tree.command(name="mute", description="Wycisz użytkownika")
@app_commands.describe(user="Użytkownik do wyciszenia", reason="Powód wyciszenia", czas="Czas wyciszenia w minutach")
async def mute(interaction: discord.Interaction, user: discord.Member, reason: str, czas: int):
    if not has_permission(interaction.user, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    role = interaction.guild.get_role(MUTE_ROLE_ID)
    if not role:
        await interaction.followup.send("Nie znaleziono roli wyciszonego.", ephemeral=True)
        return

    if role in user.roles:
        await interaction.followup.send("Ten użytkownik jest już wyciszony.", ephemeral=True)
        return

    roles_to_remove = [r for r in user.roles if r.id != interaction.guild.id and r.id != MUTE_ROLE_ID]

    mute_log = load_mute_log()
    mute_log[str(user.id)] = [r.id for r in roles_to_remove]
    save_mute_log(mute_log)

    try:
        await user.remove_roles(*roles_to_remove, reason="Backup i usunięcie przed mute")
        await user.add_roles(role, reason=reason)
    except Exception as e:
        await interaction.followup.send(f"Błąd przy nadawaniu/odbieraniu roli: {e}", ephemeral=True)
        return

    koniec = datetime.utcnow() + timedelta(minutes=czas)
    koniec_timestamp = int(koniec.timestamp())

    await send_log_embed(
        title="`🔇` Mute",
        user=user,
        moderator=interaction.user,
        reason=reason,
        extra={
            "Czas": f"{czas} minut",
            "Koniec wyciszenia": f"<t:{koniec_timestamp}:F>"
        }
    )
    await interaction.followup.send(f"✅ Użytkownik {user} został wyciszony na {czas} minut z powodu: {reason}", ephemeral=True)

    async def unmute_task():
        await asyncio.sleep(czas * 60)
        if role in user.roles:
            try:
                await user.remove_roles(role, reason="Koniec wyciszenia")

                mute_log = load_mute_log()
                role_ids = mute_log.get(str(user.id), [])
                roles_to_add = [interaction.guild.get_role(rid) for rid in role_ids if interaction.guild.get_role(rid)]
                if roles_to_add:
                    await user.add_roles(*roles_to_add, reason="Przywrócenie ról po wyciszeniu")

                if str(user.id) in mute_log:
                    mute_log.pop(str(user.id))
                    save_mute_log(mute_log)

                await send_log_embed(
                    title="🔈 Unmute (automatyczny)",
                    user=user,
                    moderator=bot.user,
                    reason="Koniec czasu wyciszenia"
                )
            except Exception as e:
                print(f"Błąd przy zdejmowaniu roli wyciszenia/przywracaniu ról: {e}")

    bot.loop.create_task(unmute_task())

@tree.command(name="unmute", description="Odcisz użytkownika")
@app_commands.describe(user="Użytkownik do odciszenia", reason="Powód odciszenia")
async def unmute(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not has_permission(interaction.user, "unmute"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    role = interaction.guild.get_role(MUTE_ROLE_ID)
    if not role:
        await interaction.followup.send("Nie znaleziono roli wyciszonego.", ephemeral=True)
        return

    if role not in user.roles:
        await interaction.followup.send("Ten użytkownik nie jest wyciszony.", ephemeral=True)
        return

    try:
        await user.remove_roles(role, reason=reason)

        mute_log = load_mute_log()
        role_ids = mute_log.get(str(user.id), [])
        roles_to_add = [interaction.guild.get_role(rid) for rid in role_ids if interaction.guild.get_role(rid)]
        if roles_to_add:
            await user.add_roles(*roles_to_add, reason="Przywrócenie ról po unmute")

        if str(user.id) in mute_log:
            mute_log.pop(str(user.id))
            save_mute_log(mute_log)

    except Exception as e:
        await interaction.followup.send(f"Błąd przy zdejmowaniu roli: {e}", ephemeral=True)
        return

    await send_log_embed(
        title="`🔈` Unmute",
        user=user,
        moderator=interaction.user,
        reason=reason
    )
    await interaction.followup.send(f"✅ Użytkownik {user} został odciszony.", ephemeral=True)

@tree.command(name="warn", description="Ostrzeż użytkownika")
@app_commands.describe(user="Użytkownik do ostrzeżenia", reason="Powód ostrzeżenia")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not has_permission(interaction.user, "warn"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    await send_log_embed(
        title="`⚠️` Warn",
        user=user,
        moderator=interaction.user,
        reason=reason
    )
    await interaction.followup.send(f"✅ Użytkownik {user} został ostrzeżony z powodu: {reason}", ephemeral=True)

@tree.command(name="ban", description="Zbanuj użytkownika")
@app_commands.describe(user="Użytkownik do zbanowania", reason="Powód bana")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not has_permission(interaction.user, "ban"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    try:
        await user.ban(reason=reason)
    except Exception as e:
        await interaction.followup.send(f"Błąd przy banowaniu: {e}", ephemeral=True)
        return

    await send_log_embed(
        title="`⛔` Ban",
        user=user,
        moderator=interaction.user,
        reason=reason
    )
    await interaction.followup.send(f"✅ Użytkownik {user} został zbanowany.", ephemeral=True)

@bot.event
async def on_ready():
    print(f"Zalogowano jako {bot.user}")
    try:
        await tree.sync()
        print("Slash commands zsynchronizowane!")
    except Exception as e:
        print(f"Błąd synchronizacji slash commands: {e}")

    await update_presence()
    update_presence_loop.start()
    ping_uptimerobot.start()

async def update_presence():
    for guild in bot.guilds:
        member_count = guild.member_count
        activity = discord.Activity(type=discord.ActivityType.watching, name=f"Imperium kebabow {member_count} osób")
        await bot.change_presence(activity=activity)
        break

@tasks.loop(minutes=10)
async def update_presence_loop():
    await update_presence()

@tasks.loop(minutes=5)
async def ping_uptimerobot():
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(UPTIME_ROBOT_URL) as resp:
                if resp.status == 200:
                    print("UptimeRobot: Ping OK")
                else:
                    print(f"UptimeRobot: Błąd pingowania, status {resp.status}")
        except Exception as e:
            print(f"UptimeRobot: Wyjątek pingowania: {e}")

@ping_uptimerobot.before_loop
async def before_ping():
    await bot.wait_until_ready()

bot.run(get_token())
