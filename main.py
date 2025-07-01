import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timedelta
import os
import sqlite3
import json
from flask import Flask
import threading

# === Flask app ===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# === Discord Bot ===
def get_token():
    return os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True  # Potrzebne do on_member_join
bot = commands.Bot(command_prefix="!", intents=intents)

# Stałe (ID ról i kanałów)
MUTED_ROLE_ID = 1389325433161646241
LOG_CHANNEL_ID = 1388833060933337129
WELCOME_CHANNEL_ID = 1388823708298252328

# Uprawnienia do komend wg ról
PERMISSIONS = {
    "mute": [
        1388937017185800375,
        1388937014379810916,
        1388938738574557305,
        1388939460372070510,
        1389326194079567912,
        1389326265063837706
    ],
    "unmute": [
        1388937017185800375,
        1388937014379810916,
        1388938738574557305,
        1388939460372070510,
        1389326194079567912,
        1389326265063837706
    ],
    "ban": [
        1388939460372070510,
        1389326194079567912,
        1389326265063837706
    ],
    "warn": [
        1388937017185800375,
        1388937014379810916,
        1388938738574557305,
        1388939460372070510,
        1389326194079567912,
        1389326265063837706
    ]
}

def has_permission(interaction: discord.Interaction, command: str) -> bool:
    allowed_roles = PERMISSIONS.get(command, [])
    user_roles_ids = [role.id for role in interaction.user.roles]
    return any(role_id in user_roles_ids for role_id in allowed_roles)

# SQLite setup
conn = sqlite3.connect('roles.db')
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS muted_roles (
    user_id INTEGER PRIMARY KEY,
    roles TEXT
)
''')
conn.commit()

def save_roles(user_id: int, roles):
    roles_ids = [role.id for role in roles]
    roles_json = json.dumps(roles_ids)
    cursor.execute('REPLACE INTO muted_roles (user_id, roles) VALUES (?, ?)', (user_id, roles_json))
    conn.commit()

def load_roles(user_id: int):
    cursor.execute('SELECT roles FROM muted_roles WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    if result:
        return json.loads(result[0])
    return []

def delete_roles(user_id: int):
    cursor.execute('DELETE FROM muted_roles WHERE user_id = ?', (user_id,))
    conn.commit()

@bot.event
async def on_ready():
    print(f"✅ Zalogowano jako {bot.user}")
    await bot.tree.sync()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="discord.gg/goatyrblx"))

@bot.event
async def on_member_join(member: discord.Member):
    guild = member.guild
    welcome_channel = guild.get_channel(WELCOME_CHANNEL_ID)
    if not welcome_channel:
        print(f"❌ Nie znaleziono kanału powitalnego o ID {WELCOME_CHANNEL_ID}")
        return

    member_count = guild.member_count
    embed = discord.Embed(
        title="`🐻‍❄️` Nowy Członek",
        description=(
            f"👋🏻 Witamy na **🐐GOATY🐐**\n"
            f"👤 Nazwa Użytkownika: **{member}**\n"
            f"📅 Konto założone: <t:{int(member.created_at.timestamp())}:F>\n"
            f"⏰ Dołączył/a: <t:{int(member.joined_at.timestamp())}:R>\n"
            f"👥 Aktualnie jest nas: **{member_count}**"
        ),
        color=discord.Color.from_rgb(255, 255, 255)
    )
    await welcome_channel.send(embed=embed)

# --- Komendy slash ---

@bot.tree.command(name="mute", description="Wycisza użytkownika na czas (w minutach)")
@app_commands.describe(user="Kogo wyciszyć", reason="Powód", time="Czas wyciszenia (minuty)")
async def mute(interaction: discord.Interaction, user: discord.Member, reason: str, time: int):
    if not has_permission(interaction, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("❌ Nie znaleziono roli Muted!", ephemeral=True)
        return

    # Zapisz poprzednie role (bez @everyone)
    previous_roles = [role for role in user.roles if role != interaction.guild.default_role]
    save_roles(user.id, previous_roles)

    # Przypisz tylko muted_role
    await user.edit(roles=[muted_role], reason=reason)

    end_time = datetime.utcnow() + timedelta(minutes=time)

    embed = discord.Embed(title="`🔇` Mute", color=discord.Color.red())
    embed.description = (
        f"**Użytkownik:** {user}\n"
        f"**Moderator:** {interaction.user}\n"
        f"**Powód:** {reason}\n"
        f"**Czas:** {time} minut\n"
        f"**Koniec wyciszenia:** <t:{int(end_time.timestamp())}:F>"
    )

    await interaction.response.send_message(f"{user.name} został zmutowany.", ephemeral=True)
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(embed=embed)

    await asyncio.sleep(time * 60)

    # Auto unmute po czasie
    try:
        roles_ids = load_roles(user.id)
        roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
        await user.edit(roles=roles, reason="Auto unmute")
        delete_roles(user.id)

        unmute_embed = discord.Embed(title="`🔊` Unmute (automatyczny)", color=discord.Color.green())
        unmute_embed.description = (
            f"**Użytkownik:** {user}\n"
            f"**Moderator:** System\n"
            f"**Powód:** Kara minęła"
        )
        if log_channel:
            await log_channel.send(embed=unmute_embed)

    except Exception as e:
        print(f"❌ Błąd przy automatycznym unmute: {e}")

@bot.tree.command(name="unmute", description="Usuwa wyciszenie użytkownika")
@app_commands.describe(user="Kogo odciszyć", reason="Powód odciszenia (opcjonalny)")
async def unmute(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    if not has_permission(interaction, "unmute"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("❌ Rola Muted nie istnieje!", ephemeral=True)
        return

    await user.remove_roles(muted_role)
    roles_ids = load_roles(user.id)
    roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
    if roles:
        await user.edit(roles=roles, reason="Ręczny unmute")
    delete_roles(user.id)

    embed = discord.Embed(title="`🔊` Unmute", color=discord.Color.green())
    embed.description = (
        f"**Użytkownik:** {user}\n"
        f"**Moderator:** {interaction.user}"
    )
    if reason:
        embed.description += f"\n**Powód:** {reason}"

    await interaction.response.send_message(f"{user.name} został odciszony.", ephemeral=True)
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(embed=embed)

@bot.tree.command(name="ban", description="Banuje użytkownika")
@app_commands.describe(user="Kogo zbanować", reason="Powód bana")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu"):
    if not has_permission(interaction, "ban"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    await user.ban(reason=reason)
    embed = discord.Embed(title="`⛔` Ban", color=discord.Color.dark_red())
    embed.description = (
        f"**Użytkownik:** {user}\n"
        f"**Moderator:** {interaction.user}\n"
        f"**Powód:** {reason}"
    )

    await interaction.response.send_message(f"{user.name} został zbanowany.", ephemeral=True)
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(embed=embed)

@bot.tree.command(name="unban", description="Odbanowuje użytkownika po ID")
@app_commands.describe(user_id="ID użytkownika do odbanowania")
async def unban(interaction: discord.Interaction, user_id: str):
    if not has_permission(interaction, "ban"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    try:
        user = await bot.fetch_user(int(user_id))
        await interaction.guild.unban(user)
        embed = discord.Embed(title="`✅` Unban", color=discord.Color.green())
        embed.description = (
            f"**Użytkownik:** {user}\n"
            f"**Moderator:** {interaction.user}"
        )

        await interaction.response.send_message(f"{user.name} został odbanowany.", ephemeral=True)
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd: {e}", ephemeral=True)

# === Start Flask i bota ===
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run(get_token())
