import discord
from discord.ext import commands, tasks
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
    return "Bot działa"

def run_flask():
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

# === Discord Bot Setup ===
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# === Stałe (ID roli i kanału logów) ===
MUTED_ROLE_ID = 1396541521003675718
LOG_CHANNEL_ID = 1396875096882417836

# Role uprawnień do komend
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
    "ban": [1393370458740490351, 1393370125083607174, 1393369936537194619,
            1396460188298641418, 1393368165567692911]
}

def has_permission(interaction: discord.Interaction, command: str) -> bool:
    allowed_roles = PERMISSIONS.get(command, [])
    user_roles_ids = [role.id for role in interaction.user.roles]
    return any(role_id in user_roles_ids for role_id in allowed_roles)

# === Baza SQLite ===
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

# === Task do automatycznego odbanowywania ===
@tasks.loop(minutes=1)
async def temp_ban_checker():
    await bot.wait_until_ready()
    for user_id, unban_time_str in db.get_all_temp_bans():
        unban_time = datetime.fromisoformat(unban_time_str)
        if datetime.utcnow() >= unban_time:
            for guild in bot.guilds:
                try:
                    # Używamy fetch_ban, jeśli user jest zbanowany
                    ban_entry = await guild.fetch_ban(discord.Object(id=user_id))
                    if ban_entry:
                        await guild.unban(discord.Object(id=user_id), reason="Koniec tymczasowego bana")
                        db.remove_temp_ban(user_id)
                        log_channel = guild.get_channel(LOG_CHANNEL_ID)
                        if log_channel:
                            await log_channel.send(f"Użytkownik <@{user_id}> został automatycznie odbanowany po wygaśnięciu bana.")
                except discord.NotFound:
                    # Nie znaleziono bana u użytkownika — usuwamy wpis w bazie
                    db.remove_temp_ban(user_id)
                except Exception as e:
                    print(f"❌ Błąd podczas odbanowywania użytkownika {user_id}: {e}")

# === Eventy bota ===
@bot.event
async def on_ready():
    print(f"✅ Zalogowano jako {bot.user}")
    await bot.tree.sync()
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="mlody sigma wbij na dysmuzgi muzgu xd"))
    temp_ban_checker.start()

# === Pomocnicze funkcje banowania ===
async def apply_temp_ban(guild: discord.Guild, user: discord.Member, moderator: discord.Member, days: int, reason: str):
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
        await log_channel.send(f"Użytkownik {user} został tymczasowo zbanowany na {days} dni za: {reason}.")

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
        await log_channel.send(f"Użytkownik {user} został permanentnie zbanowany za: {reason}.")

async def check_and_apply_ban(guild: discord.Guild, user: discord.Member, warn_count: int, moderator: discord.Member):
    try:
        if warn_count == 5:
            await apply_temp_ban(guild, user, moderator, days=3, reason="Automatyczny ban za 5 ostrzeżeń")
            db.clear_warnings(user.id)
        elif warn_count == 10:
            await apply_temp_ban(guild, user, moderator, days=7, reason="Automatyczny ban za 10 ostrzeżeń")
            db.clear_warnings(user.id)
        elif warn_count >= 20:
            await apply_perm_ban(guild, user, moderator, reason="Automatyczny permanentny ban za 20 lub więcej ostrzeżeń")
            db.clear_warnings(user.id)
    except Exception as e:
        print(f"❌ Błąd przy automatycznym banowaniu: {e}")

# === Komendy ===

@bot.tree.command(name="mute", description="Wycisz użytkownika na określony czas (w minutach)")
@app_commands.describe(user="Użytkownik do wyciszenia", reason="Powód", time="Czas trwania w minutach")
async def mute(interaction: discord.Interaction, user: discord.Member, reason: str, time: int):
    if not has_permission(interaction, "mute"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if not muted_role:
        await interaction.response.send_message("❌ Nie znaleziono roli 'Muted' na serwerze.", ephemeral=True)
        return

    # Zapisz obecne role użytkownika (oprócz @everyone i roli Muted)
    previous_roles = [role for role in user.roles if role != muted_role and role != interaction.guild.default_role]

    db.save_roles(user.id, previous_roles)

    try:
        # Usuń wszystkie role i daj tylko Muted
        await user.edit(roles=[muted_role], reason=reason)
    except Exception as e:
        await interaction.response.send_message(f"❌ Nie udało się wyciszyć użytkownika: {e}", ephemeral=True)
        return

    await interaction.response.send_message(f"✅ Użytkownik {user.mention} został wyciszony na {time} minut. Powód: {reason}")

    # Usuwamy mute po czasie
    await asyncio.sleep(time * 60)
    try:
        roles_ids = db.load_roles(user.id)
        roles_to_restore = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid) is not None]
        await user.edit(roles=roles_to_restore, reason="Koniec muta")
        db.delete_roles(user.id)
    except Exception as e:
        print(f"❌ Błąd podczas zdejmowania muta u {user}: {e}")

@bot.tree.command(name="warn", description="Dodaj ostrzeżenie użytkownikowi")
@app_commands.describe(user="Użytkownik do ostrzeżenia", reason="Powód ostrzeżenia")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not has_permission(interaction, "warn"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    db.add_warning(user.id, interaction.user.id, reason)
    warn_count = db.count_warnings(user.id)

    await interaction.response.send_message(f"✅ Ostrzeżenie dodane użytkownikowi {user.mention}. Obecna liczba ostrzeżeń: {warn_count}")

    await check_and_apply_ban(interaction.guild, user, warn_count, interaction.user)

@bot.tree.command(name="kick", description="Wyrzuć użytkownika z serwera")
@app_commands.describe(user="Użytkownik do wyrzucenia", reason="Powód")
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str):
    if not has_permission(interaction, "kick"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    try:
        await user.kick(reason=reason)
        await interaction.response.send_message(f"✅ Użytkownik {user.mention} został wyrzucony z serwera. Powód: {reason}")
    except Exception as e:
        await interaction.response.send_message(f"❌ Nie udało się wyrzucić użytkownika: {e}", ephemeral=True)

@bot.tree.command(name="ban", description="Zbanuj użytkownika z serwera")
@app_commands.describe(user="Użytkownik do zbanowania", reason="Powód", time="Czas trwania bana w dniach (opcjonalne)")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str, time: int = None):
    if not has_permission(interaction, "ban"):
        await interaction.response.send_message("❌ Nie masz uprawnień do użycia tej komendy.", ephemeral=True)
        return

    if time:
        await apply_temp_ban(interaction.guild, user, interaction.user, days=time, reason=reason)
        await interaction.response.send_message(f"✅ Użytkownik {user.mention} został tymczasowo zbanowany na {time} dni. Powód: {reason}")
    else:
        await apply_perm_ban(interaction.guild, user, interaction.user, reason=reason)
        await interaction.response.send_message(f"✅ Użytkownik {user.mention} został permanentnie zbanowany. Powód: {reason}")

# === Uruchamianie Flask + Discord ===
if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("❌ Brak tokena w zmiennych środowiskowych!")
    else:
        # Start Flask w osobnym wątku
        flask_thread = threading.Thread(target=run_flask)
        flask_thread.start()

        # Start bota
        bot.run(token)
