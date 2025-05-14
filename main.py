import discord
from discord.ext import commands, tasks
from datetime import datetime, time, timedelta
import asyncio
import os
import json
import random
import asyncpg
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
from elo_mvp_system import przetworz_mecz, ranking, profil, wczytaj_dane, zapisz_dane, PUNKTY_ELO, przewidywana_szansa
from collections import Counter


# Flask do keep-alive
app = Flask('')

@app.route('/')
def home():
    return "Bot działa :)"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

keep_alive()

# Intents i bot
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Dane
MAX_SIGNUPS = 10
signups = []
waiting_list = []
log_file = 'signup_log.txt'
event_time = None
team1 = []
team2 = []
mvp_votes = {}
bot.mvp_mapping = {}
bot.mvp_vote_messages = []
bot.last_teams = {}
bot.zwyciezca = None
signup_ids = []
reminder_sent = False
panel_channel = None
panel_message = None
ranking_mode = False
enrollment_locked = False
signup_lock = False
player_nicknames = {}
db_pool = None
last_click_times = {}  # user_id: datetime


wczytaj_dane()

# ---------- BAZA DANYCH ---------- #

db = None

async def connect_to_db():
    global db
    db = await asyncpg.connect(os.getenv("DATABASE_URL"))


db_pool = None

async def connect_lol_nick_pool():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(os.getenv("DATABASE_URL"))
        print("✅ Połączono z bazą nicków LoL-a.")
    except Exception as e:
        print("❌ Błąd przy łączeniu z bazą nicków:", e)




@bot.event
async def on_ready():
    await connect_to_db()
    await connect_lol_nick_pool()
    await create_tables()
    # refresh_panel.start()
    print(f'✅ Zalogowano jako {bot.user.name}')
    check_event_time.start()


async def create_tables():
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS lol_nicknames (
                user_id BIGINT NOT NULL,
                nickname TEXT NOT NULL,
                PRIMARY KEY (user_id, nickname)
            );
        """)
    async with db_pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS ostrzezenia (
                user_id BIGINT PRIMARY KEY,
                liczba INTEGER NOT NULL DEFAULT 0
            );
        ''')


async def get_nicknames(user_id: int) -> list[str]:
    if db_pool is None:
        print("❌ db_pool nie jest połączone!")
        return []
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT nickname FROM lol_nicknames WHERE user_id = $1", user_id)
        return [row["nickname"] for row in rows]


async def add_nicknames(user_id: int, nicknames: list[str]):
    async with db_pool.acquire() as conn:
        for nick in nicknames:
            await conn.execute(
                "INSERT INTO lol_nicknames (user_id, nickname) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                user_id, nick
            )


@tasks.loop(seconds=60)
async def check_event_time():
    global event_time, reminder_sent, tematyczne_event_time, tematyczne_reminder_sent

    now = datetime.now() + timedelta(hours=2)

    if panel_channel is None:
        return

    # Główna lista
    if event_time and not reminder_sent:
        diff = event_time - now
        if timedelta(minutes=14) < diff <= timedelta(minutes=15):
            reminder_sent = True
            if signups:
                mentions = " ".join(user.mention for user in signups)
                await panel_channel.send(f"⏰ **Przypomnienie!** Customy za 15 minut!\n{mentions}", delete_after=2400)
            else:
                await panel_channel.send("⏰ Customy za 15 minut, ale lista główna jest pusta.", delete_after=2400)

    # Tematyczna lista
    if tematyczne_event_time and not tematyczne_reminder_sent:
        diff = tematyczne_event_time - now
        if timedelta(minutes=14) < diff <= timedelta(minutes=15):
            tematyczne_reminder_sent = True
            if tematyczne_gracze:
                mentions = " ".join(f"<@{uid}>" for uid in tematyczne_gracze)
                await panel_channel.send(f"⏰ **Tematyczne przypomnienie!** Start za 15 minut!\n{mentions}", delete_after=1200)
            else:
                await panel_channel.send("⏰ Tematyczne: Brak zapisanych graczy.", delete_after=1200)


async def create_tables():
    await db.execute('''
        CREATE TABLE IF NOT EXISTS gracze (
            nick TEXT PRIMARY KEY,
            elo INTEGER NOT NULL,
            zagrane INTEGER NOT NULL,
            wygrane INTEGER NOT NULL,
            przegrane INTEGER NOT NULL,
            mvp INTEGER NOT NULL
        )
    ''')
    print("✅ Tabela gracze gotowa.")

async def aktualizuj_gracza(nick, elo, zagrane, wygrane, przegrane, mvp):
    await db.execute('''
        INSERT INTO gracze (nick, elo, zagrane, wygrane, przegrane, mvp)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (nick)
        DO UPDATE SET
            elo = EXCLUDED.elo,
            zagrane = EXCLUDED.zagrane,
            wygrane = EXCLUDED.wygrane,
            przegrane = EXCLUDED.przegrane,
            mvp = EXCLUDED.mvp
    ''', nick, elo, zagrane, wygrane, przegrane, mvp)

async def pobierz_gracza(nick):
    row = await db.fetchrow('SELECT * FROM gracze WHERE nick = $1', nick)
    if row:
        return dict(row)
    else:
        return None

# ---------- INFO I OPIS ---------- #



@bot.command(name="info")
async def info(ctx):
    """Wyświetla listę wszystkich dostępnych komend i funkcji."""
    embed = discord.Embed(
        title="ℹ️ Informacje o bocie",
        description="Poniżej znajdziesz listę dostępnych komend oraz przycisków bota.",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🎮 Komendy ogólne",
        value=(
            "`!info` – pokazuje tę wiadomość\n"
            "`!ksante` – easter egg 😄"
        ),
        inline=False
    )

    embed.add_field(
        name="📋 Panel główny (`!panel`)",
        value=(
            "`!panel` – wyświetla panel zapisów\n"
            "`!lista` – pokazuje aktualną listę graczy\n"
            "📌 Przycisk **Zapisz / Wypisz** – dołączenie do gry\n"
            "🕒 Przycisk **Ustaw czas** – ustawia godzinę wydarzenia\n"
            "🧹 Przycisk **Wyczyść listy** – czyści główną i rezerwową\n"
            "🗑️ / ➕ / 📤 – admin może zarządzać graczami\n"
            "📢 Ping – powiadamia graczy"
        ),
        inline=False
    )

    embed.add_field(
        name="🎨 Panel tematyczny (`!tematyczne`)",
        value=(
            "`!tematyczne` – uruchamia panel zapisów z wyborem ról (top, jg, mid, adc, supp)\n"
            "`!tematyczne_test` – dodaje testowych graczy do listy (admin)\n"
            "📌 Przycisk **Dołącz / Wypisz** – z wyborem ról\n"
            "🛠️ **Ustaw czas** – ustawia godzinę wydarzenia\n"
            "📢 **Pinguj graczy** – powiadomienie dla zapisanych\n"
            "✏️ **Zmień nazwę serii** – zmienia nazwę widoczną w embedzie\n"
            "➕ **Dodaj gracza** – admin podaje @gracza i linie\n"
            "🗑️ **Usuń gracza** – admin usuwa wskazanego gracza\n"
            "🧹 **Wyczyść listę** – czyści całą listę\n"
            "🎲 **Losuj drużyny** – dzieli zapisanych na 2 zespoły z pełną kompozycją ról"
        ),
        inline=False
    )

    embed.set_footer(text="Bot przygotowany z myślą o customach League of Legends ❤️")

    await ctx.send(embed=embed)

@bot.command(name="opis")
async def opis(ctx):
    """Wyświetla wersję bota i jego przeznaczenie."""
    embed = discord.Embed(
        title="🤖 KonikBOT – Wersja 5.3",
        description=(
            "KonikBOT stworzony do organizowania gier customowych w League of Legends.\n\n"
            "Umożliwia tworzenie zapisów, organizowanie gier tematycznych z zachowaniem ról.\n"
            "Panel tematyczny pozwala na wydarzenia z motywem serii skinów\n"
        ),
        color=discord.Color.green()
    )

    embed.set_footer(text="Developed by BarowatyPL (geniusz, chuda maszyna, ostatni pod Targonem, pierwszy na midzie, nie dźwiga boskości — niesie ją na barkach)")
    await ctx.send(embed=embed)



@bot.command()
@commands.has_permissions(administrator=True)
async def regulamin(ctx):
    """Wyświetla regulamin customów LoL"""
    try:
        await ctx.message.delete(delay=5)
    except discord.Forbidden:
        pass  # na wypadek braku uprawnień do kasowania

    regulamin_text = (
        "**📜 Regulamin Customów LoL**\n\n"
        "⏰ **Punktualność**\n"
        "Gracz, który nie pojawi się na czas i nie poinformuje o swojej nieobecności przynajmniej 10 minut przed rozpoczęciem gry, łamie zasady.\n\n"
        "🚫 **Zapraszanie osób trzecich**\n"
        "Nie wolno zapraszać osób spoza ustalonego składu bez wiedzy organizatora. Osoba, która to zrobi, zostaje usunięta z rozgrywki.\n\n"
        "🧠 **Zapomniany Smite? Gramy dalej**\n"
        "Nie przerywamy gry z powodu pomyłek takich jak brak smite’a. Gramy dalej – liczy się zabawa, a nie perfekcja.\n\n"
        "❌ **Pomyłki w pickach**\n"
        "Jeśli ktoś wybierze niewłaściwą postać, gra jest kontynuowana.\n"
        "_Wyjątek: w ARAM 5v5 każda drużyna może raz przerwać grę z tego powodu._\n\n"
        "🔁 **Kończysz grę = wypisz się**\n"
        "Gracz kończący udział w grach ma obowiązek wypisać się z listy. Aby zagrać ponownie, należy zapisać się od nowa po przerwie.\n\n"
        "⏳ **Czekanie na osobę z ławki**\n"
        "Na osobę z ławki czekamy maksymalnie 5 minut. Czas może być wydłużony do 10 minut tylko wtedy, gdy wszyscy gracze wyrażą zgodę a osoba potwierdzi swoje szybkie przybycie.\n\n"
        "🧮 **Dobór graczy z ławki**\n"
        "Gracze z ławki są wybierani na podstawie tego, kto pierwszy napisze na kanale, że chce grać – po otrzymanym pingu. Wcześniejsza wiadomość nie ma znaczenia.\n"
        "_Nie liczy się samo wejście na kanał głosowy ani reakcje na wiadomości._\n\n"
        "🚷 **Przerwy tylko w nagłych wypadkach**\n"
        "Przerwy są dopuszczalne wyłącznie w sytuacjach wyjątkowych (np. awaria, pilna sprawa). Nie robimy przerw na toaletę, jedzenie czy inne mniej istotne potrzeby.\n\n"
        "*W przypadku niejasności decyzję podejmują administratorzy.*"
    )

    await ctx.send(regulamin_text, delete_after=1200)


# ---------- SYSTEM ZAPISÓW I WYŚWIETLANIA ---------- #

event_time = None  # dodane globalnie

async def generate_embed_async():
    global signups_locked, event_time, ranking_mode, signups, waiting_list, db_pool

    embed = discord.Embed(title="Panel zapisów", color=discord.Color.green())

    lock_status = "🔒 **Zapisy na listę główną są zatrzymane.**" if signups_locked else "✅ **Zapisy na listę główną są otwarte.**"

    if event_time:
        czas_wydarzenia = f"🕒 **Czas wydarzenia:** {event_time.strftime('%H:%M')}"
    else:
        czas_wydarzenia = "🕒 **Czas wydarzenia nie został jeszcze ustawiony.**"

    ranking_info = "🏆 **Rankingowa**" if ranking_mode else "🎮 **Nierankingowa**"

    embed.description = f"{lock_status}\n{czas_wydarzenia}\n{ranking_info}"

    # Lista główna
    if signups:
        signup_lines = []
        for i, user in enumerate(signups):
            nicknames = await get_nicknames(user.id)
            formatted_nicks = ", ".join(f"`{n}`" for n in nicknames) if nicknames else "*brak nicku*"

            async with db_pool.acquire() as conn:
                result = await conn.fetchrow("SELECT liczba FROM ostrzezenia WHERE user_id = $1", user.id)
                liczba = result["liczba"] if result else 0
                status = "ban" if liczba >= 4 else f"{liczba}/3"

            signup_lines.append(f"{status} • {user.mention} – {formatted_nicks}")
        signup_str = "\n".join(signup_lines)
    else:
        signup_str = "Brak"

    # Lista rezerwowa
    if waiting_list:
        reserve_lines = []
        for i, user in enumerate(waiting_list):
            nicknames = await get_nicknames(user.id)
            formatted_nicks = ", ".join(f"`{n}`" for n in nicknames) if nicknames else "*brak nicku*"

            async with db_pool.acquire() as conn:
                result = await conn.fetchrow("SELECT liczba FROM ostrzezenia WHERE user_id = $1", user.id)
                liczba = result["liczba"] if result else 0
                status = "ban" if liczba >= 4 else f"{liczba}/3"

            reserve_lines.append(f"{status} • {user.mention} – {formatted_nicks}")
        reserve_str = "\n".join(reserve_lines)
    else:
        reserve_str = "Brak"

    embed.add_field(name=f"Lista główna ({len(signups)}/{MAX_SIGNUPS})", value=signup_str, inline=False)





def generate_tematyczne_embed():
    embed = discord.Embed(title=f"Dzisiejsze skiny: {seria1_nazwa} vs {seria2_nazwa}", color=discord.Color.purple())

    if tematyczne_event_time:
        embed.description = f"🕒 **Czas wydarzenia:** {tematyczne_event_time.strftime('%H:%M')}"
    else:
        embed.description = "🕒 **Czas wydarzenia nie został ustawiony.**"

    if not tematyczne_gracze:
        embed.add_field(name="Gracze", value="Brak zapisanych graczy.", inline=False)
    else:
        opis = "\n".join(
            f"{i+1}. <@{uid}> – [{', '.join(data['linie'])}]"
            for i, (uid, data) in enumerate(tematyczne_gracze.items())
        )
        embed.add_field(name="Gracze", value=opis, inline=False)

    return embed




class SignupPanel(discord.ui.View):
    def __init__(self, *, timeout=None, message):
        super().__init__(timeout=timeout)
        self.message = message
        

    @discord.ui.button(label="Zapisz", style=discord.ButtonStyle.success)
    async def signup(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        now = datetime.utcnow()
        cooldown = 10
    
        if user.id in last_click_times and (now - last_click_times[user.id]).total_seconds() < cooldown:
            await interaction.response.send_message(f"⏳ Poczekaj {cooldown} sekund przed ponownym kliknięciem.", ephemeral=True)
            return
    
        last_click_times[user.id] = now
    
        async with signup_lock:
            async with db_pool.acquire() as conn:
                result = await conn.fetchrow("SELECT liczba FROM ostrzezenia WHERE user_id = $1", user.id)
                if result and result["liczba"] >= 4:
                    await interaction.response.send_message("🚫 Masz bana na customy. Skontaktuj się z administracją.", ephemeral=True)
                    return
    
            if any(u.id == user.id for u in signups + waiting_list):
                return
    
            nicknames = await get_nicknames(user.id)
            if not nicknames:
                success = await self.ask_for_nickname(interaction, user)
                if not success:
                    return
                await self.update_message(interaction)
    
            if signups_locked:
                waiting_list.append(user)
                await log_to_discord(f"👤 {user.mention} zapisał się na listę rezerwową (główna zablokowana).")
            else:
                if len(signups) < MAX_SIGNUPS:
                    signups.append(user)
                else:
                    waiting_list.append(user)
                await log_to_discord(f"👤 {user.mention} zapisał się na listę {'główną' if user in signups else 'rezerwową'}.")
    
            await self.update_message(interaction)



    @discord.ui.button(label="Wypisz", style=discord.ButtonStyle.danger)
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        now = datetime.utcnow()
        cooldown = 10  # sekundy
    
        if user.id in last_click_times and (now - last_click_times[user.id]).total_seconds() < cooldown:
            await interaction.response.send_message(
                f"⏳ Poczekaj {cooldown} sekund przed ponownym kliknięciem.",
                ephemeral=True
            )
            return
    
        last_click_times[user.id] = now
    
        if user in signups:
            signups.remove(user)
        elif user in waiting_list:
            waiting_list.remove(user)
        else:
            return
    
        await log_to_discord(f"👤 {user.mention} wypisał się z listy.")
        await self.update_message(interaction)

    @discord.ui.button(label="Zapisz na rezerwę", style=discord.ButtonStyle.secondary, row=1)
    async def signup_reserve(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        now = datetime.utcnow()
        cooldown = 10
    
        if user.id in last_click_times and (now - last_click_times[user.id]).total_seconds() < cooldown:
            await interaction.response.send_message(f"⏳ Poczekaj {cooldown} sekund przed ponownym kliknięciem.", ephemeral=True)
            return
    
        last_click_times[user.id] = now
    
        async with signup_lock:
            async with db_pool.acquire() as conn:
                result = await conn.fetchrow("SELECT liczba FROM ostrzezenia WHERE user_id = $1", user.id)
                if result and result["liczba"] >= 4:
                    await interaction.response.send_message("🚫 Masz bana na customy. Skontaktuj się z administracją.", ephemeral=True)
                    return
    
            if any(u.id == user.id for u in signups + waiting_list):
                await interaction.response.send_message("❗ Już jesteś zapisany na listę.", ephemeral=True)
                return
    
            nicknames = await get_nicknames(user.id)
            if not nicknames:
                success = await self.ask_for_nickname(interaction, user)
                if not success:
                    return
                await self.update_message(interaction)
    
            waiting_list.append(user)
            await log_to_discord(f"👤 {user.mention} zapisał się bezpośrednio na **listę rezerwową** (przycisk).")
            await self.update_message(interaction)


    
    @discord.ui.button(label="Ustaw czas", style=discord.ButtonStyle.primary)
    async def set_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Tylko administrator może ustawić czas wydarzenia.", ephemeral=True, delete_after=10)
            return
        await interaction.response.send_message("Podaj godzinę wydarzenia w formacie `HH:MM`:", ephemeral=True, delete_after=10)
    
        def check(msg): return msg.author == interaction.user and msg.channel == interaction.channel
        try:
            msg = await bot.wait_for("message", timeout=60.0, check=check)
            hour, minute = map(int, msg.content.strip().split(":"))
            global event_time, reminder_sent
            now = datetime.now()
            event_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if event_time < now:
                event_time += timedelta(days=1)
            reminder_sent = False
            await msg.delete()
            await self.update_message(interaction)
            await log_to_discord(f"👤 {interaction.user.mention} ustawił czas wydarzenia na {event_time.strftime('%H:%M')}.")
        except asyncio.TimeoutError:
            await interaction.followup.send("Czas na odpowiedź minął.", ephemeral=True, delete_after=10)
        except ValueError:
            await interaction.followup.send("Niepoprawny format godziny.", ephemeral=True, delete_after=10)
    
    @discord.ui.button(label="🗑️ Usuń gracza", style=discord.ButtonStyle.danger, row=1)
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return
    
        await interaction.response.send_message("Podaj @użytkownika do usunięcia:", ephemeral=True)
        prompt = await interaction.original_response()
    
        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel
    
        try:
            msg = await bot.wait_for("message", timeout=30.0, check=check)
            if not msg.mentions:
                await prompt.delete()
                await msg.delete()
                return
    
            user = msg.mentions[0]
            removed_from = None
    
            if any(u.id == user.id for u in signups):
                signups[:] = [u for u in signups if u.id != user.id]
                removed_from = "głównej"
            elif any(u.id == user.id for u in waiting_list):
                waiting_list[:] = [u for u in waiting_list if u.id != user.id]
                removed_from = "rezerwowej"
    
            if removed_from:
                await log_to_discord(f"👤 {interaction.user.mention} usunął {user.mention} z listy {removed_from}.")
                await self.update_message(interaction)
    
            await prompt.delete()
            await msg.delete()
    
        except asyncio.TimeoutError:
            await prompt.delete()


    @discord.ui.button(label="➕ Dodaj gracza", style=discord.ButtonStyle.success, row=1)
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return
    
        await interaction.response.send_message("Podaj @użytkownika do dodania na listę główną:", ephemeral=True)
        prompt = await interaction.original_response()
    
        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel
    
        try:
            msg = await bot.wait_for("message", timeout=30.0, check=check)
            if not msg.mentions:
                await prompt.delete()
                await msg.delete()
                return
    
            user = msg.mentions[0]
            if any(u.id == user.id for u in signups + waiting_list):
                await prompt.delete()
                await msg.delete()
                return
    
            nicknames = await get_nicknames(user.id)
            if not nicknames:
                success = await self.ask_for_nickname_admin(interaction.channel, user)
                if not success:
                    await prompt.delete()
                    await msg.delete()
                    return
    
            if len(signups) < MAX_SIGNUPS:
                signups.append(user)
                await log_to_discord(f"👤 {interaction.user.mention} dodał {user.mention} do listy głównej.")
                await self.update_message(interaction)
            else:
                await interaction.followup.send("❗ Lista główna jest pełna.", ephemeral=True)
    
            await prompt.delete()
            await msg.delete()
    
        except asyncio.TimeoutError:
            await prompt.delete()

    @discord.ui.button(label="📤 Przenieś z rezerwy", style=discord.ButtonStyle.success, row=1)
    async def move_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return
        
        if len(signups) >= MAX_SIGNUPS:
            await interaction.response.send_message("❗ Lista główna jest pełna.", ephemeral=True)
            return
        
        await interaction.response.send_message("Podaj @użytkownika do przeniesienia z rezerwy:", ephemeral=True)
        prompt = await interaction.original_response()
        
        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel
        
        try:
            msg = await bot.wait_for("message", timeout=30.0, check=check)
            if not msg.mentions:
                await prompt.delete()
                await msg.delete()
                return
        
            user = msg.mentions[0]
            if any(u.id == user.id for u in waiting_list):
                waiting_list[:] = [u for u in waiting_list if u.id != user.id]
                signups.append(user)
                await log_to_discord(f"👤 {interaction.user.mention} przeniósł {user.mention} z rezerwy do listy głównej.")
                await self.update_message(interaction)
        
            await prompt.delete()
            await msg.delete()
        
        except asyncio.TimeoutError:
            await prompt.delete()
    


    
    @discord.ui.button(label="🪃 Wyczyść listy", style=discord.ButtonStyle.danger, row=2)
    async def clear_lists(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return
    
        signups.clear()
        waiting_list.clear()
    
        global event_time, reminder_sent
        event_time = None
        reminder_sent = False
    
        await self.update_message(interaction, log_click=True)
        await log_to_discord(f"👤 {interaction.user.mention} wyczyścił listy i usunął godzinę wydarzenia.")
    
    
    @discord.ui.button(label="📢 Ping lista główna", style=discord.ButtonStyle.primary, row=2)
    async def ping_main(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return
        if not signups:
            return
    
        mentions = " ".join(user.mention for user in signups)
        await interaction.channel.send(f"📢 Lista główna została pingnięta przez {interaction.user.mention}:\n{mentions}")
        await log_to_discord(f"👤 {interaction.user.mention} pingnął listę główną.")


    @discord.ui.button(label="📢 Ping rezerwa", style=discord.ButtonStyle.secondary, row=2)
    async def ping_reserve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return
        if not waiting_list:
            await interaction.response.send_message("❗ Lista rezerwowa jest pusta.", ephemeral=True)
            return
    
        channel_id = 1371869603227242537
        target_channel = interaction.guild.get_channel(channel_id)
    
        if target_channel:
            mentions = " ".join(user.mention for user in waiting_list)
            await target_channel.send(f"📢 Lista rezerwowa została pingnięta przez {interaction.user.mention}:\n{mentions}")
            await log_to_discord(f"👤 {interaction.user.mention} pingnął listę rezerwową w <#{channel_id}>.")


    
    @discord.ui.button(label="🎮 Zmień tryb", style=discord.ButtonStyle.primary, row=2)
    async def toggle_ranking(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return
    
        global ranking_mode
        ranking_mode = not ranking_mode
        await self.update_message(interaction, log_click=True)
        await log_to_discord(f"👤 {interaction.user.mention} zmienił tryb gry na {'🏆 Rankingowa' if ranking_mode else '🎮 Nierankingowa'}.")


    @discord.ui.button(label="🔒 Zatrzymaj zapisy", style=discord.ButtonStyle.primary, row=3)
    async def toggle_lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return
    
        global signups_locked
        signups_locked = not signups_locked
    
        button.label = "✅ Wznów zapisy" if signups_locked else "🔒 Zatrzymaj zapisy"
        await self.update_message(interaction)
        await log_to_discord(f"👤 {interaction.user.mention} {'zatrzymał' if signups_locked else 'wznowił'} zapisy na listę główną.")


    async def update_message(self, interaction: discord.Interaction, log_click: bool = False):
        embed = await generate_embed_async()
    
        try:
            await self.message.edit(embed=embed, view=self)
        except discord.HTTPException:
            pass
    
        if interaction.response.is_done():
            try:
                await interaction.followup.send("✅ Panel zaktualizowany.", ephemeral=True, delete_after=3)
            except:
                pass
        else:
            try:
                await interaction.response.defer()
            except discord.InteractionResponded:
                pass
    
        if log_click:
            await log_to_discord(f"👆 {interaction.user.mention} zmienił stan zapisów.")



    async def ask_for_nickname(self, interaction: discord.Interaction, user: discord.User) -> bool:
        await interaction.response.send_message(
            "🔹 Podaj swój nick z LoL-a (np. `Nick#EUW`). Możesz podać kilka, oddzielając przecinkami.",
            ephemeral=True
        )
    
        def check(msg): return msg.author.id == user.id and msg.channel == interaction.channel
    
        try:
            msg = await bot.wait_for("message", timeout=60.0, check=check)
            nick_input = msg.content.strip()
            nicknames = [n.strip() for n in nick_input.split(",") if n.strip()]
            await msg.delete()
    
            if not nicknames:
                fail = await interaction.followup.send("❌ Nie podano żadnego nicku. Anulowano zapis.", ephemeral=True)
                await asyncio.sleep(5)
                await fail.delete()
                return False
    
            await add_nicknames(user.id, nicknames)
            success = await interaction.followup.send("✅ Nick(i) zapisane.", ephemeral=True)
            await asyncio.sleep(5)
            await success.delete()
            return True
    
        except asyncio.TimeoutError:
            timeout = await interaction.followup.send("⏳ Czas minął. Nie podano nicku.", ephemeral=True)
            await asyncio.sleep(5)
            await timeout.delete()
            return False
    
    async def ask_for_nickname_admin(self, channel, user: discord.User) -> bool:
        try:
            prompt = await channel.send(
                f"🔹 {user.mention}, podaj nick z LoL-a (np. `Nick#EUW`). Możesz podać kilka, oddzielając przecinkami."
            )
    
            def check(msg):
                return msg.author.id == user.id and msg.channel == channel
    
            msg = await bot.wait_for("message", timeout=60.0, check=check)
            nick_input = msg.content.strip()
            nicknames = [n.strip() for n in nick_input.split(",") if n.strip()]
    
            if not nicknames:
                await msg.delete()
                fail_msg = await channel.send("❌ Nie podano żadnego nicku. Anulowano.")
                await asyncio.sleep(5)
                await fail_msg.delete()
                await prompt.delete()
                return False
    
            await add_nicknames(user.id, nicknames)
            await msg.delete()
            confirm = await channel.send("✅ Nick(i) zapisane.")
            await asyncio.sleep(5)
            await confirm.delete()
            await prompt.delete()
            return True
    
        except asyncio.TimeoutError:
            timeout_msg = await channel.send("⏳ Czas minął. Nie podano nicku.")
            await asyncio.sleep(5)
            await timeout_msg.delete()
            await prompt.delete()
            return False
    
        except Exception as e:
            error_msg = await channel.send(f"⚠️ Wystąpił błąd: {e}")
            await asyncio.sleep(5)
            await error_msg.delete()
            return False


@bot.command()
@commands.has_permissions(administrator=True)
async def panel(ctx):
    global panel_channel, panel_message
    panel_channel = ctx.channel
    embed = await generate_embed_async()
    panel_message = await ctx.send(embed=embed)
    view = SignupPanel(message=panel_message)
    await panel_message.edit(view=view)

             

@bot.command(name="lista")
@commands.has_permissions(administrator=True)
async def lista(ctx):
    """Wyświetla listę zapisanych bez przycisków (tylko dla admina)."""
    embed = await generate_embed_async()
    await ctx.send(embed=embed)

# ---------- KOMENDY DO GIER RANKINGOWYCH ---------- #

@bot.command(name="profil")
async def profil(ctx, member: discord.Member = None):
    """Pokazuje profil gracza."""
    if member is None:
        member = ctx.author

    gracz = await pobierz_gracza(str(member))

    if not gracz:
        await ctx.send(f"❌ {member.mention} nie ma jeszcze profilu w rankingu.")
        return

    embed = discord.Embed(title=f"Profil gracza {member.name}", color=discord.Color.blue())
    embed.add_field(name="ELO", value=gracz["elo"], inline=True)
    embed.add_field(name="Zagrane mecze", value=gracz["zagrane"], inline=True)
    embed.add_field(name="Wygrane", value=gracz["wygrane"], inline=True)
    embed.add_field(name="Przegrane", value=gracz["przegrane"], inline=True)
    embed.add_field(name="MVP", value=gracz["mvp"], inline=True)

    await ctx.send(embed=embed)

@bot.command(name="ranking")
async def ranking(ctx, top: int = 10):
    """Pokazuje ranking ELO (domyślnie top 10)."""
    rows = await db.fetch('SELECT * FROM gracze ORDER BY elo DESC LIMIT $1', top)

    if not rows:
        await ctx.send("❌ Brak graczy w rankingu.")
        return

    description = ""
    for i, row in enumerate(rows, start=1):
        description += f"**{i}.** {row['nick']} - {row['elo']} ELO\n"

    embed = discord.Embed(title=f"🏆 Top {top} Graczy", description=description, color=discord.Color.gold())
    await ctx.send(embed=embed)

# ---------- TEMATYCZNE GRANIE ---------- #

import discord
from discord.ext import commands
import asyncio
import random
from datetime import datetime, timedelta

seria1_nazwa = "Seria 1"
seria2_nazwa = "Seria 2"
tematyczne_gracze = {}
tematyczne_event_time = None
tematyczne_reminder_sent = False

class TematycznePanel(discord.ui.View):
    def __init__(self, *, message, timeout=None):
        super().__init__(timeout=timeout)
        self.message = message

    @discord.ui.button(label="✅ Dołącz", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(f"Podaj swoje linie dla **{seria1_nazwa}** (np. top, jg):", ephemeral=True, delete_after=10)

        def check(m): return m.author == interaction.user and m.channel == interaction.channel
        try:
            msg1 = await bot.wait_for("message", timeout=60.0, check=check)
            linie1 = [x.strip().lower() for x in msg1.content.split(",") if x.strip().lower() in ["top", "jg", "mid", "adc", "supp"]]
            await interaction.followup.send(f"Podaj swoje linie dla **{seria2_nazwa}** (np. mid, adc):", ephemeral=True, delete_after=10)
            msg2 = await bot.wait_for("message", timeout=60.0, check=check)
            linie2 = [x.strip().lower() for x in msg2.content.split(",") if x.strip().lower() in ["top", "jg", "mid", "adc", "supp"]]
            if not linie1 or not linie2:
                await interaction.followup.send("❌ Nie podano poprawnych linii.", ephemeral=True, delete_after=10)
                return
            tematyczne_gracze[interaction.user.id] = {
                "user": interaction.user,
                "linie_seria1": linie1,
                "linie_seria2": linie2
            }
            await msg1.delete()
            await msg2.delete()
            await self.update_message()
            await interaction.followup.send(f"✅ Dodano Twoje linie!", ephemeral=True, delete_after=10)
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Czas minął. Spróbuj ponownie.", ephemeral=True, delete_after=10)

    @discord.ui.button(label="❌ Wypisz", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in tematyczne_gracze:
            del tematyczne_gracze[interaction.user.id]
            await self.update_message()
            await interaction.response.send_message("👋 Zostałeś wypisany.", ephemeral=True, delete_after=10)
        else:
            await interaction.response.send_message("❌ Nie jesteś zapisany.", ephemeral=True, delete_after=10)

    @discord.ui.button(label="🛠️ Ustaw czas", style=discord.ButtonStyle.primary)
    async def set_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Tylko administrator może ustawić czas.", ephemeral=True)

        await interaction.response.send_message("🕒 Podaj godzinę wydarzenia w formacie `HH:MM`:", ephemeral=True)

        def check(m): return m.author == interaction.user and m.channel == interaction.channel
        try:
            msg = await bot.wait_for("message", timeout=60.0, check=check)
            hour, minute = map(int, msg.content.strip().split(":"))
            now = datetime.now()
            global tematyczne_event_time, tematyczne_reminder_sent
            tematyczne_event_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if tematyczne_event_time < now:
                tematyczne_event_time += timedelta(days=1)
            tematyczne_reminder_sent = False
            await msg.delete()
            await self.update_message()
            await interaction.followup.send(f"✅ Czas ustawiony na {tematyczne_event_time.strftime('%H:%M')}", ephemeral=True, delete_after=10)
        except:
            await interaction.followup.send("❌ Błąd formatu. Spróbuj `HH:MM`.", ephemeral=True, delete_after=10)

    @discord.ui.button(label="📢 Pinguj graczy", style=discord.ButtonStyle.secondary)
    async def ping(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Tylko administrator może pingować.", ephemeral=True, delete_after=10)
        if not tematyczne_gracze:
            return await interaction.response.send_message("❌ Brak zapisanych graczy.", ephemeral=True, delete_after=10)
        mentions = " ".join(f"<@{uid}>" for uid in tematyczne_gracze)
        await interaction.response.send_message(f"📢 Ping: {mentions}", delete_after=300)

    @discord.ui.button(label="✏️ Zmień nazwę serii", style=discord.ButtonStyle.primary)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Tylko administrator może zmienić nazwy.", ephemeral=True, delete_after=10)

        await interaction.response.send_message("✏️ Podaj nową nazwę serii 1:", ephemeral=True, delete_after=10)
        def check(m): return m.author == interaction.user and m.channel == interaction.channel
        try:
            msg1 = await bot.wait_for("message", timeout=30.0, check=check)
            global seria1_nazwa
            seria1_nazwa = msg1.content.strip()
            await interaction.followup.send("✏️ Podaj nową nazwę serii 2:", ephemeral=True, delete_after=10)
            msg2 = await bot.wait_for("message", timeout=30.0, check=check)
            global seria2_nazwa
            seria2_nazwa = msg2.content.strip()
            await msg1.delete()
            await msg2.delete()
            await self.update_message()
            await interaction.followup.send(f"✅ Ustawiono: **{seria1_nazwa}** vs **{seria2_nazwa}**", ephemeral=True, delete_after=10)
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Czas minął. Nie zmieniono.", ephemeral=True)

    @discord.ui.button(label="🎲 Losuj drużyny", style=discord.ButtonStyle.success)
    async def roll_teams(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("Tylko administrator może losować drużyny.", ephemeral=True, delete_after=10)

        gracze = list(tematyczne_gracze.values())
        if len(gracze) < 10:
            return await interaction.response.send_message("❌ Potrzeba co najmniej 10 graczy do losowania.", ephemeral=True, delete_after=10)

        roles = ["top", "jg", "mid", "adc", "supp"]

        def is_valid(team, seria_key):
            rcount = {r: 0 for r in roles}
            for g in team:
                for r in g[seria_key]:
                    rcount[r] += 1
            return all(rcount[r] >= 1 for r in roles)

        random.shuffle(gracze)
        for _ in range(20):
            random.shuffle(gracze)
            team1 = gracze[:5]
            team2 = gracze[5:10]
            if is_valid(team1, "linie_seria1") and is_valid(team2, "linie_seria2"):
                warning = None
                break
        else:
            await interaction.response.defer()
            warning = "⚠️ Nie udało się utworzyć zrównoważonych drużyn. Losuję losowo."
            random.shuffle(gracze)
            team1 = gracze[:5]
            team2 = gracze[5:10]

        def team_str(team, seria_key):
            return "\n".join(f"• {g['user'].mention} ({', '.join(g[seria_key])})" for g in team)

        embed = discord.Embed(title=f"🎮 {seria1_nazwa} vs {seria2_nazwa}", color=discord.Color.orange())
        if warning:
            embed.description = warning
        embed.add_field(name=f"Drużyna 1 ({seria1_nazwa})", value=team_str(team1, "linie_seria1"), inline=True)
        embed.add_field(name=f"Drużyna 2 ({seria2_nazwa})", value=team_str(team2, "linie_seria2"), inline=True)

        if warning:
            await interaction.followup.send(embed=embed, ephemeral=False, delete_after=600)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=False, delete_after=600)

    async def update_message(self):
        embed = generate_tematyczne_embed()
        await self.message.edit(embed=embed, view=self)

@bot.command()
async def tematyczne(ctx):
    global panel_channel                
    panel_channel = ctx.channel         
    embed = generate_tematyczne_embed()
    msg = await ctx.send(embed=embed)
    view = TematycznePanel(message=msg)
    await msg.edit(view=view)

def generate_tematyczne_embed():
    embed = discord.Embed(title=f"🎮 {seria1_nazwa} vs {seria2_nazwa}", color=discord.Color.blue())
    embed.description = "Kliknij \"Dołącz\" aby zapisać się na event.\nPodajesz swoje linie osobno dla każdej serii!"
    if tematyczne_gracze:
        for g in tematyczne_gracze.values():
            embed.add_field(name=g['user'].name, value=f"{seria1_nazwa}: {', '.join(g['linie_seria1'])}\n{seria2_nazwa}: {', '.join(g['linie_seria2'])}", inline=False)
    else:
        embed.add_field(name="Brak zapisanych graczy", value="Czekamy na zgłoszenia!", inline=False)
    return embed

@bot.command(name="tematyczne_test")
@commands.has_permissions(administrator=True)
async def tematyczne_test(ctx):
    from types import SimpleNamespace

    test_gracze = [
        ("Gracz 1 (adc, top)", ["adc", "top"]),
        ("Gracz 2 (jg, mid)", ["jg", "mid"]),
        ("Gracz 3 (supp)", ["supp"]),
        ("Gracz 4 (mid)", ["mid"]),
        ("Gracz 5 (adc)", ["adc"]),
        ("Gracz 6 (jg)", ["jg"]),
        ("Gracz 7 (top)", ["top"]),
        ("Gracz 8 (supp)", ["supp"]),
        ("Gracz 9 (mid)", ["mid"]),
        ("Gracz 10 (top, jg)", ["top", "jg"]),
    ]

    base_id = 900000000000000000
    for i, (name, roles) in enumerate(test_gracze):
        mock_user = SimpleNamespace(
            id=base_id + i,
            mention=f"<@{base_id + i}>",
            name=name
        )
        tematyczne_gracze[mock_user.id] = {
            "user": mock_user,
            "linie_seria1": roles,
            "linie_seria2": roles
        }

    await ctx.send("✅ Dodano 10 testowych graczy z rolami.", delete_after=10)


# ---------- KOMENDY DO NICKÓW ---------- #

@bot.command(help="Dodaje nick(i) LoL do użytkownika. Można podać wiele, oddzielając przecinkami.\nPrzykład: !dodajnick @nick_dc nick#EUNE, Nick2#EUNE")
@commands.has_permissions(administrator=True)
async def dodajnick(ctx, member: discord.Member = None, *, nicknames: str = None):
    await ctx.message.delete(delay=5)

    if not member or not nicknames:
        await ctx.send("📌 Użycie: `!dodajnick @użytkownik Nick#EUW, Smurf#EUNE`", delete_after=10)
        return

    nickname_list = [n.strip() for n in nicknames.split(",") if n.strip()]
    if not nickname_list:
        await ctx.send("❌ Nie podano żadnego nicku.", delete_after=10)
        return

    await add_nicknames(member.id, nickname_list)
    await ctx.send(f"✅ Dodano {len(nickname_list)} nick(ów) dla {member.mention}.", delete_after=10)



@bot.command(help="Usuwa nick LoL gracza.\nPrzykład: !usunnick @nick_dc nick#EUNE")
@commands.has_permissions(administrator=True)
async def usunnick(ctx, member: discord.Member = None, *, nickname: str = None):
    await ctx.message.delete(delay=5)

    if not member or not nickname:
        await ctx.send("📌 Użycie: `!usunnick @użytkownik Nick#EUW`", delete_after=10)
        return

    async with db_pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM lol_nicknames WHERE user_id = $1 AND nickname = $2",
            member.id, nickname
        )
        if result.endswith("0"):
            await ctx.send(f"❌ Nick `{nickname}` nie został znaleziony u {member.mention}.", delete_after=10)
        else:
            await ctx.send(f"🏀 Nick `{nickname}` został usunięty dla {member.mention}.", delete_after=10)


@bot.command(help="Wyświetla zapisane nicki gracza. Jeśli nie podasz gracza, pokaże Twoje.\nPrzykład: !nicki @nick_dc")
async def nicki(ctx, member: discord.Member = None):
    await ctx.message.delete(delay=5)

    target = member or ctx.author
    nicknames = await get_nicknames(target.id)

    if not nicknames:
        await ctx.send(f"🔎 {target.mention} nie ma zapisanych żadnych nicków.", delete_after=10)
    else:
        formatted = "\n".join(f"`{nick}`" for nick in nicknames)
        await ctx.send(f"📋 Nicki zapisane dla {target.mention}:\n{formatted}", delete_after=10)







# ---------- KOMENDY DLA BEKI ---------- #

@bot.command(name="ksante")
async def ksante(ctx):
    tekst = ("K'Sante👤 4,700 HP 💪 329 Armor 🤷‍♂️ 201 MR 💦 Unstoppable 🚫 "
             "A Shield 🛡 Goes over walls 🧱 Has Airborne 🌪 "
             "Cooldown is only ☝ second too 🕐 It costs 15 Mana 🧙‍♂️")
    
    await ctx.send(tekst, delete_after=300)

@bot.command(name="najlepszy")
async def info(ctx):
    """Wyświetla informacje o bocie lub wydarzeniu."""
    tekst = ("Jestem Kurwa świetny, jestem najlepszy, jestem Bogiem tej gry!!!")
    await ctx.send(tekst, delete_after=300)

@bot.command(name="lulu")
async def info(ctx):
    """Wyświetla informacje o bocie lub wydarzeniu."""
    tekst = ("JEBANA DZIWKA Z KAPELUSZEM!!!")
    await ctx.send(tekst, delete_after=300)

@bot.command(name="daj")
async def info(ctx):
    """Wyświetla informacje o bocie lub wydarzeniu."""
    tekst = ("DAJCIE MI GO!!!")
    await ctx.send(tekst, delete_after=300)


# ---------- LOGI ---------- #

async def log_to_discord(message: str):
    log_channel_id = 1366403342695141446
    channel = bot.get_channel(log_channel_id)
    if channel:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        await channel.send(f"[{timestamp}] {message}")

@bot.command(name="logi")
@commands.has_permissions(administrator=True)
async def logi(ctx, liczba: int = 10):
    """Pokazuje ostatnie X logów z kanału logów (domyślnie 10)."""
    log_channel_id = 1366403342695141446
    channel = bot.get_channel(log_channel_id)

    if not channel:
        await ctx.send("❌ Nie mogę znaleźć kanału logów.")
        return

    messages = [msg async for msg in channel.history(limit=liczba)]
    messages.reverse()

    formatted = "\n".join(msg.content for msg in messages)
    if not formatted:
        formatted = "Brak logów do wyświetlenia."

    await ctx.send(f"📄 **Ostatnie {liczba} logów:**\n```{formatted}```")


# ---------- INNE ---------- #

@tasks.loop(minutes=5)
async def refresh_panel():
    if panel_channel:
        try:
            embed = await generate_embed_async()
            message = await panel_channel.send(embed=embed)
            view = SignupPanel(message=message)
            await message.edit(view=view)
        except Exception as e:
            print(f"Błąd podczas odświeżania panelu: {e}")

async def odswiez_panel():
    global panel_message
    if panel_message:
        try:
            embed = await generate_embed_async()
            view = SignupPanel(message=panel_message)
            await panel_message.edit(embed=embed, view=view)
        except Exception as e:
            print(f"❌ Błąd przy odświeżaniu panelu: {e}")



@bot.command(name="bancustom")
@commands.has_permissions(administrator=True)
async def bancustom(ctx, member: discord.Member):
    try:
        await ctx.message.delete(delay=5)
    except discord.Forbidden:
        pass

    async with db_pool.acquire() as conn:
        result = await conn.fetchrow("SELECT liczba FROM ostrzezenia WHERE user_id = $1", member.id)
        liczba = result["liczba"] if result else 0
        liczba += 1

        await conn.execute(
            "INSERT INTO ostrzezenia (user_id, liczba) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET liczba = $2",
            member.id, liczba
        )

        status = "ban" if liczba >= 4 else f"{liczba}/3"

        await log_to_discord(f"🚫 {ctx.author.mention} dał `bancustom` dla {member.mention} – teraz ma: **{status}**")

    await odswiez_panel()

        
@bot.command(name="usunbana")
@commands.has_permissions(administrator=True)
async def usunbana(ctx, member: discord.Member):
    try:
        await ctx.message.delete(delay=5)
    except discord.Forbidden:
        pass

    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM ostrzezenia WHERE user_id = $1", member.id)

    await log_to_discord(f"✅ {ctx.author.mention} usunął ostrzeżenia dla {member.mention}")
    await odswiez_panel()






bot.run(TOKEN)
