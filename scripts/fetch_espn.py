"""Baixa as stats diárias do seu time (ESPN Fantasy Basketball) e salva em data.json.

As credenciais vêm SEMPRE de variáveis de ambiente (GitHub Secrets): SWID e ESPN_S2.
Nunca escreva elas dentro do código.
"""
import json
import os
import re
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone

import requests

LEAGUE_ID = 1059318411
TEAM_ID = 32
SEASON = 2027
START = date.fromisoformat(os.getenv("SEASON_START", "2026-10-20"))  # dia do scoring period 1
OUTPUT_FILE = "data.json"
URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/"
    f"seasons/{SEASON}/segments/0/leagues/{LEAGUE_ID}"
)

# Mesma tabela de pesos do app
WEIGHTS = {
    "fgm": 0.5, "fgmi": -0.5, "ftm": 0.25, "ftmi": -0.25,
    "tpm": 0.5, "tpmi": -0.25, "oreb": 1.0, "reb": 1.0,
    "ast": 2.25, "stl": 3.75, "blk": 4.0, "to": -2.5,
    "ej": -5.0, "ff": -2.0, "pf": -1.0, "tf": -1.5,
    "dd": 2.0, "td": 3.0, "qd": 20.0, "pts": 1.0,
}

# IDs de estatística do ESPN basquete (conforme a biblioteca espn-api).
# Antes de salvar, tudo é conferido contra o "appliedTotal" oficial do ESPN.
S = {
    "pts": "0", "blk": "1", "stl": "2", "ast": "3", "oreb": "4", "reb": "6",
    "ej": "7", "ff": "8", "pf": "9", "tf": "10", "to": "11",
    "fgm": "13", "fga": "14", "ftm": "15", "fta": "16", "tpm": "17", "tpa": "18",
}

BENCH_SLOTS = (12, 13)  # no basquete: 12 = banco, 13 = IR

PRO_TEAMS = {
    1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN",
    8: "DET", 9: "GS", 10: "HOU", 11: "IND", 12: "LAC", 13: "LAL", 14: "MIA",
    15: "MIL", 16: "MIN", 17: "BKN", 18: "NY", 19: "ORL", 20: "PHI", 21: "PHX",
    22: "POR", 23: "SAC", 24: "SA", 25: "OKC", 26: "UTAH", 27: "WAS", 28: "TOR",
    29: "MEM", 30: "CHA",
}
POS = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C"}


def slugify(text):
    text = unicodedata.normalize("NFD", text).encode("ascii", "ignore").decode("utf-8")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def need(name):
    value = os.getenv(name)
    if not value:
        sys.exit(f"Falta o secret {name}.")
    return value


def fetch(spid, cookies):
    params = [("view", "mRoster")]
    if spid:
        params.append(("scoringPeriodId", spid))
    r = requests.get(URL, params=params, cookies=cookies, timeout=60)
    if r.status_code != 200:
        sys.exit(f"ESPN respondeu {r.status_code}. Confira ESPN_S2 e SWID (o ESPN_S2 pode ter expirado).")
    return r.json()


def parse(raw):
    def g(key):
        value = float(raw.get(S[key], 0) or 0)
        return int(value) if value == int(value) else value

    pts, reb, ast, stl, blk = g("pts"), g("reb"), g("ast"), g("stl"), g("blk")
    cats = sum(1 for v in (pts, reb, ast, stl, blk) if v >= 10)  # DD/TD/QD calculados aqui
    return {
        "fgm": g("fgm"), "fgmi": max(0, g("fga") - g("fgm")),
        "ftm": g("ftm"), "ftmi": max(0, g("fta") - g("ftm")),
        "tpm": g("tpm"), "tpmi": max(0, g("tpa") - g("tpm")),
        "oreb": g("oreb"), "reb": reb, "ast": ast, "stl": stl, "blk": blk,
        "to": g("to"), "ej": g("ej"), "ff": g("ff"), "pf": g("pf"), "tf": g("tf"),
        "dd": int(cats >= 2), "td": int(cats >= 3), "qd": int(cats >= 4),
        "pts": pts,
    }


def score(stats):
    return round(sum(stats[k] * w for k, w in WEIGHTS.items()), 2)


SLOTS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F", 7: "SG/SF", 8: "G/F", 9: "PF/C", 10: "F/C", 11: "UTIL", 12: "BE", 13: "IR"}


def matchup_period(spid):
    # semana 1 = 6 dias (20/10 a 25/10); depois, 7 dias por semana
    return 1 if spid <= 6 else 2 + (spid - 7) // 7


def get_json(cookies, params, headers=None):
    r = requests.get(URL, params=params, cookies=cookies, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()


def safe(label, fn, fallback):
    """Partes extras nunca derrubam a atualização das pontuações."""
    try:
        return fn()
    except Exception as e:
        print(f"Aviso: {label} indisponível ({e})", file=sys.stderr)
        return fallback


def day_split(player, spid):
    return next(
        (s for s in player.get("stats", [])
         if s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 5 and s.get("scoringPeriodId") == spid),
        None,
    )


def fetch_league(cookies):
    """Adversário de cada semana + tabela da liga."""
    data = get_json(cookies, [("view", "mTeam"), ("view", "mMatchup"), ("view", "mMatchupScore")])
    members = {m.get("id"): m for m in data.get("members", [])}
    names, table = {}, []
    for t in data.get("teams", []):
        nome = t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip() or f"Time {t.get('id')}"
        dono = ""
        for oid in t.get("owners", []):
            m = members.get(oid)
            if m:
                dono = f"{m.get('firstName', '')} {m.get('lastName', '')}".strip() or m.get("displayName", "")
                break
        names[t.get("id")] = f"{nome} ({dono})" if dono else nome
        rec = (t.get("record") or {}).get("overall") or {}
        table.append({
            "id": t.get("id"), "name": nome, "owner": dono,
            "w": rec.get("wins", 0), "l": rec.get("losses", 0), "t": rec.get("ties", 0),
            "pf": round(rec.get("pointsFor", 0), 2), "pa": round(rec.get("pointsAgainst", 0), 2),
            "me": t.get("id") == TEAM_ID,
        })
    table.sort(key=lambda r: (-r["w"], -r["pf"]))
    weeks = {}
    for m in data.get("schedule", []):
        home = (m.get("home") or {}).get("teamId")
        away = (m.get("away") or {}).get("teamId")
        if TEAM_ID in (home, away):
            opp = away if home == TEAM_ID else home
            if opp is not None and m.get("matchupPeriodId") is not None:
                weeks[str(m["matchupPeriodId"])] = {"opp": names.get(opp, f"Time {opp}"), "oppId": opp}
    return weeks, table


def fetch_free_agents(cookies, limit=60):
    """Melhores livres dos últimos 7 dias, na pontuação da sua liga."""
    flt = {"players": {
        "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
        "limit": limit,
        "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
    }}
    data = get_json(cookies, [("view", "kona_player_info")], {"x-fantasy-filter": json.dumps(flt)})
    out = []
    for e in data.get("players", []):
        p = e.get("player", {})
        l7 = next((s for s in p.get("stats", []) if s.get("statSourceId") == 0 and s.get("statSplitTypeId") == 1), None)
        if not l7 or not l7.get("appliedTotal"):
            continue
        out.append({
            "name": p.get("fullName", "?"),
            "team": PRO_TEAMS.get(p.get("proTeamId"), "—"),
            "pos": POS.get(p.get("defaultPositionId"), "—"),
            "total": round(l7["appliedTotal"], 2),
            "avg": round(l7.get("appliedAverage") or 0, 2),
            "owned": round((p.get("ownership") or {}).get("percentOwned") or 0, 1),
        })
    out.sort(key=lambda x: -x["total"])
    return out[:12]


def fetch_pro_games(cookies, spid):
    """Jogos da NBA no scoring period: {proTeamId: {opp, home, date}}."""
    data = get_json(cookies, [("view", "proTeamSchedules_wl")])
    games = {}
    for t in (data.get("settings") or {}).get("proTeamSchedules", []):
        for g in (t.get("proGamesByScoringPeriod") or {}).get(str(spid), []):
            h, a = g.get("homeProTeamId"), g.get("awayProTeamId")
            games[h] = {"opp": PRO_TEAMS.get(a, "—"), "home": True, "date": g.get("date")}
            games[a] = {"opp": PRO_TEAMS.get(h, "—"), "home": False, "date": g.get("date")}
    return games


def main():
    cookies = {"SWID": need("SWID"), "espn_s2": need("ESPN_S2")}

    print("Conectando à API da ESPN...")
    current = int(fetch(None, cookies).get("scoringPeriodId") or 1)

    old, games_data, old_players, old_weeks = {}, {}, {}, {}
    if os.path.exists(OUTPUT_FILE):
        try:
            with open(OUTPUT_FILE, encoding="utf-8") as fh:
                old = json.load(fh)
            games_data = old.get("gamesData", {})
            old_players = {p["id"]: p for p in old.get("players", [])}
            old_weeks = old.get("weeks", {})
        except Exception:
            old, games_data, old_players, old_weeks = {}, {}, {}, {}
    # primeira vez (ou faltando dados de algum jogador): temporada inteira;
    # depois: só os últimos dias (pega correções de stats)
    missing = any(pid not in old_players for pid in games_data)
    first = max(1, current - 4) if games_data and not missing else 1

    seen, active, bad, total_found = {}, set(), [], 0
    today_entries, opp_daily = {}, dict(old.get("oppDaily", {}))
    weeks, table = safe("adversários e tabela", lambda: fetch_league(cookies), (old_weeks, old.get("table", [])))
    weeks = weeks or old_weeks
    for spid in range(first, current + 1):
        day = (START + timedelta(days=spid - 1)).isoformat()
        data = fetch(spid, cookies)
        team = next((t for t in data.get("teams", []) if t.get("id") == TEAM_ID), None)
        if not team:
            sys.exit(f"Time {TEAM_ID} não encontrado na liga.")

        found = 0
        for entry in team.get("roster", {}).get("entries", []):
            player = entry.get("playerPoolEntry", {}).get("player", {})
            name = player.get("fullName", "Desconhecido")
            pid = slugify(name)

            seen[pid] = {
                "id": pid,
                "name": name,
                "team": PRO_TEAMS.get(player.get("proTeamId"), "—"),
                "pos": POS.get(player.get("defaultPositionId"), "—"),
            }
            if spid == current:
                active.add(pid)
                today_entries[pid] = {
                    "slot": SLOTS.get(entry.get("lineupSlotId"), "?"),
                    "pro": player.get("proTeamId"),
                    "inj": player.get("injuryStatus", ""),
                }

            # stats reais (source 0) de um único dia (split 5) daquele scoring period
            st = next(
                (s for s in player.get("stats", [])
                 if s.get("statSourceId") == 0
                 and s.get("statSplitTypeId") == 5
                 and s.get("scoringPeriodId") == spid),
                None,
            )
            raw = (st or {}).get("stats") or {}
            if not any(raw.values()):
                continue  # não jogou nesse dia

            stats = parse(raw)
            mine, official = score(stats), st.get("appliedTotal")
            if official is not None and abs(mine - official) > 0.05:
                bad.append(f"{day} {name}: app={mine} espn={official} brutos={raw}")

            game = {
                "id": f"espn-{day}-{pid}",
                "date": day,
                "opp": "",
                "notStarted": entry.get("lineupSlotId") in BENCH_SLOTS,
                "stats": stats,
            }
            games = [x for x in games_data.get(pid, []) if x.get("id") != game["id"]]
            games.append(game)
            games_data[pid] = games
            found += 1
        # pontos do adversário no dia (só quem estava titular)
        opp_id = (weeks.get(str(matchup_period(spid))) or {}).get("oppId")
        opp_team = next((t for t in data.get("teams", []) if t.get("id") == opp_id), None) if opp_id is not None else None
        if opp_team:
            opp_total = 0.0
            for e in opp_team.get("roster", {}).get("entries", []):
                if e.get("lineupSlotId") in BENCH_SLOTS:
                    continue
                raw_o = (day_split(e.get("playerPoolEntry", {}).get("player", {}), spid) or {}).get("stats") or {}
                if any(raw_o.values()):
                    opp_total += score(parse(raw_o))
            opp_daily[day] = round(opp_total, 2)
        total_found += found
        print(f"{day}: {found} jogos")

    if bad:
        print("DIFERENÇA entre o cálculo do app e o ESPN. Nada foi salvo:", file=sys.stderr)
        for line in bad[:10]:
            print(" ", line, file=sys.stderr)
        sys.exit(1)
    if total_found == 0 and not games_data:
        sys.exit("Nenhum jogo encontrado. Veja o log acima.")

    all_players = dict(old_players)
    all_players.update(seen)
    players_list = [dict(p, active=p["id"] in active) for p in all_players.values()]
    players_list.sort(key=lambda p: (not p["active"], p["name"]))

    print("Adversários encontrados:", len(weeks))
    free_agents = safe("free agents", lambda: fetch_free_agents(cookies), old.get("freeAgents", []))
    games_today = safe("jogos de hoje", lambda: fetch_pro_games(cookies, current), {})
    today = old.get("today")
    if today_entries and games_today:
        today = {
            "date": (START + timedelta(days=current - 1)).isoformat(),
            "players": [
                {"id": pid, "name": seen[pid]["name"], "slot": e["slot"], "inj": e["inj"], "game": games_today.get(e["pro"])}
                for pid, e in today_entries.items()
            ],
        }

    payload = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "players": players_list,
        "weeks": weeks,
        "oppDaily": opp_daily,
        "table": table,
        "freeAgents": free_agents,
        "today": today,
        "gamesData": games_data,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print("data.json atualizado com sucesso!")


if __name__ == "__main__":
    main()
