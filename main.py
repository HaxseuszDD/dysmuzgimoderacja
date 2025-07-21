import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime
import os
import sqlite3
import json
import threading
from flask import Flask

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
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Stale (ID rol i kanalow)
MUTED_ROLE_ID = 1396541521003675718
LOG_CHANNEL_ID = 1396875096882417836

PERMISSIONS = {
    "warn": [
        1393370941811064972,
        1393370832071426230,
        1393370749661614080,
        1393370358408544328,
        1393370252519145493,
        1393370458740490351,
        1393370125083607174,
        1393369936537194619,
        1396460188298641418,
        1393368165567692911
    ],
    "mute": [
        1393370749661614080,
        1393370358408544328,
        1393370252519145493,
        1393370458740490351,
        1393370125083607174,
        1393369936537194619,
        1396460188298641418,
        1393368165567692911
    ],
    "kick": [
        1393370358408544328,
        1393370252519145493,
        1393370458740490351,
        1393370125083607174,
        1393369936537194619,
        1396460188298641418,
        1393368165567692911
    ],
    "ban": [
        1393370458740490351,
        1393370125083607174,
        1393369936537194619,
        1396460188298641418,
        1393368165567692911
    ]
}

def has_permission(interaction: discord.Interaction, command: str) -> bool:
    allowed_roles = PERMISSIONS.get(command, [])
    user_roles_ids = [role.id for role in interaction.user.roles]
    return any(role_id in user_roles_ids for role_id in allowed_roles)

conn = sqlite3.connect('roles.db')
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS muted_roles (
    user_id INTEGER PRIMARY KEY,
    roles TEXT
)
''')
cursor.execute('''
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    moderator_id INTEGER,
    reason TEXT,
    timestamp TEXT
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

def add_warning(user_id: int, moderator_id: int, reason: str):
    timestamp = datetime.utcnow().isoformat()
    cursor.execute('INSERT INTO warnings (user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?)',
                   (user_id, moderator_id, reason, timestamp))
    conn.commit()

def count_warnings(user_id: int) -> int:
    cursor.execute('SELECT COUNT(*) FROM warnings WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    return result[0] if result else 0

@bot.event
async def on_ready():
    print(f"✅ Zalogowano jako {bot.user}")
    await bot.tree.sync()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="mlody sigma wbij na dysmuzgi muzgu xd"))

@bot.tree.command(name="mute", description="Wycisza uzytkownika na czas (w minutach)")
@app_commands.describe(user="Kogo wyciszyc", reason="Powod", time="Czas wyciszenia (minuty)")
async def mute(interaction: discord.Interaction, user: discord.Member, reason: str, time: int):
    if not has_permission(interaction, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnien do uzycia tej komendy.", ephemeral=True)
        return

    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("❌ Nie znaleziono roli Muted!", ephemeral=True)
        return

    previous_roles = [role for role in user.roles if role != interaction.guild.default_role]
    save_roles(user.id, previous_roles)
    await user.edit(roles=[muted_role], reason=reason)

    await interaction.response.send_message(f"{user.name} zostal zmutowany na {time} minut.", ephemeral=True)

    await asyncio.sleep(time * 60)

    try:
        roles_ids = load_roles(user.id)
        roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
        await user.edit(roles=roles, reason="Auto unmute")
        delete_roles(user.id)
    except Exception as e:
        print(f"Blad przy automatycznym unmute: {e}")

@bot.tree.command(name="unmute", description="Usuwa wyciszenie uzytkownika")
@app_commands.describe(user="Kogo odciszyc", reason="Powod odciszenia (opcjonalny)")
async def unmute(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    if not has_permission(interaction, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnien do uzycia tej komendy.", ephemeral=True)
        return

    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("❌ Rola Muted nie istnieje!", ephemeral=True)
        return

    await user.remove_roles(muted_role)
    roles_ids = load_roles(user.id)
    roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
    if roles:
        await user.edit(roles=roles, reason="Reczny unmute")
    delete_roles(user.id)

    await interaction.response.send_message(f"{user.name} zostal odciszony.", ephemeral=True)

@bot.tree.command(name="ban", description="Banuje uzytkownika")
@app_commands.describe(user="Kogo zbanowac", reason="Powod bana")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu"):
    if not has_permission(interaction, "ban"):
        await interaction.response.send_message("❌ Nie masz uprawnien do uzycia tej komendy.", ephemeral=True)
        return

    await user.ban(reason=reason)
    await interaction.response.send_message(f"{user.name} zostal zbanowany.", ephemeral=True)

@bot.tree.command(name="kick", description="Wyrzuca uzytkownika")
@app_commands.describe(user="Kogo wyrzucic", reason="Powod wyrzucenia")
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu"):
    if not has_permission(interaction, "kick"):
        await interaction.response.send_message("❌ Nie masz uprawnien do uzycia tej komendy.", ephemeral=True)
        return

    await user.kick(reason=reason)
    await interaction.response.send_message(f"{user.name} zostal wyrzucony.", ephemeral=True)

@bot.tree.command(name="warn", description="Ostrzega uzytkownika")
@app_commands.describe(user="Kogo ostrzec", reason="Powod ostrzezenia")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not has_permission(interaction, "warn"):
        await interaction.response.send_message("❌ Nie masz uprawnien do uzycia tej komendy.", ephemeral=True)
        return

    add_warning(user.id, interaction.user.id, reason)
    warn_count = count_warnings(user.id)
    await interaction.response.send_message(f"{user.name} zostal ostrzezony. Liczba ostrzezen: {warn_count}", ephemeral=True)

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        return
    embed = discord.Embed(title=":wastebasket: Usunieto wiadomosc", color=discord.Color.red(), timestamp=datetime.utcnow())
    embed.add_field(name="Autor", value=f"{message.author} ({message.author.id})", inline=False)
    embed.add_field(name="Kanal", value=message.channel.mention, inline=False)
    embed.add_field(name="Tresc", value=message.content or "*Brak tresci (np. obrazek)*", inline=False)
    await channel.send(embed=embed)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot:
        return
    if before.content == after.content:
        return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        return
    embed = discord.Embed(title=":pencil: Edytowano wiadomosc", color=discord.Color.blue(), timestamp=datetime.utcnow())
    embed.add_field(name="Autor", value=f"{before.author} ({before.author.id})", inline=False)
    embed.add_field(name="Kanal", value=before.channel.mention, inline=False)
    embed.add_field(name="Przed", value=before.content or "*Brak tresci*", inline=False)
    embed.add_field(name="Po", value=after.content or "*Brak tresci*", inline=False)
    await channel.send(embed=embed)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run(get_token())
