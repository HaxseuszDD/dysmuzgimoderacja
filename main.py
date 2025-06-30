import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
import asyncio
import json
import os

# --------- KONFIGURACJA ---------
GUILD_ID = 1386878418716721202  # ID serwera
MUTE_ROLE_ID = 1389325433161646241  # Rola wyciszonego
LOG_CHANNEL_ID = 1388833060933337129  # Kanał logów

MUTE_LOG_FILE = "mute_logi_role.json"
WARN_LOG_FILE = "warn_logi.json"

PERMISSIONS = {
    "mute": [1388937014379810916, 1388937017185800375, 1388938738574557305, 1388939460372070510, 1386884881363243140, 1386884886341750886],
    "unmute": [1388937014379810916, 1388937017185800375, 1388938738574557305, 1388939460372070510, 1386884881363243140],
    "ban": [1389326265063837706, 1389326194079567912, 1388939460372070510],
    "warn": [1386884859502399520, 1386884865265369119, 1386884871062028480, 1386884876233474089, 1386884881363243140, 1386884886341750886],
}

def get_token():
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Brak zmiennej środowiskowej DISCORD_TOKEN!")
    return token

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

def load_json(file_path):
    if not os.path.isfile(file_path):
        return {}
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def save_json(file_path, data):
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def load_mute_log():
    return load_json(MUTE_LOG_FILE)

def save_mute_log(data):
    save_json(MUTE_LOG_FILE, data)

def load_warn_log():
    return load_json(WARN_LOG_FILE)

def save_warn_log(data):
    save_json(WARN_LOG_FILE, data)

def has_permission(member: discord.Member, command_name: str) -> bool:
    allowed_roles = PERMISSIONS.get(command_name, [])
    return any(role.id in allowed_roles for role in member.roles)

async def send_log_embed(title: str, user: discord.Member, moderator: discord.Member, reason: str, extra: dict = None):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        print("Nie znaleziono kanału logów!")
        return

    description = f"Użytkownik: {user} ({user.id})\nModerator: {moderator.display_name if moderator else 'Bot'} ({moderator.id if moderator else '---'})\nPowód: {reason}"
    if extra:
        for key, value in extra.items():
            description += f"\n{key}: {value}"

    embed = discord.Embed(title=title, description=description, color=discord.Color.orange(), timestamp=datetime.utcnow())
    await channel.send(embed=embed)

# ----------- KOMENDY -------------

@tree.command(name="mute", description="Wycisz użytkownika")
@app_commands.describe(user="Użytkownik do wyciszenia", reason="Powód wyciszenia", czas="Czas wyciszenia w minutach")
@app_commands.guilds(discord.Object(id=GUILD_ID))
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

    # Backup ról oprócz @everyone i mute
    roles_to_remove = [r for r in user.roles if r.id != interaction.guild.id and r.id != MUTE_ROLE_ID]

    mute_log = load_mute_log()
    mute_log[str(user.id)] = [r.id for r in roles_to_remove]
    save_mute_log(mute_log)

    try:
        await user.remove_roles(*roles_to_remove, reason="Backup ról przed mute")
        await user.add_roles(role, reason=reason)
    except Exception as e:
        await interaction.followup.send(f"Błąd przy nadawaniu/odbieraniu roli: {e}", ephemeral=True)
        return

    koniec = datetime.utcnow() + timedelta(minutes=czas)
    koniec_timestamp = int(koniec.timestamp())

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

    async def unmute_task():
        await asyncio.sleep(czas * 60)
        guild = bot.get_guild(GUILD_ID)
        if not guild:
            return
        member = guild.get_member(user.id)
        if not member:
            print(f"[WARN] Użytkownik {user} nie jest już na serwerze.")
            return
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Koniec wyciszenia")

                mute_log = load_mute_log()
                role_ids = mute_log.get(str(member.id), [])
                roles_to_add = [guild.get_role(rid) for rid in role_ids if guild.get_role(rid)]
                if roles_to_add:
                    await member.add_roles(*roles_to_add, reason="Przywrócenie ról po wyciszeniu")

                if str(member.id) in mute_log:
                    mute_log.pop(str(member.id))
                    save_mute_log(mute_log)

                await send_log_embed(
                    title="🔈 Unmute (automatyczny)",
                    user=member,
                    moderator=None,
                    reason="Koniec czasu wyciszenia"
                )
                print(f"[INFO] Automatyczne odciszenie {member} zakończone.")
            except Exception as e:
                print(f"[ERROR] Błąd przy automatycznym unmute: {e}")

    bot.loop.create_task(unmute_task())

@tree.command(name="unmute", description="Odcisz użytkownika")
@app_commands.describe(user="Użytkownik do odciszenia", reason="Powód odciszenia")
@app_commands.guilds(discord.Object(id=GUILD_ID))
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
        title="🔈 Unmute",
        user=user,
        moderator=interaction.user,
        reason=reason
    )
    await interaction.followup.send(f"✅ Użytkownik {user} został odciszony.", ephemeral=True)

@tree.command(name="warn", description="Ostrzeż użytkownika")
@app_commands.describe(user="Użytkownik do ostrzeżenia", reason="Powód ostrzeżenia")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not has_permission(interaction.user, "warn"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    await send_log_embed(
        title="⚠️ Warn",
        user=user,
        moderator=interaction.user,
        reason=reason
    )

    warn_log = load_warn_log()
    uid = str(user.id)
    if uid not in warn_log:
        warn_log[uid] = []

    warn_log[uid].append({
        "reason": reason,
        "moderator": interaction.user.id,
        "timestamp": datetime.utcnow().isoformat()
    })

    save_warn_log(warn_log)

    await interaction.followup.send(f"✅ Użytkownik {user} został ostrzeżony z powodu: {reason}", ephemeral=True)

@tree.command(name="ban", description="Zbanuj użytkownika")
@app_commands.describe(user="Użytkownik do zbanowania", reason="Powód bana")
@app_commands.guilds(discord.Object(id=GUILD_ID))
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
        title="⛔ Ban",
        user=user,
        moderator=interaction.user,
        reason=reason
    )
    await interaction.followup.send(f"✅ Użytkownik {user} został zbanowany.", ephemeral=True)

@tree.command(name="kary", description="Sprawdź ile ostrzeżeń i wyciszeń ma użytkownik")
@app_commands.describe(user="Użytkownik do sprawdzenia")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def kary(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.defer(ephemeral=True)

    mute_log = load_mute_log()
    is_muted = str(user.id) in mute_log

    warn_log = load_warn_log()
    warns = warn_log.get(str(user.id), [])
    warn_count = len(warns)

    embed = discord.Embed(
        title=f"Kary użytkownika {user}",
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )

    embed.add_field(name="🔇 Wyciszenie", value="✅ Aktywne" if is_muted else "Brak", inline=False)
    embed.add_field(name="⚠️ Ostrzeżenia", value=str(warn_count), inline=False)
    if warn_count > 0:
        last_warn = warns[-1]
        embed.add_field(name="Ostatnie ostrzeżenie", value=last_warn.get("reason", "brak powodu"), inline=False)

    await interaction.followup.send(embed=embed, ephemeral=True)

@tree.command(name="liczba", description="Pokaż liczbę użytkowników na serwerze")
@app_commands.guilds(discord.Object(id=GUILD_ID))
async def liczba(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        await interaction.followup.send("Nie znaleziono serwera!", ephemeral=True)
        return

    await guild.chunk()  # Załaduj wszystkich członków

    member_count = len(guild.members)

    await interaction.followup.send(f"Na serwerze jest **{member_count}** członków.", ephemeral=True)

@bot.event
async def on_ready():
    print(f"Zalogowano jako {bot.user} (ID: {bot.user.id})")
    await tree.sync(guild=discord.Object(id=GUILD_ID))
    print(f"Slash commands zsynchronizowane dla guild {GUILD_ID}")

bot.run(get_token())
