import json
import random
from datetime import datetime

ELO_START = 1000
ELO_MIN_GAIN = 15
ELO_MIN_LOSS = 15
MVP_BONUS_WIN = 5
MVP_BONUS_LOSS = -5

# Pliki do zapisu
PLIK_GRACZE = "gracze.json"
PUNKTY_ELO = {}
LOGI_MECZY = []

# Wczytywanie danych
def wczytaj_dane():
    global PUNKTY_ELO, LOGI_MECZY
    try:
        with open(PLIK_GRACZE, "r") as f:
            PUNKTY_ELO = json.load(f)
    except FileNotFoundError:
        PUNKTY_ELO = {}

    try:
        with open("mecze_log.json", "r") as f:
            LOGI_MECZY = json.load(f)
    except FileNotFoundError:
        LOGI_MECZY = []

# Zapisywanie danych
def zapisz_dane():
    with open(PLIK_GRACZE, "w") as f:
        json.dump(PUNKTY_ELO, f, indent=2)
    with open("mecze_log.json", "w") as f:
        json.dump(LOGI_MECZY, f, indent=2)

# Dodanie gracza
def dodaj_gracza(nick):
    if nick not in PUNKTY_ELO:
        PUNKTY_ELO[nick] = ELO_START

# Oblicz przewidywaną szansę
def przewidywana_szansa(elo1, elo2):
    return 1 / (1 + 10 ** ((elo2 - elo1) / 400))

# Zaktualizuj ELO po meczu
def przetworz_mecz(druzyna_a, druzyna_b, zwyciezca, mvp_a=None, mvp_b=None):
    for g in druzyna_a + druzyna_b:
        dodaj_gracza(g)

    suma_a = sum(PUNKTY_ELO[g] for g in druzyna_a)
    suma_b = sum(PUNKTY_ELO[g] for g in druzyna_b)

    szansa_a = przewidywana_szansa(suma_a, suma_b)
    szansa_b = 1 - szansa_a

    wynik_a, wynik_b = (1, 0) if zwyciezca == "A" else (0, 1)
    zmiany = {}

    # rozdziel punktów w drużynie A
    for gracz in druzyna_a:
        baza = 32 * (wynik_a - szansa_a)
        baza = max(baza, ELO_MIN_GAIN) if baza > 0 else min(baza, -ELO_MIN_LOSS)
        if mvp_a == gracz:
            baza += MVP_BONUS_WIN if wynik_a == 1 else MVP_BONUS_LOSS
        PUNKTY_ELO[gracz] += round(baza)
        zmiany[gracz] = round(baza)

    # rozdziel punktów w drużynie B
    for gracz in druzyna_b:
        baza = 32 * (wynik_b - szansa_b)
        baza = max(baza, ELO_MIN_GAIN) if baza > 0 else min(baza, -ELO_MIN_LOSS)
        if mvp_b == gracz:
            baza += MVP_BONUS_WIN if wynik_b == 1 else MVP_BONUS_LOSS
        PUNKTY_ELO[gracz] += round(baza)
        zmiany[gracz] = round(baza)

    log = {
        "czas": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "druzyna_a": druzyna_a,
        "druzyna_b": druzyna_b,
        "zwyciezca": zwyciezca,
        "mvp_a": mvp_a,
        "mvp_b": mvp_b,
        "zmiany": zmiany
    }
    LOGI_MECZY.append(log)
    zapisz_dane()

# Ranking graczy
def ranking():
    return sorted(PUNKTY_ELO.items(), key=lambda x: x[1], reverse=True)

# Profil gracza
def profil(nick):
    dodaj_gracza(nick)
    mecze = [m for m in LOGI_MECZY if nick in m["zmiany"]]
    win = sum(1 for m in mecze if (m["zwyciezca"] == "A" and nick in m["druzyna_a"]) or (m["zwyciezca"] == "B" and nick in m["druzyna_b"]))
    loss = len(mecze) - win
    mvp_count = sum(1 for m in mecze if m.get("mvp_a") == nick or m.get("mvp_b") == nick)

    return {
        "nick": nick,
        "elo": PUNKTY_ELO[nick],
        "wygrane": win,
        "przegrane": loss,
        "mvp": mvp_count
    }

# Wczytaj dane na start
wczytaj_dane()
