import discord
from discord.ext import commands, tasks
from discord import app_commands
from datetime import datetime, timedelta
import asyncio
import json
import os

# --- KONFIGURACJA ---

# ID roli mute (wyciszenia)
MUTE_ROLE_ID = 1389325433161646241  

# ID kanału logów
LOG_CHANNEL_ID = 1388833060933337129  

# Pliki JSON do backupu i logów
MUTE_LOG_FILE = "mute_log.json"            # Backup ról wyciszonego
MUTE_TIME_LOG_FILE = "mute_time_log.json"  # Backup czasu muta
PUNISHMENT_LOG_FILE = "punishment_log.json"  # Liczniki mute/warn/ban

# Role z dostępem do poszczególnych komend
PERMISSIONS = {
    "mute": [1388937014379810916, 1388937017185800375, 1388938738574557305, 1388939460372070510, 1386884881363243140, 1386884886341750886],
    "unmute": [1388937014379810916, 1388937017185800375, 1388938738574557305, 1388939460372070510, 1386884881363243140],
    "ban": [1389326265063837706, 1389326194079567912, 1388939460372070510],
    "warn": [1386884859502399520, 1386884865265369119, 1386884871062028480, 1386884876233474089, 1386884881363243140, 1386884886341750886],
}

# --- POBRANIE TOKENA ---
def get_token():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Brak zmiennej środowiskowej DISCORD_TOKEN!")
    return token

# --- INTENTY I INICJALIZACJA BOTA ---
intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# --- FUNKCJE POMOCNICZE ---

def load_json(filename):
    """Ładuje dane z pliku JSON lub zwraca pusty dict, jeśli plik nie istnieje."""
    if not os.path.isfile(filename):
        return {}
    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_json(filename, data):
    """Zapisuje dane do pliku JSON z ładnym formatowaniem."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def has_permission(member: discord.Member, command_name: str) -> bool:
    """Sprawdza, czy członek ma odpowiednią rolę do wykonania komendy."""
    allowed_roles = PERMISSIONS.get(command_name, [])
    if not allowed_roles:
        return False
    return any(role.id in allowed_roles for role in member.roles)

async def send_log_embed(title: str, user: discord.Member, moderator: discord.Member, reason: str, extra: dict = None):
    """Wysyła embed z logiem do kanału logów."""
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel:
        print("Kanał logów nie znaleziony!")
        return

    description = (
        f"**Użytkownik:** {user} (`{user.id}`)\n"
        f"**Moderator:** {moderator} (`{moderator.id}`)\n"
        f"**Powód:** {reason}"
    )

    if extra:
        for k, v in extra.items():
            description += f"\n**{k}:** {v}"

    embed = discord.Embed(
        title=title,
        description=description,
        color=discord.Color.white(),
        timestamp=datetime.utcnow()
    )
    await channel.send(embed=embed)

# --- EVENTY BOTA ---

@bot.event
async def on_ready():
    print(f"Zalogowano jako {bot.user} ({bot.user.id})")
    update_presence.start()
    await restore_mutes()

@tasks.loop(minutes=5)
async def update_presence():
    total_members = sum(guild.member_count for guild in bot.guilds)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name=f"{total_members} użytkowników"))

async def restore_mutes():
    """Po restarcie bota odczytuje mute_time_log i przywraca muty oraz restartuje zadania unmute."""
    mute_time_log = load_json(MUTE_TIME_LOG_FILE)

    for user_id_str, end_timestamp in mute_time_log.items():
        user_id = int(user_id_str)
        member = None
        guild = None
        # Znajdź użytkownika i jego serwer
        for g in bot.guilds:
            m = g.get_member(user_id)
            if m:
                member = m
                guild = g
                break
        if not member or not guild:
            continue

        role = guild.get_role(MUTE_ROLE_ID)
        if not role:
            continue

        now_ts = int(datetime.utcnow().timestamp())
        seconds_left = end_timestamp - now_ts
        if seconds_left > 0:
            # Dodaj mute, jeśli nie ma
            if role not in member.roles:
                await member.add_roles(role, reason="Przywrócenie muta po restarcie bota")

            # Uruchom zadanie odliczania unmute
            bot.loop.create_task(unmute_after(member, seconds_left))

async def unmute_after(member: discord.Member, seconds: int):
    """Funkcja do automatycznego zdejmowania muta po czasie."""
    await asyncio.sleep(seconds)

    mute_time_log = load_json(MUTE_TIME_LOG_FILE)
    mute_log = load_json(MUTE_LOG_FILE)

    guild = member.guild
    role = guild.get_role(MUTE_ROLE_ID)

    if role not in member.roles:
        # Jeśli mute już nie ma, usuń z logów
        if str(member.id) in mute_time_log:
            mute_time_log.pop(str(member.id))
            save_json(MUTE_TIME_LOG_FILE, mute_time_log)
        return

    now_ts = int(datetime.utcnow().timestamp())
    if mute_time_log.get(str(member.id), 0) > now_ts:
        # Mute nadal aktywne, nic nie rób
        return

    try:
        await member.remove_roles(role, reason="Koniec czasu wyciszenia")

        # Przywróć poprzednie role
        role_ids = mute_log.get(str(member.id), [])
        roles_to_add = [guild.get_role(rid) for rid in role_ids if guild.get_role(rid)]
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Przywrócenie ról po wyciszeniu")

        # Usuń z logów
        mute_log.pop(str(member.id), None)
        mute_time_log.pop(str(member.id), None)
        save_json(MUTE_LOG_FILE, mute_log)
        save_json(MUTE_TIME_LOG_FILE, mute_time_log)

        # Wyślij log do kanału
        await send_log_embed("🔈 Unmute (automatyczny)", member, bot.user, "Koniec czasu wyciszenia")

    except Exception as e:
        print(f"Błąd przy zdejmowaniu muta: {e}")

# --- KOMENDY SLASH ---

@tree.command(name="mute", description="Wycisz użytkownika na określony czas (minuty)")
@app_commands.describe(user="Użytkownik do wyciszenia", reason="Powód wyciszenia", czas="Czas wyciszenia w minutach")
async def mute(interaction: discord.Interaction, user: discord.Member, reason: str, czas: int):
    if not has_permission(interaction.user, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    mute_role = guild.get_role(MUTE_ROLE_ID)
    if not mute_role:
        await interaction.followup.send("Nie znaleziono roli wyciszonego.", ephemeral=True)
        return

    if mute_role in user.roles:
        await interaction.followup.send("Ten użytkownik jest już wyciszony.", ephemeral=True)
        return

    # Backup obecnych ról (oprócz @everyone i mute_role)
    roles_to_remove = [r for r in user.roles if r.id != guild.id and r.id != MUTE_ROLE_ID]

    mute_log = load_json(MUTE_LOG_FILE)
    mute_log[str(user.id)] = [r.id for r in roles_to_remove]
    save_json(MUTE_LOG_FILE, mute_log)

    try:
        # Usuwamy stare role
        await user.remove_roles(*roles_to_remove, reason="Backup i usunięcie przed mute")
        # Dodajemy mute
        await user.add_roles(mute_role, reason=reason)
    except Exception as e:
        await interaction.followup.send(f"Błąd przy nadawaniu/odbieraniu ról: {e}", ephemeral=True)
        return

    koniec = datetime.utcnow() + timedelta(minutes=czas)
    koniec_timestamp = int(koniec.timestamp())

    # Zapisz czas wyciszenia
    mute_time_log = load_json(MUTE_TIME_LOG_FILE)
    mute_time_log[str(user.id)] = koniec_timestamp
    save_json(MUTE_TIME_LOG_FILE, mute_time_log)

    # Aktualizuj licznik mute
    punishment_log = load_json(PUNISHMENT_LOG_FILE)
    user_id_str = str(user.id)
    if user_id_str not in punishment_log:
        punishment_log[user_id_str] = {"mutes": 0, "warns": 0, "bans": 0}
    punishment_log[user_id_str]["mutes"] += 1
    save_json(PUNISHMENT_LOG_FILE, punishment_log)

    # Log w kanale
    await send_log_embed(
        title="🔇 Mute",
        user=user,
        moderator=interaction.user,
        reason=reason,
        extra={
            "Czas": f"{czas} minut",
            "Koniec wyciszenia": f"<t:{koniec_timestamp}:F>"
        }
    )
    await interaction.followup.send(f"✅ Użytkownik {user} został wyciszony na {czas} minut z powodu: {reason}", ephemeral=True)

    # Uruchom zadanie unmute po czasie
    bot.loop.create_task(unmute_after(user, czas * 60))

@tree.command(name="unmute", description="Odcisz użytkownika")
@app_commands.describe(user="Użytkownik do odciszenia", reason="Powód odciszenia")
async def unmute(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not has_permission(interaction.user, "unmute"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    mute_role = guild.get_role(MUTE_ROLE_ID)
    if not mute_role:
        await interaction.followup.send("Nie znaleziono roli wyciszonego.", ephemeral=True)
        return

    if mute_role not in user.roles:
        await interaction.followup.send("Ten użytkownik nie jest wyciszony.", ephemeral=True)
        return

    try:
        await user.remove_roles(mute_role, reason=reason)

        mute_log = load_json(MUTE_LOG_FILE)
        role_ids = mute_log.get(str(user.id), [])
        roles_to_add = [guild.get_role(rid) for rid in role_ids if guild.get_role(rid)]
        if roles_to_add:
            await user.add_roles(*roles_to_add, reason="Przywrócenie ról po unmute")

        mute_time_log = load_json(MUTE_TIME_LOG_FILE)
        if str(user.id) in mute_time_log:
            mute_time_log.pop(str(user.id))
            save_json(MUTE_TIME_LOG_FILE, mute_time_log)

        if str(user.id) in mute_log:
            mute_log.pop(str(user.id))
            save_json(MUTE_LOG_FILE, mute_log)

        await send_log_embed("🔈 Unmute (manualny)", user, interaction.user, reason)

        await interaction.followup.send(f"✅ Użytkownik {user} został odciszony.", ephemeral=True)

    except Exception as e:
        await interaction.followup.send(f"Błąd przy zdejmowaniu roli muta: {e}", ephemeral=True)

@tree.command(name="sprawdz", description="Sprawdź ilość mute/warn/ban użytkownika")
@app_commands.describe(user="Użytkownik do sprawdzenia")
async def checkpunishments(interaction: discord.Interaction, user: discord.Member):
    punishment_log = load_json(PUNISHMENT_LOG_FILE)
    user_data = punishment_log.get(str(user.id), {"mutes": 0, "warns": 0, "bans": 0})

    embed = discord.Embed(title=f"Statystyki kar użytkownika {user}", color=discord.Color.blue())
    embed.add_field(name="Mute'y", value=str(user_data.get("mutes", 0)), inline=True)
    embed.add_field(name="Warny", value=str(user_data.get("warns", 0)), inline=True)
    embed.add_field(name="Bany", value=str(user_data.get("bans", 0)), inline=True)
    embed.set_footer(text=f"ID użytkownika: {user.id}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- URUCHOMIENIE BOTA ---
bot.run(get_token())
