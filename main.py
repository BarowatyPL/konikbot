
import discord
from discord.ext import commands, tasks
from datetime import datetime, time
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
    if not event_time:
        return  # jeśli czas nieustawiony, nic nie rób

    now = datetime.now()
    event_datetime = datetime.combine(now.date(), event_time)
    delta = (event_datetime - now).total_seconds()

    if 0 < delta <= 900:  # 15 minut (900 sek)
        channel = bot.get_channel(1216013668773265458)
        if channel:
            mentions = []
            for member in channel.guild.members:
                if member.display_name in signups:
                    mentions.append(member.mention)
            if mentions:
                await channel.send("⏳ Wydarzenie za 15 minut! Obecni:\n" + " ".join(mentions))
            else:
                await channel.send("Nie udało się pingować graczy — brak dopasowanych nicków.")
        await asyncio.sleep(61)  # unika ponownego wysłania


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
        await ctx.send("📢 Wołam graczy z listy:\n" + " ".join(mentions))
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



@bot.command()
@commands.has_permissions(administrator=True)
async def wyczysc(ctx):
    global signups, waiting_list, signup_ids
    signups = []
    waiting_list = []
    signup_ids = []
    log_entry(str(ctx.author), 'Wyczyszczono listy zapisów')
    await ctx.send("🧹 Lista zapisów i rezerwowa została całkowicie wyczyszczona.")



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

@bot.command()
async def lista(ctx):
    embed = discord.Embed(title="📋 Lista graczy", color=discord.Color.teal())

    zapisani_display = signups[:MAX_SIGNUPS]
    rezerwowi_display = signups[MAX_SIGNUPS:] + waiting_list

    czas_info = event_time.strftime('%H:%M') if event_time else "Nieokreślono"
    embed.set_footer(text=f"Czas rozpoczęcia: {czas_info}")

    if zapisani_display:
        embed.add_field(
            name="✅ Gracze zapisani (do 10)",
            value="\n".join(f"{i+1}. {name}" for i, name in enumerate(zapisani_display)),
            inline=False
        )
    else:
        embed.add_field(
            name="✅ Gracze zapisani (do 10)",
            value="Brak zapisanych graczy",
            inline=False
        )

    if rezerwowi_display:
        embed.add_field(
            name="🕒 Lista rezerwowa",
            value="\n".join(f"- {name}" for name in rezerwowi_display),
            inline=False
        )

    await ctx.send(embed=embed)




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

@tasks.loop(seconds=60)
async def check_event_time():
    global event_time
    if not event_time:
        return
    now = datetime.now()
    event_today = datetime.combine(now.date(), event_time)
    delta = (event_today - now).total_seconds()
    print("[DEBUG] delta:", delta)
    channel = bot.get_channel(1216013668773265458)
    if not channel:
        return
    if 870 < delta <= 930:
        mentions = [member.mention for member in channel.guild.members if member.id in signup_ids]
        if mentions:
            await channel.send("⏳ Wydarzenie za 15 minut! Obecni:\n" + " ".join(mentions))
    elif 0 < delta <= 60:
        await channel.send("📢 Wydarzenie rozpoczyna się teraz!")




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
    for nick in signups:
        for member in bot.get_all_members():
            if not member.bot and member.display_name.lower().strip() == nick.lower().strip():
                signup_ids.append(member.id)
                print(f"[DEBUG] przypisuję {nick} -> {member.id} ({member.display_name})")
                break


bot.run(TOKEN)
