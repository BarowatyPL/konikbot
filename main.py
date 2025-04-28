import discord
from discord.ext import commands, tasks
from datetime import datetime, time, timedelta
import asyncio
import os
import json
import random
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

intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # <- DODAJ TO TUTAJ

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
bot.panel_message = None

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

wczytaj_dane()

@bot.event
async def on_ready():
    print(f'Zalogowano jako {bot.user.name}')
    check_event_time.start()

@tasks.loop(seconds=60)
async def check_event_time():
    global event_time
    if not event_time:
        print("[DEBUG] Brak ustawionego event_time.")
        return

    now = datetime.now()
    event_today = datetime.combine(now.date(), event_time)
    delta = (event_today - now).total_seconds()
    print("[DEBUG] delta:", delta)

    channel = bot.get_channel(1216013668773265458)
    if not channel:
        print("[DEBUG] Nie znaleziono kanału przypomnienia.")
        return

    if 0 < delta <= 900:
        mentions = [member.mention for member in channel.guild.members if member.id in signup_ids]
        if mentions:
            await channel.send("⏳ Wydarzenie za 15 minut! Obecni:\n" + " ".join(mentions))
        else:
            await channel.send("⚠️ Nie udało się pingować graczy.")
    elif -60 <= delta <= 0:
        await channel.send("📢 Wydarzenie rozpoczyna się teraz!")




###################### KOMENDY ############################

#poprawić
@bot.command()
async def help(ctx):
    embed = discord.Embed(title="📖 Lista dostępnych komend", color=discord.Color.blue())
    embed.add_field(name="!zapisz", value="Zapisuje Cię na wydarzenie", inline=False)
    embed.add_field(name="!wypisz", value="Wypisuje Cię z wydarzenia", inline=False)
    embed.add_field(name="!lista", value="Wyświetla listę zapisanych", inline=False)
    embed.add_field(name="!dodaj <nick>", value="(admin) Dodaje gracza ręcznie", inline=False)
    embed.add_field(name="!usun <nick>", value="(admin) Usuwa gracza ręcznie", inline=False)
    embed.add_field(name="!start", value="(admin) Losuje drużyny i pokazuje ELO info", inline=False)
    embed.add_field(name="!wynik <1/2>", value="(admin) Rozpoczyna głosowanie MVP", inline=False)
    embed.add_field(name="!mvp", value="(admin) Zatwierdza MVP i rozdaje punkty", inline=False)
    embed.add_field(name="!wyczysc", value="(admin) Czysci całą listę graczy", inline=False)
    embed.add_field(name="!profil [nick]", value="Pokazuje Twój profil", inline=False)
    embed.add_field(name="!ranking", value="Top 10 graczy ELO", inline=False)
    await ctx.send(embed=embed)
#poprawić
@bot.command()
async def info(ctx):
    embed = discord.Embed(title="ℹ️ Jak działa system bota", color=discord.Color.gold())
    embed.add_field(
        name="1. Zapisy",
        value="Użytkownicy zapisują się komendą `!zapisz`, admin może też dodać gracza ręcznie `!dodaj <nick>`.",
        inline=False
    )
    embed.add_field(
        name="2. Start gry",
        value="Admin używa komendy `!start`, która losuje drużyny i pokazuje ELO oraz prognozowane zyski/straty.",
        inline=False
    )
    embed.add_field(
        name="3. Zgłoszenie wyniku",
        value="Po grze admin używa `!wynik 1` lub `!wynik 2` w zależności od zwycięzcy.",
        inline=False
    )
    embed.add_field(
        name="4. Głosowanie MVP",
        value="Po wyniku gracze mogą głosować na MVP w drużynach poprzez reakcje. Tylko uczestnicy meczu mogą głosować.",
        inline=False
    )
    embed.add_field(
        name="5. Zatwierdzenie MVP",
        value="Admin zatwierdza MVP komendą `!mvp`. MVP dostają dodatkowe punkty lub tracą mniej.",
        inline=False
    )
    embed.add_field(
        name="6. Statystyki",
        value="Każdy może użyć `!profil` by zobaczyć swoje ELO, zwycięstwa, przegrane i MVP. `!ranking` pokazuje top graczy.",
        inline=False
    )
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def czas(ctx, godzina: str = None):
    global event_time
    if not godzina:
        await ctx.send("📌 Podaj godzinę w formacie HH:MM, np. `!czas 19:45`.")
        return
    try:
        godz, minuty = map(int, godzina.split(":"))
        event_time = time(hour=godz, minute=minuty)
        await ctx.send(f"⏰ Ustawiono czas rozpoczęcia na **{event_time.strftime('%H:%M')}**.")
    except:
        await ctx.send("❌ Nieprawidłowy format godziny. Użyj formatu HH:MM.")


@bot.command()
async def wolam(ctx):
    print("[DEBUG] signup_ids:", signup_ids)
    if not signup_ids:
        await ctx.send("Brak zapisanych graczy.")
        return
    mentions = [member.mention for member in ctx.guild.members if member.id in signup_ids]
    print("[DEBUG] ctx.guild.members:", [m.display_name for m in ctx.guild.members])
    if mentions:
        await ctx.send("📢 Zapraszam na grę:\n" + " ".join(mentions))
    else:
        await ctx.send("⚠️ Nie udało się dopasować żadnych graczy z listy.")


@bot.command()
async def zapisz(ctx):
    user = ctx.author.display_name
    user_id = ctx.author.id

    if user in signups or user in waiting_list:
        await ctx.send(f'{user}, jesteś już zapisany.')
        return

    if len(signups) < MAX_SIGNUPS:
        signups.append(user)
        if user_id not in signup_ids:
            signup_ids.append(user_id)
        log_entry(user, 'Zapisano')
        print("[DEBUG] signup_ids:", signup_ids)
        await ctx.send(f'{user} został zapisany. ({len(signups)}/{MAX_SIGNUPS})')
    else:
        waiting_list.append(user)
        log_entry(user, 'Lista rezerwowa')
        await ctx.send(f'{user}, dodano do listy rezerwowej.')

    # 🔁 Odśwież panel jeśli istnieje
    aktualizuj_listy()
    if bot.panel_message:
        panel_ctx = await bot.get_context(ctx.message)
        panel_ctx.author = ctx.author
        await bot.panel_message.edit(
            embed=generuj_embed_panel("📋 Lista graczy (Panel)"),
            view=PanelView(panel_ctx)
        )


@bot.command()
async def wypisz(ctx):
    user = ctx.author.display_name
    if user in signups:
        signups.remove(user)
        log_entry(user, 'Wypisano')
        aktualizuj_listy()
        await ctx.send(f'{user} został wypisany.')
    elif user in waiting_list:
        waiting_list.remove(user)
        log_entry(user, 'Usunięto z rezerwowej')
        aktualizuj_listy()
        await ctx.send(f'{user} usunięty z listy rezerwowej.')
    else:
        await ctx.send(f'{user}, nie jesteś zapisany.')

    if bot.panel_message:
        panel_ctx = await bot.get_context(ctx.message)
        panel_ctx.author = ctx.author
        await bot.panel_message.edit(
            embed=generuj_embed_panel("📋 Lista graczy (Panel)"),
            view=PanelView(panel_ctx)
        )

@bot.command()
@commands.has_permissions(administrator=True)
async def dodaj(ctx, *, user):
    if user in signups or user in waiting_list:
        await ctx.send(f'{user} już jest zapisany.')
    else:
        waiting_list.append(user)
        log_entry(user, 'Dodany ręcznie')
        aktualizuj_listy()
        await ctx.send(f'✅ Dodano {user} do zapisów.')

        if bot.panel_message:
            panel_ctx = await bot.get_context(ctx.message)
            panel_ctx.author = ctx.author
            await bot.panel_message.edit(
                embed=generuj_embed_panel("📋 Lista graczy (Panel)"),
                view=PanelView(panel_ctx)
            )


@bot.command()
@commands.has_permissions(administrator=True)
async def wyczysc(ctx):
    global signups, waiting_list, signup_ids
    signups = []
    waiting_list = []
    signup_ids = []
    log_entry(str(ctx.author), 'Wyczyszczono listy zapisów')
    await ctx.send("🧹 Lista zapisów i rezerwowa została całkowicie wyczyszczona.")

    if bot.panel_message:
        panel_ctx = await bot.get_context(ctx.message)
        panel_ctx.author = ctx.author
        await bot.panel_message.edit(
            embed=generuj_embed_panel("📋 Lista graczy (Panel)"),
            view=PanelView(panel_ctx)
        )


@bot.command()
@commands.has_permissions(administrator=True)
async def usun(ctx, *, user):
    if user in signups:
        signups.remove(user)
        log_entry(user, 'Usunięty ręcznie')
        aktualizuj_listy()
        await ctx.send(f'🗑️ Usunięto {user} z zapisów.')
    elif user in waiting_list:
        waiting_list.remove(user)
        log_entry(user, 'Usunięty z rezerwowej ręcznie')
        aktualizuj_listy()
        await ctx.send(f'🗑️ Usunięto {user} z listy rezerwowej.')
    else:
        await ctx.send(f'{user} nie znajduje się na liście.')

    if bot.panel_message:
        panel_ctx = await bot.get_context(ctx.message)
        panel_ctx.author = ctx.author
        await bot.panel_message.edit(
            embed=generuj_embed_panel("📋 Lista graczy (Panel)"),
            view=PanelView(panel_ctx)
        )



@bot.command()
async def lista(ctx):
    embed = generuj_embed_panel("📋 Lista graczy")
    await ctx.send(embed=embed)


@bot.command()
async def panel(ctx):
    print("[DEBUG] Wywołano !panel")
    view = PanelView(ctx)
    embed = generuj_embed_panel("📋 Lista graczy (Panel)")

    if bot.panel_message:
        try:
            await bot.panel_message.edit(embed=embed, view=view)
            return
        except discord.NotFound:
            bot.panel_message = None

    bot.panel_message = await ctx.send(embed=embed, view=view)




def generuj_embed_panel(tytul="📋 Lista graczy"):
    zapisani_display = signups[:MAX_SIGNUPS]
    rezerwowi_display = signups[MAX_SIGNUPS:] + waiting_list

    embed = discord.Embed(title=tytul, color=discord.Color.green())
    czas_info = event_time.strftime('%H:%M') if event_time else "Nieokreślono"
    embed.set_footer(text=f"Czas rozpoczęcia: {czas_info}")

    opis = ""

    if zapisani_display:
        for i, nick in enumerate(zapisani_display, start=1):
            opis += f"{i}. {nick}\n"
    else:
        opis += "Brak zapisanych graczy\n"

    if rezerwowi_display:
        opis += "\n=== Rezerwowi ===\n"
        for i, nick in enumerate(rezerwowi_display, start=1):
            opis += f"{i}. {nick}\n"

    embed.add_field(name="✅ Gracze zapisani", value=opis.strip(), inline=False)
    return embed





class UsunButton(discord.ui.Button):
    def __init__(self, nick):
        super().__init__(label=f"Usuń {nick}", style=discord.ButtonStyle.danger)
        self.nick = nick

    async def callback(self, interaction: discord.Interaction):
        if self.nick in signups:
            signups.remove(self.nick)
        elif self.nick in waiting_list:
            waiting_list.remove(self.nick)

        log_entry(self.nick, "Usunięty przez przycisk")
        aktualizuj_listy()
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        if bot.panel_message:
            await bot.panel_message.edit(embed=generuj_embed_panel("📋 Lista graczy (Panel)"), view=PanelView(ctx))
        await interaction.response.send_message(f"🗑️ Usunięto {self.nick} z listy!", ephemeral=False, delete_after=10)





class ZapiszButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✅ Zapisz się", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        nick = interaction.user.display_name
        user_id = interaction.user.id

        if nick in signups or nick in waiting_list:
            await interaction.response.send_message("Już jesteś zapisany.", ephemeral=False, delete_after=10)
            return

        if len(signups) < MAX_SIGNUPS:
            signups.append(nick)
            if user_id not in signup_ids:
                signup_ids.append(user_id)
            log_entry(nick, "Zapisano przez przycisk")
        else:
            waiting_list.append(nick)
            log_entry(nick, "Dodano do rezerwowej przez przycisk")

        aktualizuj_listy()
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        if bot.panel_message:
            await bot.panel_message.edit(embed=generuj_embed_panel("📋 Lista graczy (Panel)"), view=PanelView(ctx))




class WypiszButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="❌ Wypisz się", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        nick = interaction.user.display_name
        removed = False

        if nick in signups:
            signups.remove(nick)
            removed = True
        elif nick in waiting_list:
            waiting_list.remove(nick)
            removed = True

        if removed:
            log_entry(nick, "Wypisano przez przycisk")
            aktualizuj_listy()
            ctx = await bot.get_context(interaction.message)
            ctx.author = interaction.user
            if bot.panel_message:
                await bot.panel_message.edit(embed=generuj_embed_panel("📋 Lista graczy (Panel)"), view=PanelView(ctx))
        else:
            await interaction.response.send_message("Nie jesteś zapisany.", ephemeral=False, delete_after=10)




class RezerwowyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🕒 Do rezerwowej", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        nick = interaction.user.display_name

        if nick in signups or nick in waiting_list:
            await interaction.response.send_message("Już jesteś na liście.", ephemeral=False, delete_after=10)
            return

        waiting_list.append(nick)
        log_entry(nick, "Dodano do rezerwowej przez przycisk")
        aktualizuj_listy()
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        await interaction.response.edit_message(embed=generuj_embed_panel(), view=PanelView(ctx))



class PrzeniesDoRezerwowejButton(discord.ui.Button):
    def __init__(self, nick):
        super().__init__(label=f"🔽 {nick}", style=discord.ButtonStyle.secondary)
        self.nick = nick

    async def callback(self, interaction: discord.Interaction):
        if self.nick in signups:
            signups.remove(self.nick)
            waiting_list.append(self.nick)

        log_entry(self.nick, "Przeniesiono do rezerwowej")
        aktualizuj_listy()
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        if bot.panel_message:
            await bot.panel_message.edit(embed=generuj_embed_panel("📋 Lista graczy (Panel)"), view=PanelView(ctx))




class PrzeniesDoGlownejButton(discord.ui.Button):
    def __init__(self, nick):
        super().__init__(label=f"🔼 {nick}", style=discord.ButtonStyle.primary)
        self.nick = nick

    async def callback(self, interaction: discord.Interaction):
        if self.nick in waiting_list and len(signups) < MAX_SIGNUPS:
            waiting_list.remove(self.nick)
            signups.append(self.nick)

        log_entry(self.nick, "Przeniesiono do głównej")
        aktualizuj_listy()
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        if bot.panel_message:
            await bot.panel_message.edit(embed=generuj_embed_panel("📋 Lista graczy (Panel)"), view=PanelView(ctx))



class ZmienGodzineButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="⏰ Zmień godzinę", style=discord.ButtonStyle.blurple)

    async def callback(self, interaction: discord.Interaction):
        msg_prompt = await interaction.response.send_message("Podaj nową godzinę w formacie HH:MM", ephemeral=False, delete_after=10)

        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel

        try:
            msg = await bot.wait_for("message", check=check, timeout=30)
            godzina = msg.content.strip()
            godz, minuty = map(int, godzina.split(":"))
            global event_time
            event_time = time(hour=godz, minute=minuty)
            await msg.delete(delay=10)
            await interaction.followup.send(f"✅ Ustawiono nową godzinę: **{event_time.strftime('%H:%M')}**", delete_after=10)

            aktualizuj_listy()
            ctx = await bot.get_context(interaction.message)
            ctx.author = interaction.user
            if bot.panel_message:
                await bot.panel_message.edit(embed=generuj_embed_panel("📋 Lista graczy (Panel)"), view=PanelView(ctx))

        except Exception:
            await interaction.followup.send("❌ Nie udało się ustawić godziny. Format HH:MM.", delete_after=10)


class PanelView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.add_item(ZapiszButton())
        self.add_item(WypiszButton())
        self.add_item(RezerwowyButton())
        self.add_item(ZmienGodzineButton())
        for nick in signups[:MAX_SIGNUPS] + waiting_list:
            self.add_item(UsunButton(nick))
            self.add_item(PrzeniesDoRezerwowejButton(nick))
            self.add_item(PrzeniesDoGlownejButton(nick))




########## KOMENDY DO SYSTEMY RANKINGOWEGO (JESZCZE NIE DZIAŁA) ##############################

@bot.command()
@commands.has_permissions(administrator=True)
async def start(ctx):
    global team1, team2
    if len(signups) < 10:
        await ctx.send("❌ Potrzeba dokładnie 10 zapisanych graczy.")
        return

    random.shuffle(signups)
    team1 = signups[:5]
    team2 = signups[5:10]
    bot.last_teams = {"A": team1, "B": team2}

    suma_a = sum(PUNKTY_ELO.get(g, 1000) for g in team1)
    suma_b = sum(PUNKTY_ELO.get(g, 1000) for g in team2)
    szansa_a = przewidywana_szansa(suma_a, suma_b)
    szansa_b = 1 - szansa_a

    gain_a = max(15, round(32 * (1 - szansa_a)))
    loss_a = max(15, round(32 * (0 - szansa_a)))
    gain_b = max(15, round(32 * (1 - szansa_b)))
    loss_b = max(15, round(32 * (0 - szansa_b)))

    embed = discord.Embed(title="🎮 Drużyny wylosowane!", color=discord.Color.purple())
    embed.add_field(name="Drużyna 1", value="\n".join(f"{i+1}. {g} (±{gain_a}/-{loss_a})" for i, g in enumerate(team1)), inline=True)
    embed.add_field(name="Drużyna 2", value="\n".join(f"{i+6}. {g} (±{gain_b}/-{loss_b})" for i, g in enumerate(team2)), inline=True)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def wynik(ctx, zwyciezca: int):
    if zwyciezca not in (1, 2):
        await ctx.send("❌ Użyj `!wynik 1` lub `!wynik 2`.")
        return

    bot.zwyciezca = zwyciezca
    wygrani = team1 if zwyciezca == 1 else team2
    przegrani = team2 if zwyciezca == 1 else team1
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]

    embed_win = discord.Embed(title="🏆 Głosuj na MVP (Wygrani)", color=discord.Color.green())
    embed_lose = discord.Embed(title="😓 Głosuj na MVP (Przegrani)", color=discord.Color.red())

    for i, g in enumerate(wygrani):
        embed_win.add_field(name=emojis[i], value=g, inline=False)
        bot.mvp_mapping[emojis[i]] = {"team": "A" if zwyciezca == 1 else "B", "user": g}

    for i, g in enumerate(przegrani):
        embed_lose.add_field(name=emojis[i], value=g, inline=False)
        bot.mvp_mapping[emojis[i]] = {"team": "B" if zwyciezca == 1 else "A", "user": g}

    win_msg = await ctx.send(embed=embed_win)
    lose_msg = await ctx.send(embed=embed_lose)
    bot.mvp_vote_messages = [win_msg.id, lose_msg.id]

    for i in range(5):
        await win_msg.add_reaction(emojis[i])
        await lose_msg.add_reaction(emojis[i])

    await ctx.send("✅ Głosowanie MVP rozpoczęte! Po zakończeniu wpisz `!mvp`.")

@bot.command()
@commands.has_permissions(administrator=True)
async def mvp(ctx):
    await ctx.send("⏳ Zliczanie głosów na MVP...")
    mvp_counts = {"A": Counter(), "B": Counter()}
    channel = ctx.channel

    for msg_id in bot.mvp_vote_messages:
        msg = await channel.fetch_message(msg_id)
        for reaction in msg.reactions:
            if str(reaction.emoji) in bot.mvp_mapping:
                async for user in reaction.users():
                    if user == bot.user:
                        continue
                    mapping = bot.mvp_mapping[str(reaction.emoji)]
                    mvp_counts[mapping["team"]][mapping["user"]] += 1

    mvp_a = mvp_counts["A"].most_common(1)
    mvp_b = mvp_counts["B"].most_common(1)
    mvp_a_name = mvp_a[0][0] if mvp_a else None
    mvp_b_name = mvp_b[0][0] if mvp_b else None

    przetworz_mecz(bot.last_teams["A"], bot.last_teams["B"], "A" if bot.zwyciezca == 1 else "B", mvp_a_name, mvp_b_name)
    zapisz_dane()

    embed = discord.Embed(title="📊 MVP zatwierdzeni!", color=discord.Color.gold())
    if mvp_a_name: embed.add_field(name="MVP A", value=mvp_a_name, inline=True)
    if mvp_b_name: embed.add_field(name="MVP B", value=mvp_b_name, inline=True)
    await ctx.send(embed=embed)

@bot.command()
async def ranking(ctx):
    top = ranking()
    wynik = "**Ranking ELO**\n" + "\n".join(f"{i+1}. {nick}: {elo}" for i, (nick, elo) in enumerate(top[:10]))
    await ctx.send(wynik)

@bot.command()
async def profil(ctx, *, nick=None):
    nick = nick or str(ctx.author)
    dane = profil(nick)
    await ctx.send(f"**{nick}**\nELO: {dane['elo']}\nWygrane: {dane['wygrane']}\nPrzegrane: {dane['przegrane']}\nMVP: {dane['mvp']}")




####################### FUNKCJE POMOCNICZE ##################################

def log_entry(user, action):
    with open(log_file, 'a', encoding='utf-8') as f:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        f.write(f'[{timestamp}] {action}: {user}\n')

def aktualizuj_listy():
    global signups, waiting_list, signup_ids
    combined = signups + waiting_list
    signups = combined[:MAX_SIGNUPS]
    waiting_list = combined[MAX_SIGNUPS:]
    signup_ids = []
    for member in bot.get_all_members():
        if not member.bot:
            if member.display_name in signups:
                signup_ids.append(member.id)
                print(f"[DEBUG] przypisuję {member.display_name} -> {member.id}")

class UsunButton(discord.ui.Button):
    def __init__(self, nick):
        super().__init__(label=f"Usuń {nick}", style=discord.ButtonStyle.danger)
        self.nick = nick

    async def callback(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Nie masz uprawnień.", ephemeral=True)
            return

        if self.nick in signups:
            signups.remove(self.nick)
            aktualizuj_listy()
            await interaction.response.send_message(f"🗑️ Usunięto {self.nick} z listy!", ephemeral=True)
        else:
            await interaction.response.send_message(f"{self.nick} już nie ma na liście.", ephemeral=True)


class ZapiszButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✅ Zapisz się", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction):
        nick = interaction.user.display_name
        user_id = interaction.user.id

        if nick in signups or nick in waiting_list:
            await interaction.response.send_message("Już jesteś zapisany.", ephemeral=True)
            return

        if len(signups) < MAX_SIGNUPS:
            signups.append(nick)
            if user_id not in signup_ids:
                signup_ids.append(user_id)
            log_entry(nick, "Zapisano przez przycisk")
        else:
            waiting_list.append(nick)
            log_entry(nick, "Dodano do rezerwowej przez przycisk")

        aktualizuj_listy()
        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        await interaction.message.delete()
        await interaction.channel.send(embed=generuj_embed_panel(), view=PanelView(ctx))


class WypiszButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="❌ Wypisz się", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction):
        nick = interaction.user.display_name
        removed = False

        if nick in signups:
            signups.remove(nick)
            removed = True
        elif nick in waiting_list:
            waiting_list.remove(nick)
            removed = True

        if removed:
            log_entry(nick, "Wypisano przez przycisk")
            aktualizuj_listy()
            ctx = await bot.get_context(interaction.message)
            ctx.author = interaction.user
            await interaction.message.delete()
            await interaction.channel.send(embed=generuj_embed_panel(), view=PanelView(ctx))
        else:
            await interaction.response.send_message("Nie jesteś zapisany.", ephemeral=True)


class RezerwowyButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="🕒 Do rezerwowej", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction):
        nick = interaction.user.display_name

        if nick in signups:
            await interaction.response.send_message("Jesteś już na głównej liście.", ephemeral=True)
            return

        if nick in waiting_list:
            await interaction.response.send_message("Jesteś już na liście rezerwowej.", ephemeral=True)
            return

        waiting_list.append(nick)
        log_entry(nick, "Dodano do rezerwowej przez przycisk")
        aktualizuj_listy()

        ctx = await bot.get_context(interaction.message)
        ctx.author = interaction.user
        await interaction.message.delete()
        await interaction.channel.send(embed=generuj_embed_panel(), view=PanelView(ctx))



class PrzeniesDoRezerwowejButton(discord.ui.Button):
    def __init__(self, nick):
        super().__init__(label=f"🔽 {nick}", style=discord.ButtonStyle.secondary)
        self.nick = nick

    async def callback(self, interaction: discord.Interaction):
        if self.nick in signups:
            signups.remove(self.nick)
            waiting_list.append(self.nick)
            aktualizuj_listy()
            log_entry(self.nick, "Przeniesiono do rezerwowej")
            ctx = await bot.get_context(interaction.message)
            ctx.author = interaction.user
            await interaction.message.delete()
            await interaction.channel.send(embed=generuj_embed_panel(), view=PanelView(ctx))
        else:
            await interaction.response.send_message("Gracz nie jest w głównej liście.", ephemeral=True)


class PrzeniesDoGlownejButton(discord.ui.Button):
    def __init__(self, nick):
        super().__init__(label=f"🔼 {nick}", style=discord.ButtonStyle.primary)
        self.nick = nick

    async def callback(self, interaction: discord.Interaction):
        if self.nick in waiting_list and len(signups) < MAX_SIGNUPS:
            waiting_list.remove(self.nick)
            signups.append(self.nick)
            aktualizuj_listy()
            log_entry(self.nick, "Przeniesiono do głównej")
            ctx = await bot.get_context(interaction.message)
            ctx.author = interaction.user
            await interaction.message.delete()
            await interaction.channel.send(embed=generuj_embed_panel(), view=PanelView(ctx))
        else:
            await interaction.response.send_message(
                "Nie można przenieść (lista pełna lub gracz nie jest w rezerwowej).",
                ephemeral=True
            )


class ListaView(discord.ui.View):
    def __init__(self, zapisani):
        super().__init__(timeout=None)
        for nick in zapisani:
            self.add_item(UsunButton(nick))


bot.run(TOKEN)
