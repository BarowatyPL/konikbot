# 🤖 KonikBOT v6.2
# Na potrzeby serwera Konikolandia
# Właścicielem jest BarowatyPL

import os
import json
import random
import asyncio
from datetime import datetime, timedelta, timezone
from threading import Thread
from types import SimpleNamespace

import discord
from discord.ext import commands, tasks
from discord.ui import View, Select, Button
from discord import SelectOption, Interaction, ButtonStyle

import asyncpg
from dotenv import load_dotenv
from flask import Flask

try:
    from elo_mvp_system import (
        przetworz_mecz,
        ranking as elo_ranking,
        profil as elo_profil,
        wczytaj_dane,
        zapisz_dane,
        PUNKTY_ELO,
        przewidywana_szansa,
    )
except Exception:
    przetworz_mecz = None
    elo_ranking = None
    elo_profil = None
    wczytaj_dane = lambda: None
    zapisz_dane = lambda: None
    PUNKTY_ELO = {}
    przewidywana_szansa = None


# ---------- KEEP ALIVE ---------- #

app = Flask("")

@app.route("/")
def home():
    return "Bot działa :)"

def run_flask():
    app.run(host="0.0.0.0", port=8080)

def keep_alive():
    Thread(target=run_flask).start()


# ---------- KONFIGURACJA ---------- #

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

BOT_ADMIN_ROLE = "KonikAdmin"

MAX_SIGNUPS = 10
BUTTON_COOLDOWN = 10

LOG_CHANNEL_ID = 1366403342695141446
RESERVE_PING_CHANNEL_ID = 1371869603227242537
HOF_CHANNEL_ID = 1216013668773265458

RANGI = [
    "Iron", "Bronze", "Silver", "Gold", "Platinum",
    "Emerald", "Diamond", "Master", "Grandmaster", "Challenger"
]

RANGA_EMOJI = {
    "Iron": "⬛",
    "Bronze": "🟫",
    "Silver": "⬜",
    "Gold": "🟧",
    "Platinum": "🟩",
    "Emerald": "🟢",
    "Diamond": "🟦",
    "Master": "🟪",
    "Grandmaster": "🟥",
    "Challenger": "🟨",
    "Unranked": "⚪"
}


# ---------- BOT ---------- #

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)


# ---------- GLOBALNE DANE ---------- #

db_pool = None

signups = []
waiting_list = []

event_time = None
reminder_sent = False

panel_channel = None
panel_message = None

ranking_mode = False
signups_locked = False
signup_lock = asyncio.Lock()

last_click_times = {}
rep_cooldown = {}

# Tematyczne
seria1_nazwa = "Seria 1"
seria2_nazwa = "Seria 2"
tematyczne_main = {}
tematyczne_reserve = {}
tematyczne_event_time = None
tematyczne_reminder_sent = False
tematyczne_panel_message = None


# ---------- TESTOWY USER ---------- #

class FakeUser:
    def __init__(self, name):
        self.display_name = name
        self.name = name
        self.mention = name
        self.id = abs(hash(name)) % 10_000_000_000


# ---------- UPRAWNIENIA ---------- #

def is_bot_admin():
    async def predicate(ctx):
        return any(role.name == BOT_ADMIN_ROLE for role in ctx.author.roles)
    return commands.check(predicate)

def has_panel_access(user):
    return (
        user.guild_permissions.administrator
        or any(role.name == BOT_ADMIN_ROLE for role in user.roles)
    )


# ---------- BAZA DANYCH ---------- #

async def connect_to_db():
    global db_pool

    if not DATABASE_URL:
        print("❌ Brak DATABASE_URL w .env")
        return

    try:
        db_pool = await asyncpg.create_pool(DATABASE_URL)
        print("✅ Połączono z bazą danych.")
    except Exception as e:
        print("❌ Błąd połączenia z bazą:", e)


async def create_tables():
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS lol_nicknames (
                user_id BIGINT NOT NULL,
                nickname TEXT NOT NULL,
                rank TEXT DEFAULT 'Unranked',
                PRIMARY KEY (user_id, nickname)
            );
        """)

        await conn.execute("""
            ALTER TABLE lol_nicknames
            ADD COLUMN IF NOT EXISTS rank TEXT DEFAULT 'Unranked';
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ostrzezenia (
                user_id BIGINT PRIMARY KEY,
                liczba INTEGER NOT NULL DEFAULT 0
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                user_id BIGINT PRIMARY KEY,
                messages INTEGER DEFAULT 0,
                mentions INTEGER DEFAULT 0,
                hearts_received INTEGER DEFAULT 0,
                flags_received INTEGER DEFAULT 0,
                voice_seconds INTEGER DEFAULT 0
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS voice_sessions (
                user_id BIGINT PRIMARY KEY,
                join_time TIMESTAMP
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS gracze (
                nick TEXT PRIMARY KEY,
                elo INTEGER NOT NULL DEFAULT 1000,
                zagrane INTEGER NOT NULL DEFAULT 0,
                wygrane INTEGER NOT NULL DEFAULT 0,
                przegrane INTEGER NOT NULL DEFAULT 0,
                mvp INTEGER NOT NULL DEFAULT 0
            );
        """)

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reputacja (
                user_id BIGINT PRIMARY KEY,
                punkty INTEGER NOT NULL DEFAULT 0
            );
        """)

    print("✅ Wszystkie tabele gotowe.")


async def get_nicknames(user_id: int):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT nickname, COALESCE(rank, 'Unranked') AS rank
            FROM lol_nicknames
            WHERE user_id = $1
            ORDER BY nickname
            """,
            user_id
        )
        return [(row["nickname"], row["rank"]) for row in rows]


async def add_nicknames(user_id: int, nicknames: list[str], rank: str = "Unranked"):
    async with db_pool.acquire() as conn:
        for nick in nicknames:
            await conn.execute(
                """
                INSERT INTO lol_nicknames (user_id, nickname, rank)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id, nickname) DO NOTHING
                """,
                user_id,
                nick,
                rank
            )


async def update_rank(user_id: int, nickname: str, new_rank: str):
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE lol_nicknames
            SET rank = $1
            WHERE user_id = $2 AND nickname = $3
            """,
            new_rank,
            user_id,
            nickname
        )


async def get_warning_count(user_id: int) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT liczba FROM ostrzezenia WHERE user_id = $1",
            user_id
        )
        return row["liczba"] if row else 0


async def aktualizuj_gracza(nick, elo, zagrane, wygrane, przegrane, mvp):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO gracze (nick, elo, zagrane, wygrane, przegrane, mvp)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (nick)
            DO UPDATE SET
                elo = EXCLUDED.elo,
                zagrane = EXCLUDED.zagrane,
                wygrane = EXCLUDED.wygrane,
                przegrane = EXCLUDED.przegrane,
                mvp = EXCLUDED.mvp
        """, nick, elo, zagrane, wygrane, przegrane, mvp)


async def pobierz_gracza(nick):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM gracze WHERE nick = $1",
            nick
        )
        return dict(row) if row else None


async def dodaj_reputacje(user_id: int, ilosc: int):
    async with db_pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO reputacja (user_id, punkty)
            VALUES ($1, $2)
            ON CONFLICT (user_id)
            DO UPDATE SET punkty = reputacja.punkty + $2
        """, user_id, ilosc)


async def pobierz_reputacje(user_id: int) -> int:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT punkty FROM reputacja WHERE user_id = $1",
            user_id
        )
        return row["punkty"] if row else 0


# ---------- LOGI ---------- #

async def log_to_discord(message: str):
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        await channel.send(f"[{timestamp}] {message}")


async def log_reputacja(giver: discord.Member, receiver: discord.Member, zmiana: int):
    akcja = "➕ Dał" if zmiana > 0 else "➖ Odjął"
    await log_to_discord(
        f"{akcja} reputację: {giver.mention} ➝ {receiver.mention} ({zmiana:+} pkt)"
    )


# ---------- HELPERY ---------- #

def check_button_cooldown(user_id: int) -> bool:
    now = datetime.now(timezone.utc)

    if user_id in last_click_times:
        diff = (now - last_click_times[user_id]).total_seconds()
        if diff < BUTTON_COOLDOWN:
            return False

    last_click_times[user_id] = now
    return True


async def safe_delete_message(message):
    try:
        await message.delete()
    except Exception:
        pass


async def ask_for_message(interaction, text, timeout=60):
    await interaction.response.send_message(text, ephemeral=True)

    def check(msg):
        return msg.author.id == interaction.user.id and msg.channel == interaction.channel

    try:
        msg = await bot.wait_for("message", timeout=timeout, check=check)
        return msg
    except asyncio.TimeoutError:
        return None


# ---------- EMBEDY ---------- #

async def format_player_list(players):
    if not players:
        return "Brak"

    lines = []

    for user in players:
        nicki = await get_nicknames(user.id)

        if nicki:
            formatted_nicks = ", ".join(f"`{nick}`" for nick, _ in nicki)
            first_rank = nicki[0][1]
            rank_emoji = RANGA_EMOJI.get(first_rank, RANGA_EMOJI["Unranked"])
        else:
            formatted_nicks = "*brak nicku*"
            rank_emoji = RANGA_EMOJI["Unranked"]

        warnings = await get_warning_count(user.id)
        status = "ban" if warnings >= 4 else f"{warnings}/3"

        lines.append(f"{status} • {rank_emoji} {user.mention} – {formatted_nicks}")

    return "\n".join(lines)


async def generate_main_embed():
    embed = discord.Embed(
        title="Panel zapisów",
        color=discord.Color.green()
    )

    lock_status = (
        "🔒 **Zapisy na listę główną są zatrzymane.**"
        if signups_locked
        else "✅ **Zapisy na listę główną są otwarte.**"
    )

    if event_time:
        czas = f"🕒 **Czas wydarzenia:** {event_time.strftime('%H:%M')}"
    else:
        czas = "🕒 **Czas wydarzenia nie został jeszcze ustawiony.**"

    tryb = "🏆 **Rankingowa**" if ranking_mode else "🎮 **Nierankingowa**"

    embed.description = f"{lock_status}\n{czas}\n{tryb}"

    main_list = await format_player_list(signups)
    reserve_list = await format_player_list(waiting_list)

    embed.add_field(
        name=f"Lista główna ({len(signups)}/{MAX_SIGNUPS})",
        value=main_list,
        inline=False
    )

    embed.add_field(
        name="Lista rezerwowa",
        value=reserve_list,
        inline=False
    )

    return embed


def generate_tematyczne_embed():
    embed = discord.Embed(
        title=f"🎮 {seria1_nazwa} vs {seria2_nazwa}",
        color=discord.Color.blue()
    )

    if tematyczne_event_time:
        embed.description = f"🕒 **Godzina wydarzenia:** {tematyczne_event_time.strftime('%H:%M')}"
    else:
        embed.description = "Kliknij **Dołącz**, aby zapisać się na event."

    if tematyczne_main:
        value = "\n".join(
            f"{i + 1}. {user.mention}"
            for i, user in enumerate(tematyczne_main.values())
        )
    else:
        value = "Brak zapisanych graczy."

    embed.add_field(
        name=f"✅ Główna lista ({len(tematyczne_main)}/10)",
        value=value,
        inline=False
    )

    if tematyczne_reserve:
        value = "\n".join(
            f"{i + 1}. {user.mention}"
            for i, user in enumerate(tematyczne_reserve.values())
        )
    else:
        value = "Brak graczy na rezerwie."

    embed.add_field(
        name="📋 Rezerwa",
        value=value,
        inline=False
    )

    return embed


# ---------- PANEL RANG ---------- #

@bot.command(name="rangipanel")
@commands.has_permissions(administrator=True)
async def rangipanel(ctx):
    await log_to_discord(
        f"⌨️ {ctx.author.mention} użył komendy `!rangipanel` na kanale {ctx.channel.mention}"
    )

    view = RankingPanelView()
    await ctx.send(
        "📌 **Panel nicków i rang**\n"
        "Tutaj możesz dodać nick, zmienić nick, usunąć nick oraz ustawić rangę.",
        view=view
    )


@bot.command(help="Dodaje nick(i) LoL do użytkownika.")
@commands.has_permissions(administrator=True)
async def dodajnick(ctx, member: discord.Member = None, *, nicknames: str = None):
    await log_to_discord(
        f"⌨️ {ctx.author.mention} użył komendy `!dodajnick` na kanale {ctx.channel.mention}"
    )

    try:
        await ctx.message.delete(delay=5)
    except Exception:
        pass

    if not member or not nicknames:
        return await ctx.send(
            "📌 Użycie: `!dodajnick @użytkownik Nick#EUW, Smurf#EUNE`",
            delete_after=10
        )

    nickname_list = [n.strip() for n in nicknames.split(",") if n.strip()]

    if not nickname_list:
        return await ctx.send(
            "❌ Nie podano żadnego nicku.",
            delete_after=10
        )

    await add_nicknames(member.id, nickname_list)

    await log_to_discord(
        f"➕ {ctx.author.mention} dodał nicki dla {member.mention}: `{', '.join(nickname_list)}`"
    )

    await ctx.send(
        f"✅ Dodano {len(nickname_list)} nick(ów) dla {member.mention}.",
        delete_after=10
    )


@bot.command(help="Usuwa nick LoL gracza.")
@commands.has_permissions(administrator=True)
async def usunnick(ctx, member: discord.Member = None, *, nickname: str = None):
    await log_to_discord(
        f"⌨️ {ctx.author.mention} użył komendy `!usunnick` na kanale {ctx.channel.mention}"
    )

    try:
        await ctx.message.delete(delay=5)
    except Exception:
        pass

    if not member or not nickname:
        return await ctx.send(
            "📌 Użycie: `!usunnick @użytkownik Nick#EUW`",
            delete_after=10
        )

    async with db_pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM lol_nicknames
            WHERE user_id = $1 AND nickname = $2
            """,
            member.id,
            nickname
        )

    if result.endswith("0"):
        await log_to_discord(
            f"❌ {ctx.author.mention} próbował usunąć nick `{nickname}` dla {member.mention}, ale go nie znaleziono."
        )

        await ctx.send(
            f"❌ Nick `{nickname}` nie został znaleziony u {member.mention}.",
            delete_after=10
        )
    else:
        await log_to_discord(
            f"🗑️ {ctx.author.mention} usunął nick `{nickname}` dla {member.mention}."
        )

        await ctx.send(
            f"🗑️ Nick `{nickname}` został usunięty dla {member.mention}.",
            delete_after=10
        )


@bot.command(help="Wyświetla zapisane nicki gracza.")
async def nicki(ctx, member: discord.Member = None):
    await log_to_discord(
        f"⌨️ {ctx.author.mention} użył komendy `!nicki` na kanale {ctx.channel.mention}"
    )

    try:
        await ctx.message.delete(delay=5)
    except Exception:
        pass

    target = member or ctx.author
    nicknames = await get_nicknames(target.id)

    if not nicknames:
        await ctx.send(
            f"🔎 {target.mention} nie ma zapisanych żadnych nicków.",
            delete_after=10
        )
    else:
        formatted = "\n".join(
            f"`{nick}` — {rank}"
            for nick, rank in nicknames
        )

        await ctx.send(
            f"📋 Nicki zapisane dla {target.mention}:\n{formatted}",
            delete_after=20
        )


class RankingPanelView(View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="➕ Dodaj nick",
        style=ButtonStyle.success,
        custom_id="dodaj_nick_button"
    )
    async def dodaj_nick(self, interaction: Interaction, button: Button):

        await log_to_discord(
            f"🖱️ {interaction.user.mention} kliknął `➕ Dodaj nick`"
        )

        await interaction.response.send_message(
            "📥 Napisz teraz swój nick z LoL-a.\n"
            "Możesz podać kilka oddzielonych przecinkami.\n"
            "Przykład: `Nick#EUW, Smurf#EUNE`",
            ephemeral=True
        )

        def check(msg):
            return (
                msg.author.id == interaction.user.id
                and msg.channel.id == interaction.channel.id
                and not msg.author.bot
            )

        try:
            msg = await bot.wait_for(
                "message",
                timeout=60,
                check=check
            )

            nicknames = [
                n.strip()
                for n in msg.content.split(",")
                if n.strip()
            ]

            try:
                await msg.delete()
            except Exception:
                pass

            if not nicknames:
                return await interaction.followup.send(
                    "❌ Nie podano żadnych nicków.",
                    ephemeral=True
                )

            await add_nicknames(
                interaction.user.id,
                nicknames
            )

            await log_to_discord(
                f"➕ {interaction.user.mention} dodał nicki: `{', '.join(nicknames)}`"
            )

            await interaction.followup.send(
                f"✅ Dodano nick(i): `{', '.join(nicknames)}`",
                ephemeral=True
            )

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏳ Czas minął.",
                ephemeral=True
            )

    @discord.ui.button(
        label="✏️ Zmień nick",
        style=ButtonStyle.primary,
        custom_id="zmien_nick_button"
    )
    async def zmien_nick(self, interaction: Interaction, button: Button):

        nicki = await get_nicknames(interaction.user.id)

        if not nicki:
            return await interaction.response.send_message(
                "❌ Nie masz żadnych nicków.",
                ephemeral=True
            )

        view = ZmienNickDropdownView(
            interaction.user,
            [n for n, _ in nicki]
        )

        await interaction.response.send_message(
            "✏️ Wybierz nick do zmiany:",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(
        label="🗑️ Usuń nick",
        style=ButtonStyle.danger,
        custom_id="usun_nick_button"
    )
    async def usun_nick(self, interaction: Interaction, button: Button):

        nicki = await get_nicknames(interaction.user.id)

        if not nicki:
            return await interaction.response.send_message(
                "❌ Nie masz żadnych nicków.",
                ephemeral=True
            )

        view = UsunNickDropdownView(
            interaction.user,
            [n for n, _ in nicki]
        )

        await interaction.response.send_message(
            "🗑️ Wybierz nick do usunięcia:",
            view=view,
            ephemeral=True
        )

    @discord.ui.button(
        label="🏅 Ustaw rangę",
        style=ButtonStyle.secondary,
        custom_id="ustaw_range_button"
    )
    async def ustaw_range(self, interaction: Interaction, button: Button):

        nicki = await get_nicknames(interaction.user.id)
        nicknames_only = [n for n, _ in nicki]

        if not nicknames_only:
            return await interaction.response.send_message(
                "❌ Nie masz żadnych nicków.",
                ephemeral=True
            )

        view = UstawRangaDropdownView(
            interaction.user,
            nicknames_only
        )

        await interaction.response.send_message(
            "🏅 Wybierz nick i rangę:",
            view=view,
            ephemeral=True
        )


# ---------- PANEL GŁÓWNY ---------- #

class SignupPanel(View):
    def __init__(self, *, message=None, timeout=None):
        super().__init__(timeout=timeout)
        self.message = message

    async def update_message(self, interaction=None, log_click=False):
        embed = await generate_main_embed()

        if self.message:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

        if interaction:
            if interaction.response.is_done():
                try:
                    await interaction.followup.send(
                        "✅ Panel zaktualizowany.",
                        ephemeral=True,
                        delete_after=3
                    )
                except Exception:
                    pass
            else:
                try:
                    await interaction.response.defer()
                except Exception:
                    pass

            if log_click:
                await log_to_discord(f"👆 {interaction.user.mention} zmienił stan zapisów.")

    async def ask_for_nickname(self, interaction, user) -> bool:
        await interaction.response.send_message(
            "🔹 Podaj swój nick z LoL-a, np. `Nick#EUW`. Możesz podać kilka, oddzielając przecinkami.",
            ephemeral=True
        )

        def check(msg):
            return msg.author.id == user.id and msg.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=60, check=check)
            nicknames = [n.strip() for n in msg.content.split(",") if n.strip()]
            await safe_delete_message(msg)

            if not nicknames:
                await interaction.followup.send(
                    "❌ Nie podano żadnego nicku. Anulowano zapis.",
                    ephemeral=True
                )
                return False

            await add_nicknames(user.id, nicknames)
            await interaction.followup.send(
                "✅ Nick(i) zapisane.",
                ephemeral=True
            )
            return True

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏳ Czas minął. Nie podano nicku.",
                ephemeral=True
            )
            return False

    async def ask_for_nickname_admin(self, channel, user) -> bool:
        prompt = None

        try:
            prompt = await channel.send(
                f"🔹 Podaj nick(i) LoL-a dla {user.mention}, oddziel przecinkami:"
            )

            def check(msg):
                return has_panel_access(msg.author) and msg.channel == channel

            msg = await bot.wait_for("message", timeout=60, check=check)
            nicknames = [n.strip() for n in msg.content.split(",") if n.strip()]
            await safe_delete_message(msg)

            if not nicknames:
                await channel.send("❌ Nie podano żadnego nicku. Anulowano.", delete_after=5)
                await safe_delete_message(prompt)
                return False

            await add_nicknames(user.id, nicknames)
            await channel.send(f"✅ Dodano nick(i) dla {user.mention}.", delete_after=5)
            await safe_delete_message(prompt)
            return True

        except asyncio.TimeoutError:
            await channel.send("⏳ Czas minął. Nie podano nicku.", delete_after=5)
            if prompt:
                await safe_delete_message(prompt)
            return False

    @discord.ui.button(label="Zapisz", style=discord.ButtonStyle.success)
    async def signup(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user

        if not check_button_cooldown(user.id):
            return await interaction.response.send_message(
                f"⏳ Poczekaj {BUTTON_COOLDOWN} sekund przed ponownym kliknięciem.",
                ephemeral=True
            )

        async with signup_lock:
            warnings = await get_warning_count(user.id)

            if warnings >= 4:
                return await interaction.response.send_message(
                    "🚫 Masz bana na customy. Skontaktuj się z administracją.",
                    ephemeral=True
                )

            if any(u.id == user.id for u in signups + waiting_list):
                return await interaction.response.send_message(
                    "❗ Jesteś już zapisany.",
                    ephemeral=True
                )

            nicknames = await get_nicknames(user.id)

            if not nicknames:
                success = await self.ask_for_nickname(interaction, user)
                if not success:
                    return

            if signups_locked:
                waiting_list.append(user)
                await log_to_discord(
                    f"👤 {user.mention} zapisał się na listę rezerwową, bo główna jest zablokowana."
                )
            elif len(signups) < MAX_SIGNUPS:
                signups.append(user)
                await log_to_discord(f"✅ {interaction.user.mention} zapisał się na listę główną.")
            else:
                waiting_list.append(user)
                await log_to_discord(f"✅ {interaction.user.mention} zapisał się na listę rezerwową (główna pełna).")

            await self.update_message(interaction)
            

    @discord.ui.button(label="Wypisz", style=discord.ButtonStyle.danger)
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user

        if not check_button_cooldown(user.id):
            return await interaction.response.send_message(
                f"⏳ Poczekaj {BUTTON_COOLDOWN} sekund przed ponownym kliknięciem.",
                ephemeral=True
            )

        removed = False

        if any(u.id == user.id for u in signups):
            signups[:] = [u for u in signups if u.id != user.id]
            removed = True

        if any(u.id == user.id for u in waiting_list):
            waiting_list[:] = [u for u in waiting_list if u.id != user.id]
            removed = True

        if not removed:
            return await interaction.response.send_message(
                "❌ Nie jesteś zapisany.",
                ephemeral=True
            )

        await log_to_discord(f"❌ {interaction.user.mention} wypisał się z listy.")
        await self.update_message(interaction)


    @discord.ui.button(label="Zapisz na rezerwę", style=discord.ButtonStyle.secondary, row=1)
    async def signup_reserve(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user

        if not check_button_cooldown(user.id):
            return await interaction.response.send_message(
                f"⏳ Poczekaj {BUTTON_COOLDOWN} sekund przed ponownym kliknięciem.",
                ephemeral=True
            )

        async with signup_lock:
            warnings = await get_warning_count(user.id)

            if warnings >= 4:
                return await interaction.response.send_message(
                    "🚫 Masz bana na customy. Skontaktuj się z administracją.",
                    ephemeral=True
                )

            if any(u.id == user.id for u in signups + waiting_list):
                return await interaction.response.send_message(
                    "❗ Już jesteś zapisany.",
                    ephemeral=True
                )

            nicknames = await get_nicknames(user.id)

            if not nicknames:
                success = await self.ask_for_nickname(interaction, user)
                if not success:
                    return

            waiting_list.append(user)
            await log_to_discord(f"✅ {interaction.user.mention} zapisał się bezpośrednio na listę rezerwową.")
            await self.update_message(interaction)

    @discord.ui.button(label="Ustaw czas", style=discord.ButtonStyle.primary)
    async def set_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message(
                "Tylko administrator albo KonikAdmin może ustawić czas.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "Podaj godzinę wydarzenia w formacie `HH:MM`:",
            ephemeral=True
        )

        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=60, check=check)
            hour, minute = map(int, msg.content.strip().split(":"))

            global event_time, reminder_sent

            now = datetime.now()
            event_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

            if event_time < now:
                event_time += timedelta(days=1)

            reminder_sent = False

            await safe_delete_message(msg)
            await self.update_message(interaction)
            await log_to_discord(f"🕒 {interaction.user.mention} ustawił czas wydarzenia na {event_time.strftime('%H:%M')}.")

        except asyncio.TimeoutError:
            await interaction.followup.send("Czas na odpowiedź minął.", ephemeral=True)
        except ValueError:
            await interaction.followup.send("Niepoprawny format godziny.", ephemeral=True)

    @discord.ui.button(label="🗑️ Usuń gracza", style=discord.ButtonStyle.danger, row=1)
    async def remove_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message("Brak uprawnień.", ephemeral=True)

        await interaction.response.send_message(
            "Podaj @użytkownika do usunięcia:",
            ephemeral=True
        )

        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=30, check=check)

            if not msg.mentions:
                await safe_delete_message(msg)
                return await interaction.followup.send(
                    "❌ Nie oznaczono użytkownika.",
                    ephemeral=True
                )

            user = msg.mentions[0]
            removed_from = None

            if any(u.id == user.id for u in signups):
                signups[:] = [u for u in signups if u.id != user.id]
                removed_from = "głównej"

            elif any(u.id == user.id for u in waiting_list):
                waiting_list[:] = [u for u in waiting_list if u.id != user.id]
                removed_from = "rezerwowej"

            await safe_delete_message(msg)

            if removed_from:
                await log_to_discord(f"🗑️ {interaction.user.mention} usunął {user.mention} z listy {removed_from}.")
                await self.update_message(interaction)
            else:
                await interaction.followup.send(
                    "❌ Tego użytkownika nie ma na żadnej liście.",
                    ephemeral=True
                )

        except asyncio.TimeoutError:
            await interaction.followup.send("⏳ Czas minął.", ephemeral=True)

    @discord.ui.button(label="➕ Dodaj gracza", style=discord.ButtonStyle.success, row=1)
    async def add_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message("Brak uprawnień.", ephemeral=True)

        await interaction.response.send_message(
            "Podaj @użytkownika do dodania na listę główną:",
            ephemeral=True
        )

        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=30, check=check)

            if not msg.mentions:
                await safe_delete_message(msg)
                return await interaction.followup.send(
                    "❌ Nie oznaczono użytkownika.",
                    ephemeral=True
                )

            user = msg.mentions[0]
            await safe_delete_message(msg)

            if any(u.id == user.id for u in signups + waiting_list):
                return await interaction.followup.send(
                    "❗ Ten użytkownik już jest zapisany.",
                    ephemeral=True
                )

            nicknames = await get_nicknames(user.id)

            if not nicknames:
                success = await self.ask_for_nickname_admin(interaction.channel, user)
                if not success:
                    return

            if len(signups) < MAX_SIGNUPS:
                signups.append(user)
                await log_to_discord(f"➕ {interaction.user.mention} dodał {user.mention} do listy głównej.")
                await self.update_message(interaction)
            else:
                await interaction.followup.send(
                    "❗ Lista główna jest pełna.",
                    ephemeral=True
                )

        except asyncio.TimeoutError:
            await interaction.followup.send("⏳ Czas minął.", ephemeral=True)

    @discord.ui.button(label="📤 Przenieś z rezerwy", style=discord.ButtonStyle.success, row=1)
    async def move_user(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message("Brak uprawnień.", ephemeral=True)

        if len(signups) >= MAX_SIGNUPS:
            return await interaction.response.send_message(
                "❗ Lista główna jest pełna.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "Podaj @użytkownika do przeniesienia z rezerwy:",
            ephemeral=True
        )

        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=30, check=check)

            if not msg.mentions:
                await safe_delete_message(msg)
                return

            user = msg.mentions[0]
            await safe_delete_message(msg)

            if any(u.id == user.id for u in waiting_list):
                waiting_list[:] = [u for u in waiting_list if u.id != user.id]
                signups.append(user)

                await log_to_discord(f"📤 {interaction.user.mention} przeniósł {user.mention} z rezerwy do listy głównej.")

                await self.update_message(interaction)
            else:
                await interaction.followup.send(
                    "❌ Ten użytkownik nie jest na rezerwie.",
                    ephemeral=True
                )

        except asyncio.TimeoutError:
            await interaction.followup.send("⏳ Czas minął.", ephemeral=True)

    @discord.ui.button(label="🪃 Wyczyść listy", style=discord.ButtonStyle.danger, row=2)
    async def clear_lists(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message("Brak uprawnień.", ephemeral=True)

        signups.clear()
        waiting_list.clear()

        global event_time, reminder_sent
        event_time = None
        reminder_sent = False

        await self.update_message(interaction, log_click=True)
        
        await log_to_discord(f"🪃 {interaction.user.mention} wyczyścił listy.")

    @discord.ui.button(label="📢 Ping lista główna", style=discord.ButtonStyle.primary, row=2)
    async def ping_main(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message("Brak uprawnień.", ephemeral=True)

        if not signups:
            return await interaction.response.send_message(
                "❗ Lista główna jest pusta.",
                ephemeral=True
            )

        mentions = " ".join(user.mention for user in signups)

        await interaction.response.send_message(
            f"📢 Lista główna została pingnięta przez {interaction.user.mention}:\n{mentions}"
        )

        await log_to_discord(f"📢 {interaction.user.mention} pingnął listę główną.")

    @discord.ui.button(label="📢 Ping rezerwa", style=discord.ButtonStyle.secondary, row=2)
    async def ping_reserve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message("Brak uprawnień.", ephemeral=True)

        if not waiting_list:
            return await interaction.response.send_message(
                "❗ Lista rezerwowa jest pusta.",
                ephemeral=True
            )

        target_channel = interaction.guild.get_channel(RESERVE_PING_CHANNEL_ID)

        if not target_channel:
            return await interaction.response.send_message(
                "❌ Nie znaleziono kanału do pingowania rezerwy.",
                ephemeral=True
            )

        mentions = " ".join(user.mention for user in waiting_list)

        await target_channel.send(
            f"📢 Lista rezerwowa została pingnięta przez {interaction.user.mention}:\n{mentions}"
        )

        await interaction.response.send_message(
            "✅ Rezerwa została pingnięta.",
            ephemeral=True
        )

        await log_to_discord(f"📢 {interaction.user.mention} pingnął listę listę rezerwową w <#{RESERVE_PING_CHANNEL_ID}>.")

    @discord.ui.button(label="🎮 Zmień tryb", style=discord.ButtonStyle.primary, row=2)
    async def toggle_ranking(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message("Brak uprawnień.", ephemeral=True)

        global ranking_mode
        ranking_mode = not ranking_mode

        await self.update_message(interaction, log_click=True)

        await log_to_discord(f"🎮 {interaction.user.mention} zmienił tryb gry na {'🏆 Rankingowa' if ranking_mode else '🎮 Nierankingowa'}.")

    @discord.ui.button(label="🔒 Zatrzymaj zapisy", style=discord.ButtonStyle.primary, row=3)
    async def toggle_lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message("Brak uprawnień.", ephemeral=True)

        global signups_locked
        signups_locked = not signups_locked

        button.label = "✅ Wznów zapisy" if signups_locked else "🔒 Zatrzymaj zapisy"

        await self.update_message(interaction)

        await log_to_discord(f"🔒 {interaction.user.mention} {'zatrzymał' if signups_locked else 'wznowił'} zapisy na listę główną.")

    @discord.ui.button(label="🎲 Losuj", style=discord.ButtonStyle.success, row=3)
    async def random_teams(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message("Brak uprawnień.", ephemeral=True)

        if len(signups) < 10:
            return await interaction.response.send_message(
                "❗ Potrzeba minimum 10 graczy.",
                ephemeral=True
            )

        players = signups[:10].copy()
        random.shuffle(players)

        team1 = players[:5]
        team2 = players[5:10]

        msg = "**🔵 Drużyna 1:**\n"
        msg += "\n".join(f"• {p.mention}" for p in team1)

        msg += "\n\n**🔴 Drużyna 2:**\n"
        msg += "\n".join(f"• {p.mention}" for p in team2)

        await interaction.response.send_message(msg)

        await log_to_discord(f"🎲 {interaction.user.mention} wylosował drużyny z listy głównej.")


# ---------- PANEL TEMATYCZNY ---------- #

class TematycznePanel(View):
    def __init__(self, *, message=None, timeout=None):
        super().__init__(timeout=timeout)
        self.message = message

    async def update_message(self):
        if self.message:
            embed = generate_tematyczne_embed()
            await self.message.edit(embed=embed, view=self)

    @discord.ui.button(label="✅ Dołącz", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id

        if uid in tematyczne_main or uid in tematyczne_reserve:
            return await interaction.response.send_message(
                "✅ Już jesteś zapisany.",
                ephemeral=True
            )

        if len(tematyczne_main) < 10:
            tematyczne_main[uid] = interaction.user
            msg = "✅ Zapisano na główną listę!"
        else:
            tematyczne_reserve[uid] = interaction.user
            msg = "ℹ️ Główna lista pełna. Zapisano na listę rezerwową."

        await self.update_message()
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="❌ Wypisz", style=discord.ButtonStyle.danger)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id
        removed = False

        if uid in tematyczne_main:
            del tematyczne_main[uid]
            removed = True

        if uid in tematyczne_reserve:
            del tematyczne_reserve[uid]
            removed = True

        if removed:
            await self.update_message()
            await interaction.response.send_message(
                "👋 Zostałeś wypisany.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "❌ Nie byłeś zapisany.",
                ephemeral=True
            )

    @discord.ui.button(label="📝 Zapisz się na rezerwę", style=discord.ButtonStyle.secondary)
    async def join_reserve(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = interaction.user.id

        if uid in tematyczne_main:
            return await interaction.response.send_message(
                "✅ Już jesteś na głównej liście!",
                ephemeral=True
            )

        if uid in tematyczne_reserve:
            return await interaction.response.send_message(
                "✅ Już jesteś na liście rezerwowej!",
                ephemeral=True
            )

        tematyczne_reserve[uid] = interaction.user

        await self.update_message()

        await interaction.response.send_message(
            "📝 Dodano Cię na listę rezerwową.",
            ephemeral=True
        )

    @discord.ui.button(label="🛠️ Ustaw czas", style=discord.ButtonStyle.primary)
    async def set_time(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message(
                "Tylko administrator albo KonikAdmin może ustawić czas.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "🕒 Podaj godzinę wydarzenia w formacie `HH:MM`:",
            ephemeral=True
        )

        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=60, check=check)
            hour, minute = map(int, msg.content.strip().split(":"))

            global tematyczne_event_time, tematyczne_reminder_sent

            now = datetime.now()
            tematyczne_event_time = now.replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0
            )

            if tematyczne_event_time < now:
                tematyczne_event_time += timedelta(days=1)

            tematyczne_reminder_sent = False

            await safe_delete_message(msg)
            await self.update_message()

            await interaction.followup.send(
                f"✅ Czas ustawiony na {tematyczne_event_time.strftime('%H:%M')}",
                ephemeral=True
            )

        except Exception:
            await interaction.followup.send(
                "❌ Błąd formatu. Spróbuj `HH:MM`.",
                ephemeral=True
            )

    @discord.ui.button(label="📥 Promuj z rezerwy", style=discord.ButtonStyle.secondary)
    async def promote(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message(
                "Tylko administrator albo KonikAdmin może przenosić z rezerwy.",
                ephemeral=True
            )

        if len(tematyczne_main) >= 10:
            return await interaction.response.send_message(
                "❌ Główna lista już pełna.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "🔎 Wpisz @użytkownika do przeniesienia z rezerwy:",
            ephemeral=True
        )

        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", timeout=30, check=check)
            mentioned = msg.mentions[0] if msg.mentions else None
            await safe_delete_message(msg)

            if not mentioned or mentioned.id not in tematyczne_reserve:
                return await interaction.followup.send(
                    "❌ Użytkownik nie jest na liście rezerwowej.",
                    ephemeral=True
                )

            del tematyczne_reserve[mentioned.id]
            tematyczne_main[mentioned.id] = mentioned

            await self.update_message()

            await interaction.followup.send(
                f"📤 Przeniesiono {mentioned.mention} do głównej listy.",
                ephemeral=True
            )

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏰ Czas minął. Nie wybrano gracza.",
                ephemeral=True
            )

    @discord.ui.button(label="📢 Pinguj graczy", style=discord.ButtonStyle.secondary)
    async def ping(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message(
                "Tylko administrator albo KonikAdmin może pingować.",
                ephemeral=True
            )

        if not tematyczne_main:
            return await interaction.response.send_message(
                "❌ Brak zapisanych graczy.",
                ephemeral=True
            )

        mentions = " ".join(user.mention for user in tematyczne_main.values())

        await interaction.response.send_message(
            f"📢 Ping: {mentions}",
            delete_after=300
        )

    @discord.ui.button(label="✏️ Zmień nazwę serii", style=discord.ButtonStyle.primary)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message(
                "Tylko administrator albo KonikAdmin może zmienić nazwy.",
                ephemeral=True
            )

        await interaction.response.send_message(
            "✏️ Podaj nową nazwę serii 1:",
            ephemeral=True
        )

        def check(msg):
            return msg.author == interaction.user and msg.channel == interaction.channel

        try:
            global seria1_nazwa, seria2_nazwa

            msg1 = await bot.wait_for("message", timeout=30, check=check)
            seria1_nazwa = msg1.content.strip()
            await safe_delete_message(msg1)

            await interaction.followup.send(
                "✏️ Podaj nową nazwę serii 2:",
                ephemeral=True
            )

            msg2 = await bot.wait_for("message", timeout=30, check=check)
            seria2_nazwa = msg2.content.strip()
            await safe_delete_message(msg2)

            await self.update_message()

            await interaction.followup.send(
                f"✅ Ustawiono: **{seria1_nazwa}** vs **{seria2_nazwa}**",
                ephemeral=True
            )

        except asyncio.TimeoutError:
            await interaction.followup.send(
                "⏰ Czas minął. Nie zmieniono.",
                ephemeral=True
            )

    @discord.ui.button(label="🧹 Wyczyść panel", style=discord.ButtonStyle.danger)
    async def clear_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message(
                "Tylko administrator albo KonikAdmin może czyścić panel.",
                ephemeral=True
            )

        tematyczne_main.clear()
        tematyczne_reserve.clear()

        global tematyczne_event_time, tematyczne_reminder_sent

        tematyczne_event_time = None
        tematyczne_reminder_sent = False

        await self.update_message()

        await interaction.response.send_message(
            "🧹 Panel został wyczyszczony.",
            ephemeral=True
        )

    @discord.ui.button(label="🎲 Losuj drużyny", style=discord.ButtonStyle.success)
    async def roll_teams(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not has_panel_access(interaction.user):
            return await interaction.response.send_message(
                "Tylko administrator albo KonikAdmin może losować drużyny.",
                ephemeral=True
            )

        players = list(tematyczne_main.values())

        if len(players) < 10:
            return await interaction.response.send_message(
                "❌ Potrzeba co najmniej 10 graczy do losowania.",
                ephemeral=True
            )

        random.shuffle(players)

        team1 = players[:5]
        team2 = players[5:10]

        def team_str(team):
            return "\n".join(f"• {user.mention}" for user in team)

        embed = discord.Embed(
            title=f"🎮 {seria1_nazwa} vs {seria2_nazwa}",
            color=discord.Color.orange()
        )

        embed.add_field(
            name=f"🔵 Drużyna 1 — {seria1_nazwa}",
            value=team_str(team1),
            inline=True
        )

        embed.add_field(
            name=f"🔴 Drużyna 2 — {seria2_nazwa}",
            value=team_str(team2),
            inline=True
        )

        await interaction.response.send_message(embed=embed)

        await log_to_discord(
            f"🎲 {interaction.user.mention} wylosował drużyny tematyczne."
        )


# ---------- EVENTY ---------- #

@bot.event
async def on_ready():
    wczytaj_dane()

    await connect_to_db()

    if db_pool:
        await create_tables()

    print(f"✅ Zalogowano jako {bot.user.name}")

    if not check_event_time.is_running():
        check_event_time.start()


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if db_pool:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO stats (user_id, messages, mentions)
                VALUES ($1, 1, $2)
                ON CONFLICT (user_id)
                DO UPDATE SET
                    messages = stats.messages + 1,
                    mentions = stats.mentions + $2
            """, message.author.id, len(message.mentions))

    await bot.process_commands(message)


# ---------- TASKI ---------- #

@tasks.loop(seconds=60)
async def check_event_time():
    global reminder_sent, tematyczne_reminder_sent

    now = datetime.now()

    if panel_channel is None:
        return

    if event_time and not reminder_sent:
        diff = event_time - now

        if timedelta(minutes=14) < diff <= timedelta(minutes=15):
            reminder_sent = True

            if signups:
                mentions = " ".join(user.mention for user in signups)
                await panel_channel.send(
                    f"⏰ **Przypomnienie!** Customy za 15 minut!\n{mentions}",
                    delete_after=2400
                )
            else:
                await panel_channel.send(
                    "⏰ Customy za 15 minut, ale lista główna jest pusta.",
                    delete_after=2400
                )

    if tematyczne_event_time and not tematyczne_reminder_sent:
        diff = tematyczne_event_time - now

        if timedelta(minutes=14) < diff <= timedelta(minutes=15):
            tematyczne_reminder_sent = True

            if tematyczne_main:
                mentions = " ".join(user.mention for user in tematyczne_main.values())
                await panel_channel.send(
                    f"⏰ **Tematyczne przypomnienie!** Start za 15 minut!\n{mentions}",
                    delete_after=1200
                )
            else:
                await panel_channel.send(
                    "⏰ Tematyczne: Brak zapisanych graczy.",
                    delete_after=1200
                )


# ---------- KOMENDY INFO ---------- #

@bot.command(name="info")
async def info(ctx):
    embed = discord.Embed(
        title="ℹ️ Informacje o bocie",
        description="Poniżej znajdziesz listę dostępnych komend oraz przycisków bota.",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="🎮 Komendy ogólne",
        value=(
            "`!info` – pokazuje tę wiadomość\n"
            "`!opis` – opis bota\n"
            "`!ksante` – easter egg"
        ),
        inline=False
    )

    embed.add_field(
        name="📋 Panel główny",
        value=(
            "`!panel` – panel zapisów\n"
            "`!lista` – pokazuje listę\n"
            "`!testplayer` – dodaje 10 testowych graczy\n"
            "`!bancustom @osoba` – dodaje ostrzeżenie\n"
            "`!usunbana @osoba` – usuwa ostrzeżenia"
        ),
        inline=False
    )

    embed.add_field(
        name="🎨 Panel tematyczny",
        value=(
            "`!tematyczne` – panel tematyczny\n"
            "`!tematyczne_test` – dodaje 10 testowych graczy\n"
            "Przycisk **Losuj drużyny** losuje 2 drużyny po 5 osób."
        ),
        inline=False
    )

    await ctx.send(embed=embed)


@bot.command(name="opis")
async def opis(ctx):
    embed = discord.Embed(
        title="🤖 KonikBOT – Wersja 6.0",
        description=(
            "KonikBOT stworzony do organizowania gier customowych w League of Legends.\n\n"
            "Umożliwia tworzenie zapisów, organizowanie gier tematycznych i zarządzanie graczami."
        ),
        color=discord.Color.green()
    )

    embed.set_footer(
        text="Developed by BarowatyPL"
    )

    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def regulamin(ctx):
    try:
        await ctx.message.delete(delay=5)
    except Exception:
        pass

    regulamin_text = (
        "**📜 Regulamin Customów LoL**\n\n"
        "⏰ **Punktualność**\n"
        "Gracz, który nie pojawi się na czas i nie poinformuje o nieobecności przynajmniej 10 minut przed startem, łamie zasady.\n\n"
        "🚫 **Zapraszanie osób trzecich**\n"
        "Nie wolno zapraszać osób spoza ustalonego składu bez wiedzy organizatora.\n\n"
        "🧠 **Zapomniany Smite? Gramy dalej**\n"
        "Nie przerywamy gry z powodu pomyłek takich jak brak smite’a.\n\n"
        "❌ **Pomyłki w pickach**\n"
        "Jeśli ktoś wybierze niewłaściwą postać, gra jest kontynuowana.\n\n"
        "🔁 **Kończysz grę = wypisz się**\n"
        "Gracz kończący udział ma obowiązek wypisać się z listy.\n\n"
        "⏳ **Czekanie na osobę z ławki**\n"
        "Na osobę z ławki czekamy maksymalnie 5 minut.\n\n"
        "*W przypadku niejasności decyzję podejmują administratorzy.*"
    )

    await ctx.send(regulamin_text, delete_after=1200)


# ---------- KOMENDY PANELU GŁÓWNEGO ---------- #

@bot.command()
@commands.check_any(
    commands.has_permissions(administrator=True),
    is_bot_admin()
)
async def panel(ctx):
    global panel_channel, panel_message

    panel_channel = ctx.channel

    embed = await generate_main_embed()
    view = SignupPanel(message=None)
    panel_message = await ctx.send(embed=embed, view=view)
    view.message = panel_message


@bot.command(name="lista")
@commands.has_permissions(administrator=True)
async def lista(ctx):
    embed = await generate_main_embed()
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def testplayer(ctx):
    signups.clear()
    waiting_list.clear()

    for i in range(1, 11):
        signups.append(FakeUser(f"TestGracz{i}"))

    await odswiez_panel()
    await ctx.send("✅ Dodano 10 testowych graczy do listy głównej.")


async def odswiez_panel():
    global panel_message

    if panel_message:
        try:
            embed = await generate_main_embed()
            view = SignupPanel(message=panel_message)
            await panel_message.edit(embed=embed, view=view)
        except Exception as e:
            print(f"❌ Błąd przy odświeżaniu panelu: {e}")


# ---------- KOMENDY PANELU TEMATYCZNEGO ---------- #

@bot.command()
async def tematyczne(ctx):
    global panel_channel, tematyczne_panel_message

    panel_channel = ctx.channel

    embed = generate_tematyczne_embed()
    view = TematycznePanel(message=None)
    msg = await ctx.send(embed=embed, view=view)

    view.message = msg
    tematyczne_panel_message = msg


@bot.command(name="tematyczne_test")
@commands.has_permissions(administrator=True)
async def tematyczne_test(ctx):
    tematyczne_main.clear()
    tematyczne_reserve.clear()

    base_id = 900000000000000000

    for i in range(1, 11):
        mock_user = SimpleNamespace(
            id=base_id + i,
            mention=f"TestGracz{i}",
            name=f"TestGracz{i}",
            display_name=f"TestGracz{i}"
        )
        tematyczne_main[mock_user.id] = mock_user

    if tematyczne_panel_message:
        embed = generate_tematyczne_embed()
        view = TematycznePanel(message=tematyczne_panel_message)
        await tematyczne_panel_message.edit(embed=embed, view=view)

    await ctx.send("✅ Dodano 10 testowych graczy do panelu tematycznego.", delete_after=10)

# ---------- KOMENDY RANKINGOWE ---------- #

@bot.command(name="profil")
async def profil_cmd(ctx, member: discord.Member = None):
    member = member or ctx.author

    gracz = await pobierz_gracza(str(member))
    reputacja_pkt = await pobierz_reputacje(member.id)

    if not gracz:
        return await ctx.send(
            f"❌ {member.mention} nie ma jeszcze profilu w rankingu."
        )

    embed = discord.Embed(
        title=f"📊 Profil gracza {member.display_name}",
        color=discord.Color.blue()
    )

    embed.add_field(name="ELO", value=gracz["elo"], inline=True)
    embed.add_field(name="Zagrane", value=gracz["zagrane"], inline=True)
    embed.add_field(name="Wygrane", value=gracz["wygrane"], inline=True)
    embed.add_field(name="Przegrane", value=gracz["przegrane"], inline=True)
    embed.add_field(name="MVP", value=gracz["mvp"], inline=True)
    embed.add_field(name="👍 Reputacja", value=reputacja_pkt, inline=True)

    await ctx.send(embed=embed)


@bot.command(name="ranking")
async def ranking_cmd(ctx, top: int = 10):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT *
            FROM gracze
            ORDER BY elo DESC
            LIMIT $1
            """,
            top
        )

    if not rows:
        return await ctx.send("❌ Brak graczy w rankingu.")

    description = ""

    for i, row in enumerate(rows, start=1):
        description += f"**{i}.** {row['nick']} — {row['elo']} ELO\n"

    embed = discord.Embed(
        title=f"🏆 Top {top} Graczy",
        description=description,
        color=discord.Color.gold()
    )

    await ctx.send(embed=embed)


# ---------- KOMENDY REPUTACJI ---------- #

@bot.command(name="rep")
async def rep(ctx, member: discord.Member, wartosc: int = 1):
    if member.id == ctx.author.id:
        return await ctx.send(
            "❌ Nie możesz zmieniać reputacji samemu sobie.",
            delete_after=10
        )

    if wartosc not in (-1, 1):
        return await ctx.send(
            "⚠️ Możesz tylko dodać lub odjąć 1 punkt (`1` lub `-1`).",
            delete_after=10
        )

    is_admin = ctx.author.guild_permissions.administrator
    klucz = (ctx.author.id, member.id)
    teraz = datetime.utcnow()

    if not is_admin:
        ostatnio = rep_cooldown.get(klucz)

        if ostatnio and (teraz - ostatnio).total_seconds() < 86400:
            return await ctx.send(
                "⏳ Możesz zmienić reputację tej osobie tylko raz na 24 godziny.",
                delete_after=10
            )

        rep_cooldown[klucz] = teraz

    await dodaj_reputacje(member.id, wartosc)
    await log_reputacja(ctx.author, member, wartosc)

    aktualna = await pobierz_reputacje(member.id)

    emoji = "👍" if wartosc > 0 else "👎"

    await ctx.send(
        f"{emoji} {ctx.author.mention} "
        f"{'dodał' if wartosc > 0 else 'odjął'} reputację "
        f"{member.mention} ({wartosc:+} pkt, razem: **{aktualna}**)"
    )


@bot.command(name="reputacja")
async def reputacja(ctx, member: discord.Member = None):
    member = member or ctx.author
    punkty = await pobierz_reputacje(member.id)

    await ctx.send(
        f"📊 {member.mention} ma **{punkty}** punktów reputacji."
    )


@bot.command(name="toprep")
async def toprep(ctx, limit: int = 10):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT user_id, punkty
            FROM reputacja
            ORDER BY punkty DESC
            LIMIT $1
        """, limit)

    if not rows:
        return await ctx.send("🔎 Brak danych o reputacji.")

    opis = ""

    for i, row in enumerate(rows, start=1):
        member = ctx.guild.get_member(row["user_id"])
        nick = member.display_name if member else f"<ID: {row['user_id']}>"
        opis += f"**{i}.** {nick} – {row['punkty']} pkt\n"

    embed = discord.Embed(
        title=f"🏅 Top {limit} reputacji",
        description=opis,
        color=discord.Color.purple()
    )

    await ctx.send(embed=embed)


# ---------- BANY CUSTOMÓW ---------- #

@bot.command(name="bancustom")
@commands.has_permissions(administrator=True)
async def bancustom(ctx, member: discord.Member):
    try:
        await ctx.message.delete(delay=5)
    except Exception:
        pass

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT liczba FROM ostrzezenia WHERE user_id = $1",
            member.id
        )

        liczba = row["liczba"] if row else 0
        liczba += 1

        await conn.execute("""
            INSERT INTO ostrzezenia (user_id, liczba)
            VALUES ($1, $2)
            ON CONFLICT (user_id)
            DO UPDATE SET liczba = $2
        """, member.id, liczba)

    status = "ban" if liczba >= 4 else f"{liczba}/3"

    await log_to_discord(
        f"🚫 {ctx.author.mention} dał `bancustom` dla {member.mention} – teraz ma: **{status}**"
    )

    await odswiez_panel()


@bot.command(name="usunbana")
@commands.has_permissions(administrator=True)
async def usunbana(ctx, member: discord.Member):
    try:
        await ctx.message.delete(delay=5)
    except Exception:
        pass

    async with db_pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM ostrzezenia WHERE user_id = $1",
            member.id
        )

    await log_to_discord(
        f"✅ {ctx.author.mention} usunął ostrzeżenia dla {member.mention}"
    )

    await odswiez_panel()


# ---------- LOGI ---------- #

@bot.command(name="logi")
@commands.has_permissions(administrator=True)
async def logi(ctx, liczba: int = 10):
    channel = bot.get_channel(LOG_CHANNEL_ID)

    if not channel:
        return await ctx.send("❌ Nie mogę znaleźć kanału logów.")

    messages = [msg async for msg in channel.history(limit=liczba)]
    messages.reverse()

    formatted = "\n".join(msg.content for msg in messages)

    if not formatted:
        formatted = "Brak logów do wyświetlenia."

    await ctx.send(
        f"📄 **Ostatnie {liczba} logów:**\n```{formatted}```"
    )


# ---------- KOMENDY DLA BEKI ---------- #

@bot.command(name="ksante")
async def ksante(ctx):
    tekst = (
        "K'Sante👤 4,700 HP 💪 329 Armor 🤷‍♂️ 201 MR 💦 Unstoppable 🚫 "
        "A Shield 🛡 Goes over walls 🧱 Has Airborne 🌪 "
        "Cooldown is only ☝ second too 🕐 It costs 15 Mana 🧙‍♂️"
    )

    await ctx.send(tekst, delete_after=300)


@bot.command(name="najlepszy")
async def najlepszy(ctx):
    await ctx.send(
        "Jestem Kurwa świetny, jestem najlepszy, jestem Bogiem tej gry!!!",
        delete_after=300
    )


@bot.command(name="lulu")
async def lulu(ctx):
    await ctx.send(
        "JEBANA DZIWKA Z KAPELUSZEM!!!",
        delete_after=300
    )


@bot.command(name="daj")
async def daj(ctx):
    await ctx.send(
        "DAJCIE MI GO!!!",
        delete_after=300
    )


# ---------- START ---------- #

if __name__ == "__main__":
    keep_alive()

    if not TOKEN:
        print("❌ Brak DISCORD_TOKEN w .env")
    else:
        bot.run(TOKEN)
