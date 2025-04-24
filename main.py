
import discord
from discord.ext import commands, tasks
from datetime import datetime, time
import asyncio
import os
import json
from dotenv import load_dotenv
from flask import Flask
from threading import Thread
from elo_mvp_system import przetworz_mecz, ranking, profil, wczytaj_dane, zapisz_dane

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

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

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

MAX_SIGNUPS = 10
signups = []
waiting_list = []
log_file = 'signup_log.txt'
event_time = time(20, 0)

wczytaj_dane()

@bot.event
async def on_ready():
    print(f'Zalogowano jako {bot.user.name}')
    check_event_time.start()

@bot.command()
async def help(ctx):
    help_text = (
    "**Lista dostępnych komend:**\\n"
    "`!zapisz` – Zapisuje Cię na wydarzenie.\\n"
    "`!wypisz` – Wypisuje Cię z listy.\\n"
    "`!lista` – Wyświetla listę zapisanych i rezerwowych.\\n"
    "`!dodaj <nick>` – (admin) Ręczne dodanie gracza.\\n"
    "`!usun <nick>` – (admin) Ręczne usunięcie gracza.\\n"
    "`!reset` – (admin) Resetuje listy zapisów.\\n"
    "`!ustaw <hh:mm>` – (admin) Ustawia godzinę wydarzenia.\\n"
    "`!czas` – Pokazuje aktualnie ustawioną godzinę wydarzenia.\\n"
    "`!logi` – Wyświetla ostatnie logi zapisów.\\n"
    "`!ranking` – Pokazuje ranking ELO graczy.\\n"
    "`!profil [nick]` – Pokazuje Twój profil lub wybranego gracza."
)
    await ctx.send(help_text)

@bot.command()
async def zapisz(ctx):
    user = str(ctx.author)
    if user in signups or user in waiting_list:
        await ctx.send(f'{user}, jesteś już zapisany.')
        return
    if len(signups) < MAX_SIGNUPS:
        signups.append(user)
        log_entry(user, 'Zapisano')
        await ctx.send(f'{user} został zapisany. ({len(signups)}/{MAX_SIGNUPS})')
    else:
        waiting_list.append(user)
        log_entry(user, 'Lista rezerwowa')
        await ctx.send(f'{user}, dodano do listy rezerwowej.')

@bot.command()
async def wypisz(ctx):
    user = str(ctx.author)
    if user in signups:
        signups.remove(user)
        log_entry(user, 'Wypisano')
        if waiting_list:
            moved_user = waiting_list.pop(0)
            signups.append(moved_user)
            log_entry(moved_user, 'Przeniesiono z rezerwowej')
        await ctx.send(f'{user} został wypisany.')
    elif user in waiting_list:
        waiting_list.remove(user)
        log_entry(user, 'Usunięto z rezerwowej')
        await ctx.send(f'{user} usunięty z listy rezerwowej.')
    else:
        await ctx.send(f'{user}, nie jesteś zapisany.')

@bot.command()
@commands.has_permissions(administrator=True)
async def dodaj(ctx, *, user):
    if user not in signups:
        signups.append(user)
        log_entry(user, 'Dodany ręcznie')
        await ctx.send(f'✅ Dodano {user} do zapisów.')
    else:
        await ctx.send(f'{user} już jest na liście.')

@bot.command()
@commands.has_permissions(administrator=True)
async def usun(ctx, *, user):
    if user in signups:
        signups.remove(user)
        log_entry(user, 'Usunięty ręcznie')
        await ctx.send(f'🗑️ Usunięto {user} z zapisów.')
    else:
        await ctx.send(f'{user} nie znajduje się na liście.')

@bot.command()
async def lista(ctx):
    list_msg = '**Zapisani:**
' + '\n'.join(f'{i+1}. {name}' for i, name in enumerate(signups))
    if waiting_list:
        list_msg += '\n**Rezerwowi:**\n' + '\n'.join(f'- {name}' for name in waiting_list)
    await ctx.send(list_msg)

@bot.command()
@commands.has_permissions(administrator=True)
async def ustaw(ctx, godzina: str):
    global event_time
    try:
        godz, minuty = map(int, godzina.split(':'))
        event_time = time(godz, minuty)
        await ctx.send(f'⏰ Ustawiono nową godzinę wydarzenia: {event_time.strftime("%H:%M")}')
    except:
        await ctx.send('❌ Niepoprawny format! Użyj np. `!ustaw 18:30`.')

@bot.command()
async def czas(ctx):
    godzina_str = event_time.strftime("%H:%M")
    await ctx.send(f"⏰ Aktualna godzina wydarzenia: {godzina_str}")

@bot.command()
@commands.has_permissions(administrator=True)
async def reset(ctx):
    global signups, waiting_list
    signups = []
    waiting_list = []
    log_entry(str(ctx.author), 'Zresetowano listy')
    await ctx.send('🗑️ Lista zapisów i rezerwowa zostały wyczyszczone.')

@bot.command()
async def logi(ctx):
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()[-10:]
            log_text = ''.join(lines) or 'Brak logów.'
        await ctx.send(f'📝 Ostatnie logi:\n```{log_text}```')
    except FileNotFoundError:
        await ctx.send('❌ Nie znaleziono pliku logów.')

@bot.command(name="ranking")
async def show_ranking(ctx):
    top = ranking()
    wynik = "**Ranking ELO**\n"
    for i, (nick, elo) in enumerate(top[:10], 1):
        wynik += f"{i}. {nick}: {elo}\n"
    await ctx.send(wynik)

@bot.command(name="profil")
async def show_profil(ctx, *, nick=None):
    nick = nick or str(ctx.author)
    dane = profil(nick)
    await ctx.send(f"**{nick}**\nELO: {dane['elo']}\nWygrane: {dane['wygrane']}\nPrzegrane: {dane['przegrane']}\nMVP: {dane['mvp']}")

@tasks.loop(seconds=60)
async def check_event_time():
    now = datetime.now().time()
    if now.hour == event_time.hour and now.minute == event_time.minute:
        channel = discord.utils.get(bot.get_all_channels(), name='ogolny')
        if channel:
            await channel.send('📢 Wydarzenie rozpoczyna się teraz!')
        await asyncio.sleep(60)

def log_entry(user, action):
    with open(log_file, 'a', encoding='utf-8') as f:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        f.write(f'[{timestamp}] {action}: {user}\n')

bot.run(TOKEN)
