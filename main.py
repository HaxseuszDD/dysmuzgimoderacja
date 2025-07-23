import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
from datetime import datetime, timedelta
import os
import sqlite3
import json
import threading
from flask import Flask

# --- Flask app ---
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot działa!"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# --- Discord bot setup ---
def get_token():
    return os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Stałe (ID) ---
MUTED_ROLE_ID = 1396541521003675718
LOG_CHANNEL_ID = 1396875096882417836

PERMISSIONS = {
    "warn": [1393370941811064972, 1393370832071426230, 1393370749661614080,
             1393370358408544328, 1393370252519145493, 1393370458740490351,
             1393370125083607174, 1393369936537194619, 1396460188298641418,
             1393368165567692911],
    "mute": [1393370749661614080, 1393370358408544328, 1393370252519145493,
             1393370458740490351, 1393370125083607174, 1393369936537194619,
             1396460188298641418, 1393368165567692911],
    "kick": [1393370358408544328, 1393370252519145493, 1393370458740490351,
             1393370125083607174, 1393369936537194619, 1396460188298641418,
             1393368165567692911],
    "ban":  [1393370458740490351, 1393370125083607174, 1393369936537194619,
             1396460188298641418, 1393368165567692911]
}

def has_permission(interaction: discord.Interaction, command: str) -> bool:
    allowed_roles = PERMISSIONS.get(command, [])
    user_roles_ids = [role.id for role in interaction.user.roles]
    return any(role_id in user_roles_ids for role_id in allowed_roles)

# --- SQLite connection ---
conn = sqlite3.connect('roles.db', check_same_thread=False)
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

cursor.execute('''
CREATE TABLE IF NOT EXISTS temp_bans (
    user_id INTEGER PRIMARY KEY,
    unban_time TEXT
)
''')

conn.commit()

# --- Database helper functions ---
def save_roles(user_id: int, roles):
    roles_ids = [role.id for role in roles]
    roles_json = json.dumps(roles_ids)
    cursor.execute('REPLACE INTO muted_roles (user_id, roles) VALUES (?, ?)', (user_id, roles_json))
    conn.commit()

def load_roles(user_id: int):
    cursor.execute('SELECT roles FROM muted_roles WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    if result and result[0]:
        return json.loads(result[0])
    return []

def delete_roles(user_id: int):
    cursor.execute('DELETE FROM muted_roles WHERE user_id = ?', (user_id,))
    conn.commit()

def add_warning(user_id: int, moderator_id: int, reason: str):
    timestamp = datetime.utcnow().isoformat()
    cursor.execute(
        'INSERT INTO warnings (user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?)',
        (user_id, moderator_id, reason, timestamp)
    )
    conn.commit()

def count_warnings(user_id: int) -> int:
    cursor.execute('SELECT COUNT(*) FROM warnings WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    return result[0] if result else 0

def clear_warnings(user_id: int):
    cursor.execute('DELETE FROM warnings WHERE user_id = ?', (user_id,))
    conn.commit()

def save_temp_ban(user_id: int, unban_time: datetime):
    cursor.execute('REPLACE INTO temp_bans (user_id, unban_time) VALUES (?, ?)', (user_id, unban_time.isoformat()))
    conn.commit()

def remove_temp_ban(user_id: int):
    cursor.execute('DELETE FROM temp_bans WHERE user_id = ?', (user_id,))
    conn.commit()

def get_all_temp_bans():
    cursor.execute('SELECT user_id, unban_time FROM temp_bans')
    return cursor.fetchall()

# --- Ban unban scheduler ---
async def schedule_unban(user_id: int, guild_id: int, unban_time: datetime):
    now = datetime.utcnow()
    delay = (unban_time - now).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)

    guild = bot.get_guild(guild_id)
    if not guild:
        return
    try:
        user = await bot.fetch_user(user_id)
        await guild.unban(user, reason="Automatyczne odbanowanie po wygaśnięciu bana")
        remove_temp_ban(user_id)
    except Exception as e:
        print(f"❌ Błąd przy automatycznym odbanowaniu użytkownika {user_id}: {e}")

# --- Events ---
@bot.event
async def on_ready():
    print(f"✅ Zalogowano jako {bot.user}")
    await bot.tree.sync()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="mlody sigma wbij na dysmuzgi muzgu xd"))

    # Przywracanie zaplanowanych odbanowań
    for user_id, unban_time_str in get_all_temp_bans():
        unban_time = datetime.fromisoformat(unban_time_str)
        if bot.guilds:
            guild_id = bot.guilds[0].id
            asyncio.create_task(schedule_unban(user_id, guild_id, unban_time))

# --- Commands ---

@bot.tree.command(name="mute", description="Wycisz użytkownika na określony czas (w minutach)")
@app_commands.describe(user="Użytkownik do wyciszenia", reason="Powód wyciszenia", time="Czas trwania w minutach")
async def mute(interaction: discord.Interaction, user: discord.Member, reason: str, time: int):
    if not has_permission(interaction, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnień do tej komendy.", ephemeral=True)
        return

    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("❌ Nie znaleziono roli wyciszenia (muted)!", ephemeral=True)
        return

    # Zapisz aktualne role użytkownika (bez domyślnej i muted)
    previous_roles = [role for role in user.roles if role != interaction.guild.default_role and role != muted_role]
    save_roles(user.id, previous_roles)

    try:
        await user.edit(roles=[muted_role], reason=reason)
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd przy wyciszaniu: {e}", ephemeral=True)
        return

    embed_dm = discord.Embed(title="🔇 Wyciszenie", color=discord.Color.red())
    embed_dm.add_field(name="Moderator", value=str(interaction.user), inline=False)
    embed_dm.add_field(name="Powód", value=reason, inline=False)
    embed_dm.add_field(name="Czas trwania", value=f"{time} minut", inline=False)
    try:
        await user.send(embed=embed_dm)
    except discord.Forbidden:
        pass

    await interaction.response.send_message(f"✅ {user.display_name} został wyciszony na {time} minut.", ephemeral=True)

    # Czekaj określony czas, potem przywróć role
    await asyncio.sleep(time * 60)

    try:
        roles_ids = load_roles(user.id)
        roles_to_restore = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
        if roles_to_restore:
            await user.edit(roles=roles_to_restore, reason="Automatyczne odciszenie")
            delete_roles(user.id)
    except Exception as e:
        print(f"❌ Błąd przy przywracaniu ról po wyciszeniu: {e}")

@bot.tree.command(name="unmute", description="Odcisz użytkownika")
@app_commands.describe(user="Użytkownik do odciszenia", reason="Powód (opcjonalny)")
async def unmute(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    if not has_permission(interaction, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnień do tej komendy.", ephemeral=True)
        return

    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if muted_role:
        await user.remove_roles(muted_role, reason=reason or "Ręczne odciszenie")

    roles_ids = load_roles(user.id)
    roles_to_restore = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
    if roles_to_restore:
        await user.edit(roles=roles_to_restore, reason="Ręczne odciszenie")
        delete_roles(user.id)

    embed_dm = discord.Embed(title="🔊 Odciszenie", color=discord.Color.green())
    embed_dm.add_field(name="Moderator", value=str(interaction.user), inline=False)
    if reason:
        embed_dm.add_field(name="Powód", value=reason, inline=False)
    try:
        await user.send(embed=embed_dm)
    except discord.Forbidden:
        pass

    await interaction.response.send_message(f"✅ {user.display_name} został odciszony.", ephemeral=True)

@bot.tree.command(name="ban", description="Zbanuj użytkownika")
@app_commands.describe(user="Użytkownik do zbanowania", reason="Powód")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu"):
    if not has_permission(interaction, "ban"):
        await interaction.response.send_message("❌ Nie masz uprawnień do tej komendy.", ephemeral=True)
        return

    embed_dm = discord.Embed(title="⛔ Ban", color=discord.Color.dark_red())
    embed_dm.add_field(name="Moderator", value=str(interaction.user), inline=False)
    embed_dm.add_field(name="Powód", value=reason, inline=False)
    try:
        await user.send(embed=embed_dm)
    except discord.Forbidden:
        pass

    try:
        await user.ban(reason=reason)
        await interaction.response.send_message(f"✅ {user.display_name} został zbanowany.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd podczas banowania: {e}", ephemeral=True)

@bot.tree.command(name="kick", description="Wyrzuć użytkownika")
@app_commands.describe(user="Użytkownik do wyrzucenia", reason="Powód")
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu"):
    if not has_permission(interaction, "kick"):
        await interaction.response.send_message("❌ Nie masz uprawnień do tej komendy.", ephemeral=True)
        return

    embed_dm = discord.Embed(title="👢 Wyrzucenie", color=discord.Color.orange())
    embed_dm.add_field(name="Moderator", value=str(interaction.user), inline=False)
    embed_dm.add_field(name="Powód", value=reason, inline=False)
    try:
        await user.send(embed=embed_dm)
    except discord.Forbidden:
        pass

    try:
        await user.kick(reason=reason)
        await interaction.response.send_message(f"✅ {user.display_name} został wyrzucony.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd podczas wyrzucania: {e}", ephemeral=True)

@bot.tree.command(name="warn", description="Ostrzeż użytkownika")
@app_commands.describe(user="Użytkownik do ostrzeżenia", reason="Powód")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not has_permission(interaction, "warn"):
        await interaction.response.send_message("❌ Nie masz uprawnień do ostrzegania.", ephemeral=True)
        return

    add_warning(user.id, interaction.user.id, reason)
    warn_count = count_warnings(user.id)

    embed_warn = discord.Embed(title="⚠️ Ostrzeżenie", color=discord.Color.gold())
    embed_warn.add_field(name="Moderator", value=str(interaction.user), inline=False)
    embed_warn.add_field(name="Powód", value=reason, inline=False)
    embed_warn.add_field(name="Łączna liczba ostrzeżeń", value=str(warn_count), inline=False)

    try:
        await user.send(embed=embed_warn)
    except discord.Forbidden:
        pass

    await interaction.response.send_message(f"✅ {user.display_name} został ostrzeżony. Łączna liczba ostrzeżeń: {warn_count}", ephemeral=True)

    # Automatyczne bany na podstawie warnów
    ban_duration = None  # None = ban na zawsze
    if warn_count == 5:
        ban_duration = timedelta(days=3)
    elif warn_count == 10:
        ban_duration = timedelta(days=7)
    elif warn_count >= 20:
        ban_duration = None  # ban permanentny
    else:
        return  # Nie osiągnięto progu bana

    # Informacja o automatycznym banie
    embed_ban = discord.Embed(title="⛔ Ban automatyczny", color=discord.Color.red())
    embed_ban.add_field(name="Powód", value=f"Osiągnięto {warn_count} ostrzeżeń", inline=False)
    if ban_duration:
        embed_ban.add_field(name="Czas trwania bana", value=str(ban_duration), inline=False)
    else:
        embed_ban.add_field(name="Czas trwania bana", value="Permanentny", inline=False)

    try:
        await user.send(embed=embed_ban)
    except discord.Forbidden:
        pass

    try:
        if ban_duration:
            await interaction.guild.ban(user, reason="Automatyczny ban na podstawie ostrzeżeń", delete_message_days=0)
            unban_time = datetime.utcnow() + ban_duration
            save_temp_ban(user.id, unban_time)
            asyncio.create_task(schedule_unban(user.id, interaction.guild.id, unban_time))
            await interaction.followup.send(f"🔨 {user.display_name} został automatycznie zbanowany na {ban_duration.days} dni.", ephemeral=True)
        else:
            await interaction.guild.ban(user, reason="Automatyczny ban permanentny na podstawie ostrzeżeń", delete_message_days=0)
            remove_temp_ban(user.id)
            await interaction.followup.send(f"🔨 {user.display_name} został automatycznie zbanowany permanentnie.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Błąd podczas automatycznego banowania: {e}", ephemeral=True)

@bot.tree.command(name="warnings", description="Pokaż ostrzeżenia użytkownika")
@app_commands.describe(user="Użytkownik")
async def warnings(interaction: discord.Interaction, user: discord.Member):
    if not has_permission(interaction, "warn"):
        await interaction.response.send_message("❌ Nie masz uprawnień.", ephemeral=True)
        return

    cursor.execute('SELECT id, moderator_id, reason, timestamp FROM warnings WHERE user_id = ?', (user.id,))
    warnings_list = cursor.fetchall()
    if not warnings_list:
        await interaction.response.send_message(f"{user.display_name} nie ma żadnych ostrzeżeń.", ephemeral=True)
        return

    embed = discord.Embed(title=f"Ostrzeżenia {user.display_name}", color=discord.Color.gold())
    for wid, mod_id, reason, timestamp in warnings_list:
        mod = interaction.guild.get_member(mod_id)
        mod_name = mod.display_name if mod else "Nieznany"
        embed.add_field(name=f"Ostrzeżenie #{wid}", value=f"Moderator: {mod_name}\nPowód: {reason}\nData: {timestamp}", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="clearwarnings", description="Wyczyść ostrzeżenia użytkownika")
@app_commands.describe(user="Użytkownik")
async def clearwarnings(interaction: discord.Interaction, user: discord.Member):
    if not has_permission(interaction, "warn"):
        await interaction.response.send_message("❌ Nie masz uprawnień.", ephemeral=True)
        return
    clear_warnings(user.id)
    await interaction.response.send_message(f"✅ Ostrzeżenia {user.display_name} zostały usunięte.", ephemeral=True)

# --- Uruchomienie ---
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    bot.run(get_token())
