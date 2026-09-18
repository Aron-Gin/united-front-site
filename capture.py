"""
Captura periodica dos dados do site do jogo (holiday.servegame.com) pra
alimentar o dashboard United Front (data.json no mesmo repo do GitHub Pages).

Fontes:
  - Guild (membros, level, vocacao, online/offline):
      /?subtopic=guilds&action=show&guild=PVP+COVARDE
  - Highscores de Experience (rank, nome, level, exp em %), paginado:
      /index.php/highscores/experience/<pagina>
  - Payhunt (quem esta na payhunt agora, incluindo Extra/Backup):
      /index.php/payhunt
  - Kill Statistics (mortes recentes, com timestamp):
      /index.php/killstatistics

So guarda no snapshot quem e da guild (cruza highscores x lista de membros).
"""
import json, os, re, time, sys
import urllib.request
from datetime import datetime

BASE = "https://holiday.servegame.com"
GUILD_NAME = "PVP COVARDE"
HIGHSCORE_PAGES = 10  # cobre ate rank ~1000; da conta dos membros de level mais alto
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
HISTORY_HOURS = 48  # quanto de snapshot/mortes manter no arquivo

HEADERS = {"User-Agent": "Mozilla/5.0 (UnitedFrontCapture/1.0)"}

def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")

def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()

def parse_guild_members(html):
    members = {}
    for m in re.finditer(
        r'<tr bgcolor="[^"]*">\s*<td>\s*([^<]*?)\s*</td>\s*<td>.*?<a href="[^"]*/characters/[^"]*">\s*([^<]+?)\s*</a>.*?</td>\s*<td>\s*([^<]+?)\s*</td>\s*<td>\s*(\d+)\s*</td>\s*<td class="onlinestatus">\s*.*?<b>\s*(Online|Offline)\s*</b>',
        html, re.S):
        rank_title, name, vocation, level, status = m.groups()
        name = clean(name)
        if not name:
            continue
        members[name] = {
            "name": name,
            "level": int(level),
            "vocation": clean(vocation),
            "online": status == "Online",
        }
    return members

def parse_highscores_page(html):
    rows = []
    for m in re.finditer(
        r'<tr bgcolor="[^"]*">\s*<td>\s*\d+\.\s*</td>\s*<td>\s*<a href="[^"]*/characters/[^"]*">\s*([^<]+?)\s*</a>\s*</td>.*?<td[^>]*>\s*(\d+)\s*</td>.*?<td[^>]*>\s*([\d.]+)%\s*</td>',
        html, re.S):
        name, level, pct = m.groups()
        rows.append((clean(name), int(level), float(pct)))
    has_next = "Next Page" in html
    return rows, has_next

def collect_highscores():
    all_rows = {}
    for page in range(1, HIGHSCORE_PAGES + 1):
        url = f"{BASE}/index.php/highscores/experience/{page}"
        try:
            html = fetch(url)
        except Exception as ex:
            print(f"[highscores pagina {page}] erro: {ex}", file=sys.stderr)
            break
        rows, has_next = parse_highscores_page(html)
        for name, level, pct in rows:
            all_rows[name] = (level, pct)
        if not has_next:
            break
        time.sleep(0.5)
    return all_rows

def parse_payhunt_names(html):
    names = set()
    for m in re.finditer(r'class="player-name">\s*<span class="status-dot"></span>\s*([^<]+?)\s*</td>', html, re.S):
        n = clean(m.group(1))
        if n:
            names.add(n)
    return names

MONTHS_PT = None  # datas ja vem numericas (DD.MM.YYYY), nao precisa de nomes de mes

def parse_kill_timestamp(text):
    # "17.09.2026, 23:38:14" -> epoch ms (hora do servidor, usada so p/ ordenar/filtrar)
    try:
        dt = datetime.strptime(text.strip(), "%d.%m.%Y, %H:%M:%S")
        return int(dt.timestamp() * 1000)
    except Exception:
        return None

def parse_killstatistics(html):
    deaths = []
    for m in re.finditer(
        r'<td width="110"[^>]*>\s*<small>\s*([\d.:, ]+?)\s*</small>\s*</td>\s*<td valign="top">\s*<a[^>]*>\s*<b>\s*([^<]+?)\s*</b>\s*</a>\s*(has slain by|has died by)',
        html, re.S):
        ts_text, victim, cause = m.groups()
        ts = parse_kill_timestamp(ts_text)
        if ts is None:
            continue
        deaths.append({"name": clean(victim), "seenAt": ts, "pvp": cause.strip() == "has slain by"})
    return deaths

def load_existing():
    if os.path.exists(DATA_PATH):
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"snapshots": [], "deaths": []}

def prune(data, now_ms):
    cutoff = now_ms - HISTORY_HOURS * 3600 * 1000
    data["snapshots"] = [s for s in data["snapshots"] if s["t"] >= cutoff]
    data["deaths"] = [d for d in data["deaths"] if d["seenAt"] >= cutoff]
    return data

def main():
    now_ms = int(time.time() * 1000)

    print("Buscando membros da guild...")
    guild_html = fetch(f"{BASE}/?subtopic=guilds&action=show&guild=" + GUILD_NAME.replace(" ", "+"))
    members = parse_guild_members(guild_html)
    print(f"  {len(members)} membros encontrados")

    print("Buscando highscores (experience)...")
    highscore_map = collect_highscores()
    print(f"  {len(highscore_map)} personagens no highscores (paginas escaneadas)")

    print("Buscando payhunt...")
    payhunt_html = fetch(f"{BASE}/index.php/payhunt")
    payhunt_names = parse_payhunt_names(payhunt_html)
    print(f"  {len(payhunt_names)} na payhunt/extra")

    print("Buscando kill statistics...")
    kill_html = fetch(f"{BASE}/index.php/killstatistics")
    all_deaths = parse_killstatistics(kill_html)
    # So mortes de membros da guild -- o killstatistics e do servidor
    # inteiro, sem esse filtro o "Top Mortes" mistura todo mundo.
    new_deaths = [d for d in all_deaths if d["name"] in members]
    print(f"  {len(all_deaths)} mortes na pagina atual, {len(new_deaths)} de membros da guild")

    # Monta as rows do snapshot: todo membro da guild, cruzando com highscores
    # quando disponivel (pct preciso); senao so o level (pct fica None).
    rows = []
    for name, info in members.items():
        if name in highscore_map:
            level, pct = highscore_map[name]
            rows.append([name, level, pct])
        else:
            rows.append([name, info["level"], None])

    data = load_existing()
    data["snapshots"].append({"t": now_ms, "rows": rows, "payhunt": sorted(payhunt_names)})

    existing_keys = {(d["name"], d["seenAt"]) for d in data["deaths"]}
    added = 0
    for d in new_deaths:
        key = (d["name"], d["seenAt"])
        if key not in existing_keys:
            data["deaths"].append(d)
            existing_keys.add(key)
            added += 1
    print(f"  {added} mortes novas adicionadas (dedup por nome+timestamp)")

    data = prune(data, now_ms)

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))

    print(f"OK -- {len(data['snapshots'])} snapshots, {len(data['deaths'])} mortes no arquivo final")

if __name__ == "__main__":
    main()
