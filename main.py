import os
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta

import discord
from discord.ext import tasks, commands
from discord import app_commands

import keep_alive  # zakładam, że masz ten plik i funkcję keep_alive()


# --- KONFIGURACJA ---

GUILD_ID = 123456789012345678  # <- tutaj wstaw ID swojego serwera (int)
guild_obj = discord.Object(id=GUILD_ID)

MUTE_ROLE_ID = 1389325433161646241
LOG_CHANNEL_ID = 1388833060933337129
UPTIME_ROBOT_URL = "https://10f7dc3b-c58d-4bd9-a7f5-0007e7a53bbb-00-3i9bf2ihu3ras.riker.replit.dev/"
MUTE_LOG_FILE = "mute_logi_role.txt"

PERMISSIONS = {
    "mute": [1388937014379810916, 1388937017185800375, 1388938738574557305, 1388939460372070510, 1386884881363243140, 1386884886341750886],
    "unmute": [1388937014379810916, 1388937017185800375, 1388938738574557305, 1388939460372070510, 1386884881363243140],
    "ban": [1389326265063837706, 1389326194079567912, 1388939460372070510],
    "warn": [1386884859502399520, 1386884865265369119, 1386884871062028480, 1386884876233474089, 1386884881363243140, 1386884886341750886],
}


# --- FUNKCJE POMOCNICZE ---

def has_permission(member: discord.Member, command_name: str) -> bool:
    required_roles = PERMISSIONS.get(command_name, [])
    return any(role.id in required_roles for role in member.roles)


def load_mute_log() -> dict:
    try:
        with open(MUTE_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_mute_log(data: dict):
    with open(MUTE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


async def send_log_embed(bot: commands.Bot, embed: discord.Embed):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        await channel.send(embed=embed)


# --- BOT I KOMENDY ---

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.remove_command("help")


@bot.event
async def on_ready():
    await bot.tree.sync(guild=guild_obj)  # synchronizacja tylko na 1 guildzie
    print(f"Zalogowano jako: {bot.user} (ID: {bot.user.id})")
    print(f"Slash commands zsynchronizowane dla guild: {GUILD_ID}")
    update_status.start()
    ping_uptime.start()


@tasks.loop(minutes=10)
async def update_status():
    try:
        member_count = sum(guild.member_count for guild in bot.guilds)
        status = f"Imperium kebabow {member_count} osób"
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=status))
    except Exception as e:
        print(f"Błąd podczas aktualizacji statusu: {e}")


@tasks.loop(minutes=5)
async def ping_uptime():
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(UPTIME_ROBOT_URL) as resp:
                await resp.text()
                print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}] Ping uptime, status: {resp.status}")
    except Exception as e:
        print(f"Błąd podczas pingowania uptime: {e}")


# --- KOMENDY SLASH ---


@bot.tree.command(name="mute", description="Wycisza użytkownika na określony czas (w minutach)", guild=guild_obj)
@app_commands.describe(user="Użytkownik do wyciszenia", time="Czas wyciszenia w minutach", reason="Powód wyciszenia")
async def mute(interaction: discord.Interaction, user: discord.Member, time: int, reason: str):
    if interaction.guild.id != GUILD_ID:
        await interaction.response.send_message("Ta komenda działa tylko na serwerze docelowym.", ephemeral=True)
        return

    if not has_permission(interaction.user, "mute"):
        await interaction.response.send_message("Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    mute_role = interaction.guild.get_role(MUTE_ROLE_ID)
    if not mute_role:
        await interaction.response.send_message("Rola wyciszenia (Muted) nie została znaleziona.", ephemeral=True)
        return

    if mute_role in user.roles:
        await interaction.response.send_message("Ten użytkownik jest już wyciszony.", ephemeral=True)
        return

    roles_to_remove = [role.id for role in user.roles if role.id != interaction.guild.id and role.id != MUTE_ROLE_ID]

    mute_log = load_mute_log()
    unmute_time = datetime.utcnow() + timedelta(minutes=time)
    mute_log[str(user.id)] = {
        "roles": roles_to_remove,
        "unmute_time": unmute_time.isoformat(),
        "reason": reason
    }
    save_mute_log(mute_log)

    try:
        await user.remove_roles(*[interaction.guild.get_role(rid) for rid in roles_to_remove], reason=f"Wyciszenie przez {interaction.user} na {time} minut")
        await user.add_roles(mute_role, reason=f"Wyciszenie przez {interaction.user} na {time} minut")
    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas nadawania/odejmowania ról: {e}", ephemeral=True)
        return

    await interaction.response.send_message(f"{user.mention} został wyciszony na {time} minut. Powód: {reason}", ephemeral=False)

    embed = discord.Embed(
        title="🔇 Mute",
        color=discord.Color.orange(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Użytkownik", value=f"{user.mention}", inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=False)
    embed.add_field(name="Powód", value=reason, inline=False)
    embed.add_field(name="Czas", value=f"{time} minut", inline=False)
    embed.add_field(name="Koniec wyciszenia", value=f"<t:{int(unmute_time.timestamp())}:F>", inline=False)

    await send_log_embed(bot, embed)

    # Auto unmute po czasie
    await asyncio.sleep(time * 60)
    mute_log = load_mute_log()
    if str(user.id) in mute_log:
        try:
            await user.remove_roles(mute_role, reason="Automatyczne odciszenie po czasie mute")
            roles_ids = mute_log[str(user.id)]["roles"]
            roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
            await user.add_roles(*roles, reason="Przywrócenie ról po odciszeniu")
            del mute_log[str(user.id)]
            save_mute_log(mute_log)

            embed = discord.Embed(
                title="🔈 Unmute (auto)",
                description=f"Automatyczne odciszenie użytkownika {user.mention} po upływie {time} minut.",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            await send_log_embed(bot, embed)
        except Exception as e:
            embed = discord.Embed(
                title="⚠️ Błąd przy auto unmute",
                description=f"Nie udało się odciszyć {user.mention} automatycznie:\n{e}",
                color=discord.Color.red(),
                timestamp=datetime.utcnow()
            )
            await send_log_embed(bot, embed)


@bot.tree.command(name="unmute", description="Odcisza użytkownika ręcznie.", guild=guild_obj)
@app_commands.describe(user="Użytkownik do odciszenia", reason="Powód odciszenia (opcjonalny)")
async def unmute(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak podanego powodu"):
    if interaction.guild.id != GUILD_ID:
        await interaction.response.send_message("Ta komenda działa tylko na serwerze docelowym.", ephemeral=True)
        return

    if not has_permission(interaction.user, "unmute"):
        await interaction.response.send_message("Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    mute_role = interaction.guild.get_role(MUTE_ROLE_ID)
    if not mute_role:
        await interaction.response.send_message("Rola wyciszenia (Muted) nie została znaleziona.", ephemeral=True)
        return

    if mute_role not in user.roles:
        await interaction.response.send_message("Ten użytkownik nie jest wyciszony.", ephemeral=True)
        return

    mute_log = load_mute_log()
    if str(user.id) not in mute_log:
        await interaction.response.send_message("Nie znaleziono danych o wyciszeniu tego użytkownika.", ephemeral=True)
        return

    try:
        await user.remove_roles(mute_role, reason=f"Odciszenie przez {interaction.user}")
        roles_ids = mute_log[str(user.id)]["roles"]
        roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
        await user.add_roles(*roles, reason=f"Odciszenie przez {interaction.user}")
        del mute_log[str(user.id)]
        save_mute_log(mute_log)
    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas przywracania ról: {e}", ephemeral=True)
        return

    await interaction.response.send_message(f"{user.mention} został odciszony. Powód: {reason}", ephemeral=False)

    embed = discord.Embed(
        title="🔈 Unmute",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Użytkownik", value=user.mention, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=False)
    embed.add_field(name="Powód", value=reason, inline=False)
    await send_log_embed(bot, embed)


@bot.tree.command(name="warn", description="Ostrzega użytkownika.", guild=guild_obj)
@app_commands.describe(user="Użytkownik do ostrzeżenia", reason="Powód ostrzeżenia")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    if interaction.guild.id != GUILD_ID:
        await interaction.response.send_message("Ta komenda działa tylko na serwerze docelowym.", ephemeral=True)
        return

    if not has_permission(interaction.user, "warn"):
        await interaction.response.send_message("Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    embed = discord.Embed(
        title="⚠️ Warn",
        color=discord.Color.gold(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Użytkownik", value=user.mention, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=False)
    embed.add_field(name="Powód", value=reason, inline=False)

    await send_log_embed(bot, embed)
    await interaction.response.send_message(f"{user.mention} został ostrzeżony. Powód: {reason}", ephemeral=False)


@bot.tree.command(name="ban", description="Banuje użytkownika.", guild=guild_obj)
@app_commands.describe(user="Użytkownik do zbanowania", reason="Powód bana")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str):
    if interaction.guild.id != GUILD_ID:
        await interaction.response.send_message("Ta komenda działa tylko na serwerze docelowym.", ephemeral=True)
        return

    if not has_permission(interaction.user, "ban"):
        await interaction.response.send_message("Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    try:
        await user.ban(reason=f"Ban przez {interaction.user} Powód: {reason}")
    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas banowania użytkownika: {e}", ephemeral=True)
        return

    embed = discord.Embed(
        title="⛔ Ban",
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Użytkownik", value=user.mention, inline=False)
    embed.add_field(name="Moderator", value=interaction.user.mention, inline=False)
    embed.add_field(name="Powód", value=reason, inline=False)

    await send_log_embed(bot, embed)
    await interaction.response.send_message(f"{user.mention} został zbanowany. Powód: {reason}", ephemeral=False)


# --- URUCHOMIENIE ---

keep_alive.keep_alive()  # uruchom keep_alive (zakładam, że w tym pliku jest funkcja keep_alive)

bot.run(os.getenv("DISCORD_TOKEN"))
