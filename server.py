import os, json, re, base64, math, requests
from difflib import SequenceMatcher
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

BASKETBALL_API_KEY = os.getenv("BASKETBALL_API_KEY", "")
BASKETBALL_API_URL = "https://v1.basketball.api-sports.io"

app = Flask(__name__, static_folder=".", static_url_path="")

MARGINE_SICUREZZA = 7


def tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        return False
    requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": msg, "parse_mode": "HTML"},
        timeout=20
    ).raise_for_status()
    return True


def parse_json(t):
    c = (t or "").replace("```json", "").replace("```", "").strip()
    m = re.search(r"\{.*\}", c, re.S)
    return json.loads(m.group(0) if m else c)


def extract(raw, mime):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")

    b64 = base64.b64encode(raw).decode()
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = """
Leggi uno screenshot basket live bookmaker. Rispondi SOLO JSON valido.

Formato:
{
"homeTeam":string|null,
"awayTeam":string|null,
"homeScore":number|null,
"awayScore":number|null,
"quarter":number|null,
"timeRemaining":"M:SS"|null,
"lineOU":number|null,
"oddsOver":number|null,
"oddsUnder":number|null,
"ouLines":[{"line":number,"over":number|null,"under":number|null}],
"q1Home":number|null,
"q1Away":number|null,
"q2Home":number|null,
"q2Away":number|null,
"q3Home":number|null,
"q3Away":number|null,
"q4Home":number|null,
"q4Away":number|null,
"confidence":number
}

Regole:
- Se il cronometro mostra 0:0 o 0:00, scrivi "0:00".
- Se è intervallo/fine secondo quarto, quarter=2 e timeRemaining="0:00".
- Se vedi più linee U/O, mettile in ouLines.
- Come lineOU scegli la linea U/O più bassa visibile.
- Non inventare dati non visibili.
"""

    r = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": [
                {"type": "text", "text": "Leggi lo screenshot."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
            ]}
        ],
        temperature=0
    )

    return parse_json(r.choices[0].message.content)


def clock_min(x):
    try:
        s = str(x or "0:00").strip().replace(" ", "")
        if s in ["0:0", "0.0"]:
            return 0
        if ":" in s:
            m, sec = s.split(":")[:2]
            return int(m) + int(sec) / 60
        return float(s)
    except Exception:
        return None


def ritmo(ppm):
    if ppm < 2.45:
        return "molto lento"
    if ppm < 2.85:
        return "lento"
    if ppm < 3.25:
        return "medio"
    if ppm < 3.65:
        return "veloce"
    return "molto veloce"


def prob(value):
    return max(5, min(95, round(100 / (1 + math.exp(-value / 5.8)))))


def stake(bankroll, conf, value):
    if conf < 68:
        return 0
    edge = abs(value)
    if edge >= 14 and conf >= 82:
        pct = 0.05
    elif edge >= 10 and conf >= 74:
        pct = 0.035
    else:
        pct = 0.025
    return max(1, round(bankroll * pct))


def safe_float(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None


def quarter_total(ex, h_key, a_key):
    h = safe_float(ex.get(h_key))
    a = safe_float(ex.get(a_key))
    if h is None or a is None:
        return None
    return h + a


def choose_best_line(ex):
    lines = ex.get("ouLines") or []
    valid = []

    for item in lines:
        line = safe_float(item.get("line"))
        over = safe_float(item.get("over"))
        under = safe_float(item.get("under"))
        if line is not None:
            valid.append({"line": line, "over": over, "under": under})

    if valid:
        chosen = sorted(valid, key=lambda x: x["line"])[0]
        return chosen["line"], chosen["over"], chosen["under"], valid

    return safe_float(ex.get("lineOU")), safe_float(ex.get("oddsOver")), safe_float(ex.get("oddsUnder")), []


def trend_quarti(q1, q2, q3, q4):
    trend_factor = 1.0
    desc = "trend quarti non disponibile"

    if q1 is not None and q2 is not None:
        diff = q2 - q1
        if diff <= -8:
            trend_factor *= 0.92
            desc = f"rallentamento tra Q1 e Q2 ({diff:+.0f} punti)"
        elif diff <= -5:
            trend_factor *= 0.95
            desc = f"leggero rallentamento tra Q1 e Q2 ({diff:+.0f} punti)"
        elif diff >= 8:
            trend_factor *= 1.08
            desc = f"accelerazione tra Q1 e Q2 ({diff:+.0f} punti)"
        elif diff >= 5:
            trend_factor *= 1.05
            desc = f"leggera accelerazione tra Q1 e Q2 ({diff:+.0f} punti)"
        else:
            desc = f"ritmo stabile tra Q1 e Q2 ({diff:+.0f} punti)"

    if q2 is not None and q3 is not None:
        diff = q3 - q2
        if diff <= -8:
            trend_factor *= 0.94
            desc = f"forte rallentamento recente ({diff:+.0f} punti)"
        elif diff <= -5:
            trend_factor *= 0.97
            desc = f"rallentamento recente ({diff:+.0f} punti)"
        elif diff >= 8:
            trend_factor *= 1.06
            desc = f"forte accelerazione recente ({diff:+.0f} punti)"
        elif diff >= 5:
            trend_factor *= 1.03
            desc = f"accelerazione recente ({diff:+.0f} punti)"

    return trend_factor, desc


def decide(ex, bankroll):
    home = ex.get("homeTeam") or "Squadra A"
    away = ex.get("awayTeam") or "Squadra B"

    line, odds_over, odds_under, all_lines = choose_best_line(ex)

    required = {
        "homeScore": ex.get("homeScore"),
        "awayScore": ex.get("awayScore"),
        "quarter": ex.get("quarter"),
        "timeRemaining": ex.get("timeRemaining"),
        "lineOU": line
    }

    missing = [k for k, v in required.items() if v is None]

    if missing:
        return nobet(
            home, away, "-", "-",
            "Dati mancanti dall'estrazione AI: " + ", ".join(missing) + ".",
            [
                "Il parser AI non ha restituito tutti i campi",
                "Ritaglia meglio lo screenshot o riprova",
                "Puntata consigliata 0 €"
            ],
            extracted=ex
        )

    h = int(float(ex["homeScore"]))
    a = int(float(ex["awayScore"]))
    total = h + a
    q = int(float(ex["quarter"]))
    left = clock_min(ex["timeRemaining"])
    line = float(line)

    if left is None or q < 1 or q > 4 or left < 0 or left > 10:
        return nobet(
            home, away, f"{h}-{a}", f"{ex.get('timeRemaining')} Q{q}",
            "Cronometro o quarto non affidabili.",
            ["Tempo non validato", "Puntata consigliata 0 €"],
            line,
            extracted=ex
        )

    played = (q - 1) * 10 + (10 - left)
    rem = max(0, 40 - played)
    if played <= 0:
        played = 0.1

    ppm = total / played

    q1 = quarter_total(ex, "q1Home", "q1Away")
    q2 = quarter_total(ex, "q2Home", "q2Away")
    q3 = quarter_total(ex, "q3Home", "q3Away")
    q4 = quarter_total(ex, "q4Home", "q4Away")

    trend_factor, trend_desc = trend_quarti(q1, q2, q3, q4)

    phase = {1: .97, 2: .95, 3: .93, 4: .88}.get(q, .90)
    fatigue = .96 if q >= 3 else 1
    blowout = .91 if abs(h - a) >= 22 and q >= 3 else 1
    close = .03 if abs(h - a) <= 8 and q >= 4 else 0

    corr_ppm = max(1.70, min(4.85, ppm * phase * fatigue * blowout * (1 + close) * trend_factor))
    raw = total + rem * corr_ppm
    shrink = .82 if played < 20 else .88
    pred = round(line + (raw - line) * shrink)

    value = pred - line
    po = prob(value)
    pu = 100 - po

    over_wait = round(pred - MARGINE_SICUREZZA, 1)
    under_wait = round(pred + MARGINE_SICUREZZA, 1)

    if value >= MARGINE_SICUREZZA:
        side = "OVER"
    elif value <= -MARGINE_SICUREZZA:
        side = "UNDER"
    else:
        side = None

    conf = 0
    conf += 22
    conf += 18 if odds_over and odds_under else 8
    conf += 20 if played >= 8 else 8
    conf += 25 if abs(value) >= 12 else 18 if abs(value) >= 9 else 10 if abs(value) >= 7 else 0
    conf += 8 if home != "Squadra A" and away != "Squadra B" else 0
    conf += 8 if q1 is not None and q2 is not None else 0
    if played < 5:
        conf -= 10
    conf = max(0, min(90, round(conf)))

    st = stake(bankroll, conf, value) if side else 0

    share = h / total if total else .5
    fh = round(pred * share)
    fa = round(pred - fh)

    if side and conf >= 68 and st > 0:
        signal = "BET"
        action = f"GIOCA {side}"
        decision_text = f"Scommetti {side} {line}"
        reason = f"Margine sufficiente. Totale previsto {pred}, linea {line}, margine {value:+.1f}."
    elif abs(value) >= 4:
        signal = "OBSERVE"
        action = "ASPETTA"
        decision_text = "Aspetta una linea più conveniente"
        reason = f"Margine insufficiente. Totale previsto {pred}, linea {line}, margine {value:+.1f}."
    else:
        signal = "NO_BET"
        action = "NO BET"
        decision_text = "Non scommettere"
        reason = f"Linea troppo vicina. Totale previsto {pred}, linea {line}, margine {value:+.1f}."

    why = [
        f"Totale previsto: {pred}",
        f"Linea bookmaker scelta: {line}",
        f"Margine: {value:+.1f}",
        f"Ritmo attuale: {ppm:.2f} punti/min",
        f"Trend ritmo: {trend_desc}",
        f"Q1: {q1 if q1 is not None else '-'} punti",
        f"Q2: {q2 if q2 is not None else '-'} punti",
        f"Q3: {q3 if q3 is not None else '-'} punti",
        f"OVER giocabile a {over_wait} o meno",
        f"UNDER giocabile da {under_wait} o più"
    ]

    return {
        "signal": signal,
        "action": action,
        "decision_text": decision_text,
        "side": side,
        "line": line,
        "stake": st,
        "confidence": conf,
        "score": f"{h}-{a}",
        "clock": f"{ex.get('timeRemaining')} Q{q}",
        "teams": {"home": home, "away": away},
        "rhythm": ritmo(ppm),
        "ppm": round(ppm, 2),
        "played": round(played, 1),
        "remaining": round(rem, 1),
        "total_predicted": pred,
        "final_score": f"{fh}-{fa}",
        "value": round(value, 1),
        "prob_over": po,
        "prob_under": pu,
        "over_wait_line": over_wait,
        "under_wait_line": under_wait,
        "q1_total": q1,
        "q2_total": q2,
        "q3_total": q3,
        "q4_total": q4,
        "trend_desc": trend_desc,
        "trend_factor": round(trend_factor, 3),
        "all_lines": all_lines,
        "reason": reason,
        "why": why,
        "source": "screenshot-first + trend quarti",
        "extracted": ex
    }


def nobet(home, away, score, clock, reason, why, line="-", extracted=None):
    return {
        "signal": "NO_BET",
        "action": "NO BET",
        "decision_text": "Non scommettere",
        "side": None,
        "line": line,
        "stake": 0,
        "confidence": 0,
        "score": score,
        "clock": clock,
        "teams": {"home": home, "away": away},
        "rhythm": "non valutabile",
        "ppm": "-",
        "played": "-",
        "remaining": "-",
        "total_predicted": "-",
        "final_score": "-",
        "value": 0,
        "prob_over": 50,
        "prob_under": 50,
        "over_wait_line": "-",
        "under_wait_line": "-",
        "q1_total": None,
        "q2_total": None,
        "q3_total": None,
        "q4_total": None,
        "trend_desc": "-",
        "reason": reason,
        "why": why,
        "source": "screenshot-first",
        "extracted": extracted or {}
    }


def norm_team(s):
    s = (s or "").lower()
    repl = {
        "capo verde": "cape verde",
        "cabo verde": "cape verde",
        "sudan del sud": "south sudan"
    }
    for k, v in repl.items():
        s = s.replace(k, v)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def similarity(a, b):
    return SequenceMatcher(None, norm_team(a), norm_team(b)).ratio()


def basketball_api_get(path, params=None):
    if not BASKETBALL_API_KEY:
        raise RuntimeError("BASKETBALL_API_KEY missing")

    r = requests.get(
        f"{BASKETBALL_API_URL}{path}",
        headers={"x-apisports-key": BASKETBALL_API_KEY},
        params=params or {},
        timeout=20
    )
    r.raise_for_status()
    return r.json()


def game_total(game):
    scores = game.get("scores", {})
    home = scores.get("home", {})
    away = scores.get("away", {})

    h = home.get("total")
    a = away.get("total")

    if h is None:
        h = home.get("score")
    if a is None:
        a = away.get("score")

    try:
        return int(h), int(a)
    except Exception:
        return None, None


def get_live_games():
    data = basketball_api_get("/games", {"live": "all"})
    return data.get("response", [])


def find_matching_game(home_name, away_name, home_score=None, away_score=None):
    games = get_live_games()
    best = None
    best_score = 0

    for g in games:
        teams = g.get("teams", {})
        gh = teams.get("home", {}).get("name", "")
        ga = teams.get("away", {}).get("name", "")

        direct = similarity(home_name, gh) + similarity(away_name, ga)
        reverse = similarity(home_name, ga) + similarity(away_name, gh)

        score_match = 0
        api_h, api_a = game_total(g)

        if api_h is not None and api_a is not None and home_score is not None and away_score is not None:
            if api_h == int(home_score) and api_a == int(away_score):
                score_match += 0.5
            if api_h == int(away_score) and api_a == int(home_score):
                score_match += 0.5

        match_score = max(direct, reverse) + score_match

        if match_score > best_score:
            best_score = match_score
            best = g

    if best and best_score >= 1.15:
        return best, best_score

    return None, best_score


def evaluate_live_bet(total, side, line):
    side = (side or "").upper()
    line = float(line)

    if side == "OVER":
        if total > line:
            return "✅ in vantaggio"
        if total >= line - 8:
            return "⚠️ ancora viva ma al limite"
        return "❌ in difficoltà"

    if side == "UNDER":
        if total < line:
            return "✅ in vantaggio"
        if total <= line + 8:
            return "⚠️ ancora viva ma al limite"
        return "❌ in difficoltà"

    return "NO BET"


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "telegram": bool(TG_TOKEN and TG_CHAT),
        "openai": bool(OPENAI_API_KEY),
        "basketball_api": bool(BASKETBALL_API_KEY),
        "mode": "screenshot-first + trend quarti + live check"
    })


@app.route("/api/screenshot/analyze", methods=["POST"])
def shot():
    f = request.files.get("image")
    bankroll = float(request.form.get("bankroll") or 25)

    if not f:
        return jsonify({"error": "Nessuna immagine"}), 400

    ex = extract(f.read(), f.mimetype or "image/jpeg")
    decision = decide(ex, bankroll)

    return jsonify({
        "extracted": ex,
        "decision": decision,
        "mode": "screenshot-first + trend quarti"
    })


@app.route("/api/live/find", methods=["POST"])
def live_find():
    p = request.get_json(force=True)

    home = p.get("homeTeam")
    away = p.get("awayTeam")
    hs = p.get("homeScore")
    aw = p.get("awayScore")

    game, score = find_matching_game(home, away, hs, aw)

    if not game:
        return jsonify({
            "ok": False,
            "message": "Partita live non trovata",
            "match_score": round(score, 3)
        }), 404

    teams = game.get("teams", {})
    status = game.get("status", {})
    api_h, api_a = game_total(game)

    return jsonify({
        "ok": True,
        "game_id": game.get("id"),
        "home": teams.get("home", {}).get("name"),
        "away": teams.get("away", {}).get("name"),
        "score": f"{api_h}-{api_a}",
        "status": status,
        "match_score": round(score, 3),
        "message": "Partita agganciata correttamente"
    })


@app.route("/api/live/check-now", methods=["POST"])
def live_check_now():
    p = request.get_json(force=True)

    game_id = p.get("game_id")
    side = p.get("side")
    line = p.get("line")

    if not game_id:
        return jsonify({"ok": False, "error": "game_id mancante"}), 400

    data = basketball_api_get("/games", {"id": game_id})
    games = data.get("response", [])

    if not games:
        return jsonify({"ok": False, "error": "Partita non trovata nell'API"}), 404

    game = games[0]
    teams = game.get("teams", {})
    status = game.get("status", {})

    h, a = game_total(game)
    total = (h or 0) + (a or 0)

    bet_state = evaluate_live_bet(total, side, line) if side and line else "Nessuna giocata registrata"

    msg = (
        f"🏀 <b>{teams.get('home', {}).get('name')} - {teams.get('away', {}).get('name')}</b>\\n"
        f"Risultato live: <b>{h}-{a}</b>\\n"
        f"Totale attuale: <b>{total}</b>\\n"
        f"Stato partita: {status.get('long') or status.get('short') or '-'}\\n"
        f"Giocata: <b>{str(side).upper()} {line}</b>\\n"
        f"Situazione: <b>{bet_state}</b>"
    )

    tg(msg)

    return jsonify({
        "ok": True,
        "message": "Aggiornamento Telegram inviato",
        "score": f"{h}-{a}",
        "total": total,
        "bet_state": bet_state,
        "status": status
    })


@app.route("/api/telegram/test", methods=["POST"])
def telegram_test():
    tg("✅ MultiBasket AI PRO: notifiche Telegram attive.")
    return jsonify({"ok": True, "message": "Messaggio Telegram inviato."})


@app.route("/api/watch/start", methods=["POST"])
def watch():
    msg = "⚠️ Modalità screenshot-first: per aggiornare la giocata devi caricare un nuovo screenshot."
    tg(msg)
    return jsonify({"ok": True, "message": msg})


@app.route("/api/bet/register", methods=["POST"])
def bet():
    p = request.get_json(force=True)

    tg(
        f"✅ <b>Giocata registrata</b>\\n"
        f"{str(p.get('side')).upper()} {p.get('line')}\\n"
        f"Puntata: {p.get('stake')} €\\n"
        f"Fonte: screenshot-first."
    )

    return jsonify({"ok": True, "message": "Giocata registrata."})


@app.route("/api/bet/quality")
def quality():
    return jsonify({"message": "Aggiorna caricando un nuovo screenshot live."})


@app.route("/api/live/games")
def live_games():
    try:
        games = get_live_games()
        return jsonify({"count": len(games), "games": games})
    except Exception as e:
        return jsonify({"count": 0, "games": [], "error": str(e)}), 500


@app.route("/api/live/analyze", methods=["POST"])
def live_analyze():
    return jsonify({"error": "Usa Analizza screenshot."}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
