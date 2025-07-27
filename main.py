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
    "warn": {1393370941811064972, 1393370832071426230, 1393370749661614080,
             1393370358408544328, 1393370252519145493, 1393370458740490351,
             1393370125083607174, 1393369936537194619, 1396460188298641418,
             1393368165567692911},
    "mute": {1393370749661614080, 1393370358408544328, 1393370252519145493,
             1393370458740490351, 1393370125083607174, 1393369936537194619,
             1396460188298641418, 1393368165567692911},
    "kick": {1393370358408544328, 1393370252519145493, 1393370458740490351,
             1393370125083607174, 1393369936537194619, 1396460188298641418,
             1393368165567692911},
    "ban": {1393370458740490351, 1393370125083607174, 1393369936537194619,
            1396460188298641418, 1393368165567692911}
}

def has_permission(interaction: discord.Interaction, command: str) -> bool:
    allowed_roles = PERMISSIONS.get(command, set())
    user_roles = {role.id for role in interaction.user.roles}
    return not allowed_roles.isdisjoint(user_roles)

# === Baza SQLite ===
class Database:
    def __init__(self, path="roles.db"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._setup_tables()

    def _setup_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS muted_roles (
                user_id INTEGER PRIMARY KEY,
                roles TEXT,
                unmute_time TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                moderator_id INTEGER,
                reason TEXT,
                timestamp TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS temp_bans (
                user_id INTEGER PRIMARY KEY,
                unban_time TEXT
            )
        ''')
        self.conn.commit()

    def save_roles(self, user_id: int, roles, unmute_time: datetime):
        roles_ids = [role.id for role in roles]
        roles_json = json.dumps(roles_ids)
        self.cursor.execute('REPLACE INTO muted_roles (user_id, roles, unmute_time) VALUES (?, ?, ?)',
                            (user_id, roles_json, unmute_time.isoformat()))
        self.conn.commit()

    def load_roles(self, user_id: int):
        self.cursor.execute('SELECT roles, unmute_time FROM muted_roles WHERE user_id = ?', (user_id,))
        row = self.cursor.fetchone()
        if row:
            roles = json.loads(row[0])
            unmute_time = datetime.fromisoformat(row[1])
            return roles, unmute_time
        return [], None

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
        return self.cursor.fetchone()[0] or 0

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

    def get_all_mutes(self):
        self.cursor.execute('SELECT user_id, roles, unmute_time FROM muted_roles')
        return self.cursor.fetchall()

db = Database()

# === Rate limit helper do logów i DM ===
_last_message_times = {}

async def safe_send(destination, content=None, embed=None, delay=1):
    now = asyncio.get_event_loop().time()
    last = _last_message_times.get(destination.id, 0)
    if now - last < delay:
        await asyncio.sleep(delay - (now - last))
    try:
        if content and embed:
            await destination.send(content=content, embed=embed)
        elif embed:
            await destination.send(embed=embed)
        elif content:
            await destination.send(content)
        _last_message_times[destination.id] = asyncio.get_event_loop().time()
    except discord.HTTPException as e:
        if e.status == 429:
            print("429 Rate limit, czekam...")
            await asyncio.sleep(5)
        else:
            print(f"Błąd wysyłania wiadomości: {e}")
    except Exception as e:
        print(f"Nieznany błąd wysyłania wiadomości: {e}")

# === Tasky do automatycznego odbanowywania i zdejmowania muta ===
@tasks.loop(minutes=1)
async def temp_ban_checker():
    await bot.wait_until_ready()
    now = datetime.utcnow()
    for user_id, unban_time_str in db.get_all_temp_bans():
        unban_time = datetime.fromisoformat(unban_time_str)
        if now >= unban_time:
            for guild in bot.guilds:
                try:
                    await guild.unban(discord.Object(id=user_id), reason="Koniec tymczasowego bana")
                    db.remove_temp_ban(user_id)
                    log_channel = guild.get_channel(LOG_CHANNEL_ID)
                    if log_channel:
                        await safe_send(log_channel, f"Użytkownik <@{user_id}> został automatycznie odbanowany po wygaśnięciu bana.")
                except discord.NotFound:
                    db.remove_temp_ban(user_id)
                except discord.HTTPException as e:
                    if e.status == 429:
                        print("Rate limit przy odbanowywaniu, czekam...")
                        await asyncio.sleep(5)
                    else:
                        print(f"Błąd HTTP: {e}")
                except Exception as e:
                    print(f"❌ Błąd przy odbanowywaniu użytkownika {user_id}: {e}")

@tasks.loop(seconds=30)
async def unmute_checker():
    await bot.wait_until_ready()
    now = datetime.utcnow()
    for user_id, roles_json, unmute_time_str in db.get_all_mutes():
        unmute_time = datetime.fromisoformat(unmute_time_str)
        if now >= unmute_time:
            for guild in bot.guilds:
                member = guild.get_member(user_id)
                if member:
                    try:
                        muted_role = guild.get_role(MUTED_ROLE_ID)
                        if muted_role in member.roles:
                            await member.remove_roles(muted_role, reason="Koniec muta")
                        saved_roles_ids = json.loads(roles_json)
                        roles_to_add = [guild.get_role(rid) for rid in saved_roles_ids if guild.get_role(rid)]
                        await member.add_roles(*roles_to_add, reason="Przywrócenie ról po muctie")
                        db.delete_roles(user_id)
                        log_channel = guild.get_channel(LOG_CHANNEL_ID)
                        if log_channel:
                            await safe_send(log_channel, f"Użytkownik <@{user_id}> został automatycznie odmutowany.")
                    except Exception as e:
                        print(f"❌ Błąd przy odmutowywaniu użytkownika {user_id}: {e}")

# === Dekorator sprawdzający uprawnienia ===
def check_perm(command_name):
    def predicate(interaction: discord.Interaction):
        if has_permission(interaction, command_name):
            return True
        raise app_commands.CheckFailure(f"Brak uprawnień do komendy {command_name}")
    return app_commands.check(predicate)

# === Komendy Slash ===
@bot.event
async def on_ready():
    print(f'Zalogowano jako {bot.user} (ID: {bot.user.id})')
    try:
        synced = await bot.tree.sync()
        print(f"Zsynchronizowano {len(synced)} komend slash.")
    except Exception as e:
        print(f"Błąd synchronizacji komend: {e}")
    temp_ban_checker.start()
    unmute_checker.start()

@bot.tree.command(name="warn", description="Dodaj warn użytkownikowi")
@check_perm("warn")
@app_commands.describe(user="Użytkownik do warnowania", reason="Powód warna")
async def warn(interaction: discord.Interaction, user: discord.Member, reason: str):
    if user == interaction.user:
        await interaction.response.send_message("Nie możesz dać sobie warna.", ephemeral=True)
        return
    db.add_warning(user.id, interaction.user.id, reason)
    warns = db.count_warnings(user.id)

    await interaction.response.send_message(f"Użytkownik {user.mention} został ostrzeżony. Aktualna liczba warnów: {warns}")

    # Log
    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(title="Warn dodany", color=discord.Color.orange())
        embed.add_field(name="Użytkownik", value=f"{user} ({user.id})", inline=False)
        embed.add_field(name="Moderator", value=f"{interaction.user} ({interaction.user.id})", inline=False)
        embed.add_field(name="Powód", value=reason, inline=False)
        embed.add_field(name="Liczba warnów", value=str(warns))
        embed.timestamp = datetime.utcnow()
        await safe_send(log_channel, embed=embed)

    # Automatyczne bany
    try:
        if warns == 5:
            unban_time = datetime.utcnow() + timedelta(days=3)
            await user.ban(reason="Automatyczny ban za 5 warnów")
            db.add_temp_ban(user.id, unban_time)
            await safe_send(interaction.channel, f"Użytkownik {user.mention} został zbanowany na 3 dni za 5 warnów.")
        elif warns == 10:
            unban_time = datetime.utcnow() + timedelta(days=7)
            await user.ban(reason="Automatyczny ban za 10 warnów")
            db.add_temp_ban(user.id, unban_time)
            await safe_send(interaction.channel, f"Użytkownik {user.mention} został zbanowany na 7 dni za 10 warnów.")
        elif warns >= 20:
            await user.ban(reason="Automatyczny ban na stałe za 20 warnów")
            await safe_send(interaction.channel, f"Użytkownik {user.mention} został zbanowany na stałe za 20 warnów.")
    except Exception as e:
        print(f"Błąd przy automatycznym banowaniu: {e}")

@bot.tree.command(name="clearwarns", description="Usuń wszystkie warny użytkownika")
@check_perm("warn")
@app_commands.describe(user="Użytkownik, którego warny chcesz usunąć")
async def clearwarns(interaction: discord.Interaction, user: discord.Member):
    db.clear_warnings(user.id)
    await interaction.response.send_message(f"Warny użytkownika {user.mention} zostały usunięte.")
    log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        await safe_send(log_channel, f"Warny użytkownika {user} zostały usunięte przez {interaction.user}.")

@bot.tree.command(name="mute", description="Wycisz użytkownika")
@check_perm("mute")
@app_commands.describe(user="Użytkownik do wyciszenia", czas="Czas muta (minuty)", reason="Powód muta")
async def mute(interaction: discord.Interaction, user: discord.Member, czas: int, reason: str):
    if MUTED_ROLE_ID not in [role.id for role in interaction.guild.roles]:
        await interaction.response.send_message("Rola mutowana nie istnieje!", ephemeral=True)
        return

    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)

    if muted_role in user.roles:
        await interaction.response.send_message(f"{user.mention} jest już wyciszony.", ephemeral=True)
        return

    # Zapisz aktualne role i daj mute
    current_roles = [role for role in user.roles if role.id != interaction.guild.id and role != muted_role]
    unmute_time = datetime.utcnow() + timedelta(minutes=czas)

    try:
        # Usuń role i dodaj mute
        await user.remove_roles(*current_roles, reason="Mute - usunięcie ról")
        await user.add_roles(muted_role, reason=f"Mute na {czas} minut. Powód: {reason}")

        # Zapisz w bazie
        db.save_roles(user.id, current_roles, unmute_time)

        await interaction.response.send_message(f"Użytkownik {user.mention} został wyciszony na {czas} minut. Powód: {reason}")

        # Log
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title="Mute", color=discord.Color.red())
            embed.add_field(name="Użytkownik", value=f"{user} ({user.id})", inline=False)
            embed.add_field(name="Moderator", value=f"{interaction.user} ({interaction.user.id})", inline=False)
            embed.add_field(name="Powód", value=reason, inline=False)
            embed.add_field(name="Czas muta", value=f"{czas} minut", inline=False)
            embed.timestamp = datetime.utcnow()
            await safe_send(log_channel, embed=embed)

    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas mutowania: {e}", ephemeral=True)

@bot.tree.command(name="unmute", description="Odcisz użytkownika")
@check_perm("mute")
@app_commands.describe(user="Użytkownik do odciszenia")
async def unmute(interaction: discord.Interaction, user: discord.Member):
    muted_role = interaction.guild.get_role(MUTED_ROLE_ID)
    if muted_role not in user.roles:
        await interaction.response.send_message(f"{user.mention} nie jest wyciszony.", ephemeral=True)
        return
    try:
        await user.remove_roles(muted_role, reason="Unmute")
        roles_ids, _ = db.load_roles(user.id)
        roles = [interaction.guild.get_role(rid) for rid in roles_ids if interaction.guild.get_role(rid)]
        if roles:
            await user.add_roles(*roles, reason="Przywrócenie ról po unmute")
        db.delete_roles(user.id)
        await interaction.response.send_message(f"Użytkownik {user.mention} został odciszony.")

        # Log
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await safe_send(log_channel, f"Użytkownik {user} został odciszony przez {interaction.user}.")
    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas odciszania: {e}", ephemeral=True)

@bot.tree.command(name="kick", description="Wyrzuć użytkownika z serwera")
@check_perm("kick")
@app_commands.describe(user="Użytkownik do wyrzucenia", reason="Powód kicka")
async def kick(interaction: discord.Interaction, user: discord.Member, reason: str):
    if user == interaction.user:
        await interaction.response.send_message("Nie możesz wyrzucić siebie.", ephemeral=True)
        return
    try:
        await user.kick(reason=reason)
        await interaction.response.send_message(f"Użytkownik {user.mention} został wyrzucony. Powód: {reason}")

        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title="Kick", color=discord.Color.orange())
            embed.add_field(name="Użytkownik", value=f"{user} ({user.id})", inline=False)
            embed.add_field(name="Moderator", value=f"{interaction.user} ({interaction.user.id})", inline=False)
            embed.add_field(name="Powód", value=reason, inline=False)
            embed.timestamp = datetime.utcnow()
            await safe_send(log_channel, embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas kicka: {e}", ephemeral=True)

@bot.tree.command(name="ban", description="Zbanuj użytkownika")
@check_perm("ban")
@app_commands.describe(user="Użytkownik do zbanowania", reason="Powód bana")
async def ban(interaction: discord.Interaction, user: discord.Member, reason: str):
    if user == interaction.user:
        await interaction.response.send_message("Nie możesz zbanować siebie.", ephemeral=True)
        return
    try:
        await user.ban(reason=reason)
        await interaction.response.send_message(f"Użytkownik {user.mention} został zbanowany. Powód: {reason}")

        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            embed = discord.Embed(title="Ban", color=discord.Color.red())
            embed.add_field(name="Użytkownik", value=f"{user} ({user.id})", inline=False)
            embed.add_field(name="Moderator", value=f"{interaction.user} ({interaction.user.id})", inline=False)
            embed.add_field(name="Powód", value=reason, inline=False)
            embed.timestamp = datetime.utcnow()
            await safe_send(log_channel, embed=embed)
    except Exception as e:
        await interaction.response.send_message(f"Błąd podczas bana: {e}", ephemeral=True)

@bot.tree.command(name="warns", description="Pokaż liczbę warnów użytkownika")
@check_perm("warn")
@app_commands.describe(user="Użytkownik, którego warny chcesz zobaczyć")
async def warns(interaction: discord.Interaction, user: discord.Member):
    warns_count = db.count_warnings(user.id)
    await interaction.response.send_message(f"Użytkownik {user.mention} ma {warns_count} warnów.")

# === Uruchomienie Flask i Discord ===
def main():
    threading.Thread(target=run_flask).start()
    bot.run(os.environ.get("DISCORD_TOKEN"))

if __name__ == "__main__":
    main()
