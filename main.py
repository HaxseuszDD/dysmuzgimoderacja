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

# === Flask app ===
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot działa"

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

# Stałe (ID)
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

# Zarządzanie bazą SQLite w formie klasowej (lepsze zarządzanie połączeniem)
class Database:
    def __init__(self, path="roles.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.cursor = self.conn.cursor()
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

# Funkcja do automatycznego banowania na podstawie warnów
async def check_and_apply_ban(guild: discord.Guild, user: discord.Member, warn_count: int, moderator: discord.Member):
    try:
        if warn_count == 5:
            await apply_temp_ban(guild, user, moderator, days=3, reason="Automatyczny ban za 5 ostrzeżeń")
        elif warn_count == 10:
            await apply_temp_ban(guild, user, moderator, days=7, reason="Automatyczny ban za 10 ostrzeżeń")
        elif warn_count >= 20:
            await apply_perm_ban(guild, user, moderator, reason="Automatyczny permanentny ban za 20 lub więcej ostrzeżeń")
    except Exception as e:
        print(f"❌ Błąd przy automatycznym banowaniu: {e}")

async def apply_temp_ban(guild: discord.Guild, user: discord.Member, moderator: discord.Member, days: int, reason: str):
    # Wysyłamy DM
    try:
        await user.send(embed=discord.Embed(title="⛔ Tymczasowy ban", color=discord.Color.dark_red())
                        .add_field(name="Moderator", value=str(moderator), inline=False)
                        .add_field(name="Powód", value=reason, inline=False)
                        .add_field(name="Czas trwania", value=f"{days} dni", inline=False))
    except discord.Forbidden:
        pass

    await user.ban(reason=reason)
    unban_time = datetime.utcnow() + timedelta(days=days)
    db.add_temp_ban(user.id, unban_time)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"Użytkownik {user} został tymczasowo zbanowany na {days} dni za {reason}.")

async def apply_perm_ban(guild: discord.Guild, user: discord.Member, moderator: discord.Member, reason: str):
    try:
        await user.send(embed=discord.Embed(title="⛔ Permanentny ban", color=discord.Color.dark_red())
                        .add_field(name="Moderator", value=str(moderator), inline=False)
                        .add_field(name="Powód", value=reason, inline=False))
    except discord.Forbidden:
        pass

    await user.ban(reason=reason)
    log_channel = guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await log_channel.send(f"Użytkownik {user} został permanentnie zbanowany za {reason}.")

# Task do odbanowywania użytkowników po czasie
@tasks.loop(minutes=1)
async def temp_ban_checker():
    await bot.wait_until_ready()
    for user_id, unban_time_str in db.get_all_temp_bans():
        unban_time = datetime.fromisoformat(unban_time_str)
        if datetime.utcnow() >= unban_time:
            for guild in bot.guilds:
                try:
                    ban_entry = await guild.fetch_ban(discord.Object(id=user_id))
                    if ban_entry:
                        await guild.unban(discord.Object(id=user_id), reason="Koniec tymczasowego bana")
                        db.remove_temp_ban(user_id)
                        log_channel = guild.get_channel(LOG_CHANNEL_ID)
                        if log_channel:
                            await log_channel.send(f"Użytkownik <@{user_id}> został automatycznie odbanowany po wygaśnięciu bana.")
                except discord.NotFound:
                    # Użytkownik nie jest zbanowany w tym guildzie
                    db.remove_temp_ban(user_id)
                except Exception as e:
                    print(f"❌ Błąd podczas automatycznego odbanowywania użytkownika {user_id}: {e}")

@bot.event
async def on_ready():
    print(f"✅ Zalogowano jako {bot.user}")
    await bot.tree.sync()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="mlody sigma wbij na dysmuzgi muzgu xd"))
    temp_ban_checker.start()

# Komendy

@bot.tree.command(name="mute", description="Wycisz użytkownika na określony czas (w minutach)")
@app_commands.describe(user="Użytkownik do wyciszenia", reason="Powód", time="Czas trwania w minutach")
async def mute(interaction: discord.Interaction, user: discord.Member, reason: str, time: int):
    if not has_permission(interaction, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnień.", ephemeral=True)
        return

    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("❌ Nie znaleziono roli wyciszenia!", ephemeral=True)
        return

    previous_roles = [role for role in user.roles if role != interaction.guild.default_role and role != muted_role]
    db.save_roles(user.id, previous_roles)
    try:
        await user.edit(roles=[muted_role], reason=reason)
    except Exception as e:
        print(f"❌ Błąd przy ustawianiu roli muted: {e}")

    dm_embed = discord.Embed(title="🔇 Wyciszenie", color=discord.Color.red())
    dm_embed.add_field(name="Moderator", value=str(interaction.user), inline=False)
    dm_embed.add_field(name="Powód", value=reason, inline=False)
    dm_embed.add_field(name="Czas trwania", value=f"{time} minut", inline=False)
    try:
        await user.send(embed=dm_embed)
    except discord.Forbidden:
        pass

    await interaction.response.send_message(f"{user.name} został wyciszony na {time} minut.", ephemeral=True)
    await asyncio.sleep(time * 60)

    try:
        roles_ids = db.load_roles(user.id)
        roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
        if roles:
            await user.edit(roles=roles, reason="Automatyczne odciszenie")
            db.delete_roles(user.id)
    except Exception as e:
        print(f"❌ Błąd przy przywracaniu ról: {e}")

@bot.tree.command(name="unmute", description="Odcisz użytkownika")
@app_commands.describe(user="Użytkownik do odciszenia", reason="Powód (opcjonalny)")
async def unmute(interaction: discord.Interaction, user: discord.Member, reason: str = None):
    if not has_permission(interaction, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnień.", ephemeral=True)
        return

    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if muted_role:
        await user.remove_roles(muted_role)

    roles_ids = db.load_roles(user.id)
    roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
    if roles:
        await user.edit(roles=roles, reason="Ręczne odciszenie")
    db.delete_roles(user.id)

    dm_embed = discord.Embed(title="🔊 Odciszenie", color=discord.Color.green())
    dm_embed.add_field(name="Moderator", value=str(interaction.user), inline=False)
    if reason:
        dm_embed.add_field(name="Powód", value=reason, inline=False)
    try:
        await user.send(embed=dm_embed)
    except discord.Forbidden:
        pass

    await interaction.response.send_message(f"{user.name} został odciszony.", ephemeral=True)

@bot.tree.command(name="ban", description="Zbanuj użytkownika")
@app_commands.describe(user="Użytkownik do zbanowania", reason="Powód")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu"):
    if not has_permission(interaction, "ban"):
        await interaction.response.send_message("❌ Nie masz uprawnień.", ephemeral=True)
        return

    try:
        await user.send(embed=discord.Embed(title="⛔ Ban", color=discord.Color.dark_red())
                        .add_field(name="Moderator", value=str(interaction.user), inline=False)
                        .add_field(name="Powód", value=reason, inline=False))
    except discord.Forbidden:
        pass

    await user.ban(reason=reason)
    await interaction.response.send_message(f"{user.name} został zbanowany.", ephemeral=True)

@bot.tree.command(name="kick", description="Wyrzuć użytkownika")
@app_commands.describe(user="Użytkownik do wyrzucenia", reason="Powód")
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str = "Brak powodu"):
    if not has_permission(interaction, "kick"):
        await interaction.response.send_message("❌ Nie masz uprawnień.", ephemeral=True)
        return

    try:
        await user.send(embed=discord.Embed(title="👢 Wyrzucenie", color=discord.Color.orange())
                        .add_field(name="Moderator", value=str(interaction.user), inline=False)
                        .add_field(name="Powód", value=reason, inline=False))
    except discord.Forbidden:
        pass

    await user.kick(reason=reason)
    await interaction.response.send_message(f"{user.name} został wyrzucony.", ephemeral=True)

@bot.tree.command(name="warn", description="Ostrzeż użytkownika")
@app_commands.describe(user="Użytkownik do ostrzeżenia", reason="Powód")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not has_permission(interaction, "warn"):
        await interaction.response.send_message("❌ Nie masz uprawnień.", ephemeral=True)
        return

    db.add_warning(user.id, interaction.user.id, reason)
    warn_count = db.count_warnings(user.id)

    try:
        await user.send(embed=discord.Embed(title="⚠️ Ostrzeżenie", color=discord.Color.yellow())
                        .add_field(name="Moderator", value=str(interaction.user), inline=False)
                        .add_field(name="Powód", value=reason, inline=False)
                        .add_field(name="Łączna liczba ostrzeżeń", value=str(warn_count), inline=False))
    except discord.Forbidden:
        pass

    await interaction.response.send_message(f"{user.name} został ostrzeżony. Łączna liczba ostrzeżeń: {warn_count}", ephemeral=True)

    # Sprawdź czy należy automatycznie zbanować
async def apply_temp_ban(guild, user, moderator, days, reason):
    try:
        await user.send(embed=discord.Embed(title="⛔ Tymczasowy ban", color=discord.Color.dark_red())
                        .add_field(name="Moderator", value=str(moderator), inline=False)
                        .add_field(name="Powód", value=reason, inline=False)
                        .add_field(name="Czas trwania", value=f"{days} dni", inline=False))
    except discord.Forbidden:
        pass
    await user.ban(reason=reason)
    await asyncio.sleep(days * 24 * 60 * 60)
    await guild.unban(user, reason="Koniec bana tymczasowego")

async def apply_perm_ban(guild, user, moderator, reason):
    try:
        await user.send(embed=discord.Embed(title="⛔ Permanentny ban", color=discord.Color.dark_red())
                        .add_field(name="Moderator", value=str(moderator), inline=False)
                        .add_field(name="Powód", value=reason, inline=False))
    except discord.Forbidden:
        pass
    await user.ban(reason=reason)

async def check_and_apply_ban(guild, user, warn_count, moderator):
    try:
        if warn_count == 5:
            await apply_temp_ban(guild, user, moderator, days=3, reason="Automatyczny ban za 5 ostrzeżeń")
            cursor.execute("DELETE FROM warnings WHERE user_id = ?", (user.id,))
            conn.commit()
        elif warn_count == 10:
            await apply_temp_ban(guild, user, moderator, days=7, reason="Automatyczny ban za 10 ostrzeżeń")
            cursor.execute("DELETE FROM warnings WHERE user_id = ?", (user.id,))
            conn.commit()
        elif warn_count >= 20:
            await apply_perm_ban(guild, user, moderator, reason="Automatyczny permanentny ban za 20 lub więcej ostrzeżeń")
            cursor.execute("DELETE FROM warnings WHERE user_id = ?", (user.id,))
            conn.commit()
    except Exception as e:
        print(f"❌ Błąd przy automatycznym banowaniu: {e}")

@bot.event
async def on_message_delete(message):
    if message.author.bot:
        return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title=":wastebasket: Usunięto wiadomość", color=discord.Color.red(), timestamp=datetime.utcnow())
        embed.add_field(name="Autor", value=f"{message.author} ({message.author.id})", inline=False)
        embed.add_field(name="Kanał", value=message.channel.mention, inline=False)
        embed.add_field(name="Treść", value=message.content or "*Brak treści (np. obraz)*", inline=False)
        await channel.send(embed=embed)

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or before.content == after.content:
        return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        embed = discord.Embed(title=":pencil: Edytowano wiadomość", color=discord.Color.blue(), timestamp=datetime.utcnow())
        embed.add_field(name="Autor", value=f"{before.author} ({before.author.id})", inline=False)
        embed.add_field(name="Kanał", value=before.channel.mention, inline=False)
        embed.add_field(name="Przed", value=before.content or "*Brak treści*", inline=False)
        embed.add_field(name="Po", value=after.content or "*Brak treści*", inline=False)
        await channel.send(embed=embed)

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    token = get_token()
    if not token:
        print("❌ Brak tokena w zmiennych środowiskowych!")
    else:
        bot.run(token)
