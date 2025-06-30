import os
import json
import asyncio
import aiohttp
from datetime import datetime, timedelta

import discord
from discord.ext import tasks, commands
from discord import app_commands

import keep_alive  # import pliku keep_alive (kod nie jest podawany)


# --- KONFIGURACJA ---

MUTE_ROLE_ID = 1389325433161646241  # ID roli Muted
LOG_CHANNEL_ID = 1388833060933337129  # ID kanału logów
UPTIME_ROBOT_URL = "https://10f7dc3b-c58d-4bd9-a7f5-0007e7a53bbb-00-3i9bf2ihu3ras.riker.replit.dev/"  # przykładowy URL (podmień na swój)
MUTE_LOG_FILE = "mute_logi_role.txt"  # plik do backupu ról wyciszonych

PERMISSIONS = {
    "mute": [1388937014379810916, 1388937017185800375, 1388938738574557305, 1388939460372070510, 1386884881363243140, 1386884886341750886],
    "unmute": [1388937014379810916, 1388937017185800375, 1388938738574557305, 1388939460372070510, 1386884881363243140],
    "ban": [1389326265063837706, 1389326194079567912, 1388939460372070510],
    "warn": [1386884859502399520, 1386884865265369119, 1386884871062028480, 1386884876233474089, 1386884881363243140, 1386884886341750886],
}


# --- FUNKCJE POMOCNICZE ---

def has_permission(member: discord.Member, command_name: str) -> bool:
    """Sprawdza, czy użytkownik ma wymaganą rolę do danej komendy."""
    required_roles = PERMISSIONS.get(command_name, [])
    return any(role.id in required_roles for role in member.roles)


def load_mute_log() -> dict:
    """Wczytuje z pliku JSON backup ról wyciszonych użytkowników."""
    try:
        with open(MUTE_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_mute_log(data: dict):
    """Zapisuje do pliku JSON backup ról wyciszonych użytkowników."""
    with open(MUTE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


async def send_log_embed(bot: commands.Bot, title: str, description: str, color: discord.Color = discord.Color.blue()):
    """Wysyła embed do kanału logów."""
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=datetime.utcnow()
        )
        await channel.send(embed=embed)


# --- BOT I KOMENDY ---

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Usuwamy domyślne help command, jeśli potrzebne (opcjonalne)
bot.remove_command("help")


@bot.event
async def on_ready():
    """Event po uruchomieniu bota."""
    await bot.tree.sync()  # synchronizacja slash commandów
    print(f"Zalogowano jako: {bot.user} (ID: {bot.user.id})")
    print("Slash commands zsynchronizowane.")
    update_status.start()
    ping_uptime.start()


@tasks.loop(minutes=10)
async def update_status():
    """Pętla aktualizująca status bota co 10 minut."""
    try:
        guilds = bot.guilds
        member_count = sum(guild.member_count for guild in guilds)
        status = f"Watching gg/goatyrblx {member_count} osób"
        await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=status))
    except Exception as e:
        print(f"Błąd podczas aktualizacji statusu: {e}")


@tasks.loop(minutes=5)
async def ping_uptime():
    """Pętla pingująca URL uptime co 5 minut."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(UPTIME_ROBOT_URL) as resp:
                text = await resp.text()
                print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}] Ping uptime, status: {resp.status}")
    except Exception as e:
        print(f"Błąd podczas pingowania uptime: {e}")


# --- KOMENDY SLASH ---

@bot.tree.command(name="mute", description="Wycisza użytkownika na określony czas (w minutach).")
@app_commands.describe(user="Użytkownik do wyciszenia", time="Czas wyciszenia w minutach")
async def mute(interaction: discord.Interaction, user: discord.Member, time: int):
    # Sprawdzenie uprawnień
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

    # Backup obecnych ról użytkownika (oprócz @everyone i roli Muted)
    roles_to_remove = [role.id for role in user.roles if role.id != interaction.guild.id and role.id != MUTE_ROLE_ID]

    mute_log = load_mute_log()
    mute_log[str(user.id)] = {
        "roles": roles_to_remove,
        "unmute_time": (datetime.utcnow() + timedelta(minutes=time)).isoformat()
    }
    save_mute_log(mute_log)

    # Usunięcie ról i dodanie roli Muted
    try:
        await user.remove_roles(*[interaction.guild.get_role(rid) for rid in roles_to_remove], reason=f"Wyciszenie przez {interaction.user} na {time} minut")
        await user.add_roles(mute_role, reason=f"Wyciszenie przez {interaction.user} na {time} minut")
    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas nadawania/odejmowania ról: {e}", ephemeral=True)
        return

    await interaction.response.send_message(f"{user.mention} został wyciszony na {time} minut.", ephemeral=False)

    # Logowanie
    desc = f"Użytkownik {user.mention} został wyciszony przez {interaction.user.mention} na {time} minut.\nBackup ról: {len(roles_to_remove)} role."
    await send_log_embed(bot, "Mute", desc, discord.Color.white())

    # Automatyczne odciszenie po czasie
    await asyncio.sleep(time * 60)
    # Wczytanie znowu, bo w międzyczasie może się zmienić
    mute_log = load_mute_log()
    if str(user.id) in mute_log:
        try:
            # Usuwamy rolę mute
            await user.remove_roles(mute_role, reason="Automatyczne odciszenie po czasie mute")
            # Przywracamy role z backupu
            roles_ids = mute_log[str(user.id)]["roles"]
            roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
            await user.add_roles(*roles, reason="Przywrócenie ról po odciszeniu")
            # Usuwamy wpis z logu
            del mute_log[str(user.id)]
            save_mute_log(mute_log)
            # Logujemy odciszenie
            desc = f"Automatyczne odciszenie użytkownika {user.mention} po upływie {time} minut."
            await send_log_embed(bot, "Unmute (auto)", desc, discord.Color.green())
        except Exception as e:
            await send_log_embed(bot, "Błąd przy auto unmute", f"Nie udało się odciszyć {user.mention} automatycznie:\n{e}", discord.Color.red())


@bot.tree.command(name="unmute", description="Odcisza użytkownika ręcznie.")
@app_commands.describe(user="Użytkownik do odciszenia")
async def unmute(interaction: discord.Interaction, user: discord.Member):
    # Sprawdzenie uprawnień
    if not has_permission(interaction.user, "unmute"):
        await interaction.response.send_message("Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    mute_role = interaction.guild.get_role(MUTE_ROLE_ID)
    if not mute_role:
        await interaction.response.send_message("Rola wyciszenia (Muted) nie została znaleziona.", ephemeral=True)
        return

    mute_log = load_mute_log()
    if str(user.id) not in mute_log:
        await interaction.response.send_message("Ten użytkownik nie jest wyciszony lub brak danych backupu ról.", ephemeral=True)
        return

    try:
        # Usunięcie roli mute
        await user.remove_roles(mute_role, reason=f"Odciszenie przez {interaction.user}")

        # Przywrócenie ról z backupu
        roles_ids = mute_log[str(user.id)]["roles"]
        roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
        await user.add_roles(*roles, reason=f"Odciszenie przez {interaction.user}")

        # Usunięcie z logu
        del mute_log[str(user.id)]
        save_mute_log(mute_log)

        await interaction.response.send_message(f"{user.mention} został odciszony.", ephemeral=False)

        desc = f"Użytkownik {user.mention} został odciszony przez {interaction.user.mention}."
        await send_log_embed(bot, "Unmute", desc, discord.Color.green())

    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas odciszania: {e}", ephemeral=True)


@bot.tree.command(name="warn", description="Ostrzega użytkownika.")
@app_commands.describe(user="Użytkownik do ostrzeżenia", reason="Powód ostrzeżenia")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    # Sprawdzenie uprawnień
    if not has_permission(interaction.user, "warn"):
        await interaction.response.send_message("Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    desc = (
        f"**Ostrzeżony użytkownik:** {user.mention} (`{user.id}`)\n"
        f"**Ostrzeżenie od:** {interaction.user.mention}\n"
        f"**Powód:** {reason}"
    )
    await send_log_embed(bot, "Ostrzeżenie", desc, discord.Color.gold())
    await interaction.response.send_message(f"{user.mention} został ostrzeżony.", ephemeral=False)


@bot.tree.command(name="ban", description="Banuje użytkownika.")
@app_commands.describe(user="Użytkownik do zbanowania", reason="Powód bana (opcjonalne)")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak podanego powodu"):
    # Sprawdzenie uprawnień
    if not has_permission(interaction.user, "ban"):
        await interaction.response.send_message("Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    try:
        await user.ban(reason=f"Ban przez {interaction.user}: {reason}")
        await interaction.response.send_message(f"{user.mention} został zbanowany.", ephemeral=False)

        desc = (
            f"**Zbanowany użytkownik:** {user.mention} (`{user.id}`)\n"
            f"**Banujący:** {interaction.user.mention}\n"
            f"**Powód:** {reason}"
        )
        await send_log_embed(bot, "Ban", desc, discord.Color.red())

    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas banowania: {e}", ephemeral=True)


# --- START BOTA ---

if __name__ == "__main__":
    keep_alive.keep_alive()  # wywołanie funkcji keep_alive z pliku keep_alive.py
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Brak tokena w zmiennej środowiskowej DISCORD_TOKEN!")
        exit(1)
    bot.run(token)
