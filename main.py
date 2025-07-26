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

# --- Database management ---
class Database:
    def __init__(self, path="roles.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        # Tworzymy tabele jeśli nie istnieją
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS muted_roles (
            user_id INTEGER PRIMARY KEY,
            roles TEXT
        )''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            moderator_id INTEGER,
            reason TEXT,
            timestamp TEXT
        )''')
        self.cursor.execute('''CREATE TABLE IF NOT EXISTS temp_bans (
            user_id INTEGER PRIMARY KEY,
            unban_time TEXT
        )''')
        self.conn.commit()

    def save_roles(self, user_id: int, roles):
        roles_ids = [role.id for role in roles]
        roles_json = json.dumps(roles_ids)
        self.cursor.execute('REPLACE INTO muted_roles (user_id, roles) VALUES (?, ?)', (user_id, roles_json))
        self.conn.commit()

    def load_roles(self, user_id: int):
        self.cursor.execute('SELECT roles FROM muted_roles WHERE user_id = ?', (user_id,))
        result = self.cursor.fetchone()
        if result:
            return json.loads(result[0])
        return []

    def delete_roles(self, user_id: int):
        self.cursor.execute('DELETE FROM muted_roles WHERE user_id = ?', (user_id,))
        self.conn.commit()

    def add_warning(self, user_id: int, moderator_id: int, reason: str):
        timestamp = datetime.utcnow().isoformat()
        self.cursor.execute('INSERT INTO warnings (user_id, moderator_id, reason, timestamp) VALUES (?, ?, ?, ?)',
                            (user_id, moderator_id, reason, timestamp))
        self.conn.commit()

    def count_warnings(self, user_id: int) -> int:
        self.cursor.execute('SELECT COUNT(*) FROM warnings WHERE user_id = ?', (user_id,))
        result = self.cursor.fetchone()
        return result[0] if result else 0

    def clear_warnings(self, user_id: int):
        self.cursor.execute('DELETE FROM warnings WHERE user_id = ?', (user_id,))
        self.conn.commit()

    def add_temp_ban(self, user_id: int, unban_time: datetime):
        self.cursor.execute('REPLACE INTO temp_bans (user_id, unban_time) VALUES (?, ?)', (user_id, unban_time.isoformat()))
        self.conn.commit()

    def remove_temp_ban(self, user_id: int):
        self.cursor.execute('DELETE FROM temp_bans WHERE user_id = ?', (user_id,))
        self.conn.commit()

    def get_all_temp_bans(self):
        self.cursor.execute('SELECT user_id, unban_time FROM temp_bans')
        return self.cursor.fetchall()

db = Database()

# --- Bot setup ---
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Stałe ID
MUTED_ROLE_ID = 1396541521003675718
LOG_CHANNEL_ID = 1396875096882417836

# Uprawnienia - listy ID ról
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

# --- Automatyczne banowanie ---
async def apply_temp_ban(guild: discord.Guild, user: discord.Member, moderator: discord.Member, days: int, reason: str):
    try:
        embed = discord.Embed(title="⛔ Tymczasowy ban", color=discord.Color.dark_red())
        embed.add_field(name="Moderator", value=str(moderator), inline=False)
        embed.add_field(name="Powód", value=reason, inline=False)
        embed.add_field(name="Czas trwania", value=f"{days} dni", inline=False)
        await user.send(embed=embed)
    except discord.Forbidden:
        pass

    await user.ban(reason=reason)
    unban_time = datetime.utcnow() + timedelta(days=days)
    db.add_temp_ban(user.id, unban_time)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"Użytkownik {user} został tymczasowo zbanowany na {days} dni za: {reason}")

async def apply_perm_ban(guild: discord.Guild, user: discord.Member, moderator: discord.Member, reason: str):
    try:
        embed = discord.Embed(title="⛔ Permanentny ban", color=discord.Color.dark_red())
        embed.add_field(name="Moderator", value=str(moderator), inline=False)
        embed.add_field(name="Powód", value=reason, inline=False)
        await user.send(embed=embed)
    except discord.Forbidden:
        pass

    await user.ban(reason=reason)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"Użytkownik {user} został permanentnie zbanowany za: {reason}")

async def check_and_apply_ban(guild, user, warn_count, moderator):
    if warn_count == 5:
        await apply_temp_ban(guild, user, moderator, days=3, reason="Automatyczny ban za 5 ostrzeżeń")
        db.clear_warnings(user.id)
    elif warn_count == 10:
        await apply_temp_ban(guild, user, moderator, days=7, reason="Automatyczny ban za 10 ostrzeżeń")
        db.clear_warnings(user.id)
    elif warn_count >= 20:
        await apply_perm_ban(guild, user, moderator, reason="Automatyczny permanentny ban za 20 lub więcej ostrzeżeń")
        db.clear_warnings(user.id)

# --- Task do odbanowywania ---
@tasks.loop(minutes=1)
async def temp_ban_checker():
    await bot.wait_until_ready()
    now = datetime.utcnow()
    for user_id, unban_time_str in db.get_all_temp_bans():
        unban_time = datetime.fromisoformat(unban_time_str)
        if now >= unban_time:
            for guild in bot.guilds:
                try:
                    # fetch_ban przyjmuje user ID
                    ban_entry = await guild.fetch_ban(discord.Object(id=user_id))
                    if ban_entry:
                        await guild.unban(discord.Object(id=user_id), reason="Koniec tymczasowego bana")
                        db.remove_temp_ban(user_id)
                        log_channel = guild.get_channel(LOG_CHANNEL_ID)
                        if log_channel:
                            await log_channel.send(f"Użytkownik <@{user_id}> został automatycznie odbanowany po wygaśnięciu bana.")
                except discord.NotFound:
                    # Użytkownik nie jest zbanowany w tym guildzie, więc usuwamy wpis
                    db.remove_temp_ban(user_id)
                except Exception as e:
                    print(f"❌ Błąd przy odbanowywaniu użytkownika {user_id}: {e}")

# --- Eventy ---

@bot.event
async def on_ready():
    print(f"✅ Zalogowano jako {bot.user} (ID: {bot.user.id})")
    await bot.tree.sync()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="mlody sigma wbij na dysmuzgi muzgu xd"))
    temp_ban_checker.start()

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title=":wastebasket: Usunięto wiadomość", color=discord.Color.red(), timestamp=datetime.utcnow())
        embed.add_field(name="Autor", value=f"{message.author} ({message.author.id})", inline=False)
        embed.add_field(name="Kanał", value=message.channel.mention, inline=False)
        embed.add_field(name="Treść", value=message.content or "Brak treści", inline=False)
        await channel.send(embed=embed)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot:
        return
    if before.content == after.content:
        return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title=":pencil: Edytowano wiadomość", color=discord.Color.orange(), timestamp=datetime.utcnow())
        embed.add_field(name="Autor", value=f"{before.author} ({before.author.id})", inline=False)
        embed.add_field(name="Kanał", value=before.channel.mention, inline=False)
        embed.add_field(name="Przed", value=before.content or "Brak treści", inline=False)
        embed.add_field(name="Po", value=after.content or "Brak treści", inline=False)
        await channel.send(embed=embed)

# --- Slash commands ---

@bot.tree.command(name="mute", description="Wycisza użytkownika na serwerze")
@app_commands.describe(user="Użytkownik do wyciszenia", czas="Czas wyciszenia w minutach (0 - bez limitu)", reason="Powód wyciszenia")
async def mute(interaction: discord.Interaction, user: discord.Member, czas: int = 0, reason: str = "Brak powodu"):
    if not has_permission(interaction, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnień do wyciszania!", ephemeral=True)
        return
    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if muted_role in user.roles:
        await interaction.response.send_message("❌ Ten użytkownik jest już wyciszony.", ephemeral=True)
        return

    # Zapisz aktualne role (bez @everyone i muted)
    roles_to_save = [r for r in user.roles if r != interaction.guild.default_role and r != muted_role]
    db.save_roles(user.id, roles_to_save)

    # Usuń role i dodaj muted
    try:
        await user.remove_roles(*roles_to_save)
        await user.add_roles(muted_role, reason=f"Muted przez {interaction.user} - {reason}")
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd podczas mutowania: {e}", ephemeral=True)
        return

    # Jeśli czas > 0 to ustaw timer
    if czas > 0:
        unmute_time = datetime.utcnow() + timedelta(minutes=czas)

        async def unmute_task():
            await asyncio.sleep(czas * 60)
            if muted_role in user.roles:
                await user.remove_roles(muted_role, reason="Automatyczne odciszenie")
                roles_ids = db.load_roles(user.id)
                roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
                await user.add_roles(*roles, reason="Przywrócenie ról po odciszeniu")
                db.delete_roles(user.id)
                channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
                if channel:
                    await channel.send(f"🔈 Użytkownik {user} został automatycznie odciszony po {czas} minutach.")
        bot.loop.create_task(unmute_task())

    await interaction.response.send_message(f"🔇 {user.mention} został wyciszony na {czas} minut. Powód: {reason}")

@bot.tree.command(name="unmute", description="Odcisza użytkownika")
@app_commands.describe(user="Użytkownik do odciszenia")
async def unmute(interaction: discord.Interaction, user: discord.Member):
    if not has_permission(interaction, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnień do odciszania!", ephemeral=True)
        return
    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if muted_role not in user.roles:
        await interaction.response.send_message("❌ Ten użytkownik nie jest wyciszony.", ephemeral=True)
        return
    try:
        await user.remove_roles(muted_role, reason=f"Unmute przez {interaction.user}")
        roles_ids = db.load_roles(user.id)
        roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
        await user.add_roles(*roles, reason="Przywrócenie ról po odciszeniu")
        db.delete_roles(user.id)
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd podczas odciszania: {e}", ephemeral=True)
        return
    await interaction.response.send_message(f"🔊 {user.mention} został odciszony.")

@bot.tree.command(name="warn", description="Ostrzega użytkownika")
@app_commands.describe(user="Użytkownik do ostrzeżenia", reason="Powód ostrzeżenia")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu"):
    if not has_permission(interaction, "warn"):
        await interaction.response.send_message("❌ Nie masz uprawnień do warnowania!", ephemeral=True)
        return
    db.add_warning(user.id, interaction.user.id, reason)
    warn_count = db.count_warnings(user.id)
    await interaction.response.send_message(f"⚠️ Ostrzeżono {user.mention}. Aktualna liczba warnów: {warn_count}. Powód: {reason}")

    await check_and_apply_ban(interaction.guild, user, warn_count, interaction.user)

@bot.tree.command(name="kick", description="Wyrzuca użytkownika z serwera")
@app_commands.describe(user="Użytkownik do wyrzucenia", reason="Powód kicka")
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu"):
    if not has_permission(interaction, "kick"):
        await interaction.response.send_message("❌ Nie masz uprawnień do kickowania!", ephemeral=True)
        return
    try:
        await user.kick(reason=f"{reason} - przez {interaction.user}")
        await interaction.response.send_message(f"👢 {user} został wyrzucony. Powód: {reason}")
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd przy kicku: {e}", ephemeral=True)

@bot.tree.command(name="ban", description="Banuje użytkownika z serwera")
@app_commands.describe(user="Użytkownik do zbanowania", reason="Powód bana")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu"):
    if not has_permission(interaction, "ban"):
        await interaction.response.send_message("❌ Nie masz uprawnień do banowania!", ephemeral=True)
        return
    try:
        await user.ban(reason=f"{reason} - przez {interaction.user}")
        await interaction.response.send_message(f"⛔ {user} został zbanowany. Powód: {reason}")
    except Exception as e:
        await interaction.response.send_message(f"❌ Błąd przy banie: {e}", ephemeral=True)

# --- Uruchomienie Flask i Bota ---
if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    TOKEN = os.environ.get("TOKEN")
    if not TOKEN:
        print("❌ Nie znaleziono tokena bota w zmiennej środowiskowej TOKEN.")
        exit(1)
    bot.run(TOKEN)
