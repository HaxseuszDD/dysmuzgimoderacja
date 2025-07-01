import discord
from discord.ext import commands
from discord import app_commands
import asyncio
from datetime import datetime, timedelta
import os
import sqlite3
import json

def get_token():
    return os.getenv("DISCORD_TOKEN")  # Token pobierany z zmiennej środowiskowej

intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# === Ustawienia ===
MUTED_ROLE_ID = 1389325433161646241  # <--- ID roli Muted
LOG_CHANNEL_ID = 1388833060933337129  # <--- ID kanału logów

# ===== ROLLE PERMISJI DLA KOMEND =====
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

# --- SQLite setup ---
conn = sqlite3.connect('roles.db')
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS muted_roles (
    user_id INTEGER PRIMARY KEY,
    roles TEXT
)
''')
conn.commit()

def save_roles(user_id, roles):
    roles_ids = [role.id for role in roles]
    roles_json = json.dumps(roles_ids)
    cursor.execute('REPLACE INTO muted_roles (user_id, roles) VALUES (?, ?)', (user_id, roles_json))
    conn.commit()

def load_roles(user_id):
    cursor.execute('SELECT roles FROM muted_roles WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    if result:
        roles_ids = json.loads(result[0])
        return roles_ids
    return []

def delete_roles(user_id):
    cursor.execute('DELETE FROM muted_roles WHERE user_id = ?', (user_id,))
    conn.commit()

@bot.event
async def on_ready():
    print(f"✅ Zalogowano jako {bot.user}")
    await bot.tree.sync()

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

    previous_roles = [role for role in user.roles if role != interaction.guild.default_role]
    save_roles(user.id, previous_roles)
    await user.edit(roles=[muted_role], reason=reason)
    end_time = datetime.utcnow() + timedelta(minutes=time)

    embed = discord.Embed(title="🔇 Mute", color=discord.Color.red())
    embed.add_field(name="Użytkownik", value=f"{user.mention}", inline=False)
    embed.add_field(name="Moderator", value=f"{interaction.user.mention}", inline=False)
    embed.add_field(name="Powód", value=reason, inline=False)
    embed.add_field(name="Czas", value=f"{time} minut", inline=True)
    embed.add_field(name="Koniec wyciszenia", value=f"<t:{int(end_time.timestamp())}:F>", inline=True)

    await interaction.response.send_message(f"{user.mention} został zmutowany.", ephemeral=True)
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(embed=embed)

    await asyncio.sleep(time * 60)

    # Automatyczny unmute po czasie
    roles_ids = load_roles(user.id)
    roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
    try:
        await user.edit(roles=roles, reason="Auto unmute")
        delete_roles(user.id)

        unmute_embed = discord.Embed(title="🔊 Unmute (automatyczny)", color=discord.Color.green())
        unmute_embed.add_field(name="Użytkownik", value=f"{user.mention}", inline=False)
        unmute_embed.add_field(name="Moderator", value="System", inline=False)
        unmute_embed.add_field(name="Czas", value="Mute zakończony", inline=False)
        if log_channel:
            await log_channel.send(embed=unmute_embed)

    except Exception as e:
        print(f"❌ Błąd przy automatycznym unmute: {e}")

@bot.tree.command(name="unmute", description="Usuwa wyciszenie użytkownika")
@app_commands.describe(user="Kogo odciszyć")
async def unmute(interaction: discord.Interaction, user: discord.Member):
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

    embed = discord.Embed(title="🔊 Unmute", color=discord.Color.green())
    embed.add_field(name="Użytkownik", value=f"{user.mention}", inline=False)
    embed.add_field(name="Moderator", value=f"{interaction.user.mention}", inline=False)

    await interaction.response.send_message(f"{user.mention} został odciszony.", ephemeral=True)
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
    embed = discord.Embed(title="⛔ Ban", color=discord.Color.dark_red())
    embed.add_field(name="Użytkownik", value=f"{user.mention}", inline=False)
    embed.add_field(name="Moderator", value=f"{interaction.user.mention}", inline=False)
    embed.add_field(name="Powód", value=reason, inline=False)

    await interaction.response.send_message(f"{user.mention} został zbanowany.", ephemeral=True)
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
        embed = discord.Embed(title="✅ Unban", color=discord.Color.green())
        embed.add_field(name="Użytkownik", value=f"{user.mention}", inline=False)
        embed.add_field(name="Moderator", value=f"{interaction.user.mention}", inline=False)

        await interaction.response.send_message(f"{user.name} został odbanowany.", ephemeral=True)
        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd: {e}", ephemeral=True)

bot.run(get_token())
