import os, sqlite3, math, time, threading
from datetime import datetime
import requests
from flask import Flask, request, jsonify, send_from_directory

DB = "multibasket_pro.db"
API_BASKETBALL_KEY = os.getenv("API_BASKETBALL_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))

app = Flask(__name__, static_folder=".", static_url_path="")

def connect():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.execute("""CREATE TABLE IF NOT EXISTS watches(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id TEXT UNIQUE,
        bankroll REAL,
        line REAL,
        odds_over REAL,
        odds_under REAL,
        active INTEGER DEFAULT 1,
        last_signal TEXT,
        last_confidence INTEGER DEFAULT 0,
        last_probability INTEGER DEFAULT 0,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS bets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fixture_id TEXT,
        side TEXT,
        line REAL,
        stake REAL,
        bankroll REAL,
        active INTEGER DEFAULT 1,
        last_probability INTEGER DEFAULT 0,
        last_quality TEXT,
        created_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT,
        fixture_id TEXT,
        message TEXT,
        created_at TEXT
    )""")
    c.commit()
    return c

def api_call(path, params):
    if not API_BASKETBALL_KEY:
        raise RuntimeError("API_BASKETBALL_KEY missing")
    url = f"https://v1.basketball.api-sports.io/{path}"
    r = requests.get(url, headers={"x-apisports-key": API_BASKETBALL_KEY}, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def tg_send(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM NOT CONFIGURED]", message)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}, timeout=20)
    r.raise_for_status()
    return True

def log_event(kind, fixture_id, message):
    try:
        c = connect()
        c.execute("INSERT INTO events(kind,fixture_id,message,created_at) VALUES(?,?,?,?)",
                  (kind, fixture_id, message, datetime.now().isoformat()))
        c.commit()
        c.close()
    except Exception as e:
        print("log error", e)

def clock_minutes(clock):
    if clock is None:
        return 0.0
    s = str(clock).strip()
    try:
        if ":" in s:
            m, sec = s.split(":")[:2]
            return int(m) + int(sec) / 60
        return float(s)
    except Exception:
        return 0.0

def normalize_game(g):
    teams = g.get("teams", {}) or {}
    scores = g.get("scores", {}) or {}
    status = g.get("status", {}) or {}
    hs = (scores.get("home", {}) or {}).get("total") or 0
    aw = (scores.get("away", {}) or {}).get("total") or 0
    short = str(status.get("short") or "").upper()
    q = 4
    for ch in short:
        if ch.isdigit():
            q = int(ch)
            break
    if q < 1 or q > 4:
        q = 4
    return {
        "fixture_id": str(g.get("id")),
        "home": (teams.get("home", {}) or {}).get("name") or "Casa",
        "away": (teams.get("away", {}) or {}).get("name") or "Ospite",
        "home_score": int(hs),
        "away_score": int(aw),
        "quarter": q,
        "clock": status.get("timer") or status.get("clock") or status.get("elapsed") or "0:00",
        "status": status.get("long") or status.get("short") or "",
        "league": (g.get("league") or {}).get("name") or "",
    }

def get_fixture(fixture_id):
    data = api_call("games", {"id": fixture_id})
    resp = data.get("response", [])
    if not resp:
        raise ValueError("Fixture non trovata")
    return normalize_game(resp[0])

def logistic(x):
    return 1 / (1 + math.exp(-x))

def rhythm_label(pace):
    if pace < 2.4: return "Molto lento"
    if pace < 2.8: return "Lento"
    if pace < 3.2: return "Medio"
    if pace < 3.6: return "Veloce"
    return "Molto veloce"

def stake_from_bankroll(bankroll, confidence):
    if confidence >= 88: pct = 0.06
    elif confidence >= 80: pct = 0.045
    elif confidence >= 70: pct = 0.03
    elif confidence >= 65: pct = 0.02
    else: pct = 0
    return max(0, round(bankroll * pct))

def model(live, bankroll, line, odds_over=1.85, odds_under=1.85):
    h = live["home_score"]
    a = live["away_score"]
    total = h + a
    q = int(live["quarter"])
    left = clock_minutes(live["clock"])
    played = max(0.1, (q - 1) * 10 + (10 - left))
    remaining = max(0, 40 - played)
    pace = total / played
    phase = {1: 0.98, 2: 0.96, 3: 0.94, 4: 0.90}.get(q, 0.90)
    adjusted_pace = max(1.65, min(5.05 if q == 4 else 5.35, pace * phase))
    raw_total = total + remaining * adjusted_pace
    correction = max(-3, min(3, (line - raw_total) * 0.10))
    predicted = round(raw_total + correction)
    value = predicted - line
    share = h / total if total else 0.5
    final_home = round(predicted * share)
    final_away = round(predicted - final_home)
    prob_over = round(logistic(value / 5.2) * 100)
    prob_under = 100 - prob_over
    threshold = 8 if played >= 8 else 10
    side = None
    if value >= threshold: side = "OVER"
    elif value <= -threshold: side = "UNDER"
    confidence = 0
    if side:
        confidence = round(min(100, min(55, abs(value) * 4) + min(20, played / 40 * 28) + 15 + (10 if bankroll >= 20 else 5)))
    stake = stake_from_bankroll(bankroll, confidence)
    signal = "BET" if side and confidence >= 65 and stake > 0 else ("OBSERVE" if abs(value) >= 4 else "NO_BET")
    reason = f"{side} con valore {value:+.1f}: totale previsto {predicted}, linea {line}." if signal == "BET" else f"Osserva: valore {value:+.1f}, non ancora sufficiente." if signal == "OBSERVE" else f"No bet: valore {value:+.1f} insufficiente rispetto alla linea {line}."
    return {
        "signal": signal, "side": side, "line": line, "stake": stake, "confidence": confidence,
        "score": f"{h}-{a}", "clock": f"{live['clock']} Q{q}",
        "teams": {"home": live["home"], "away": live["away"]}, "league": live["league"],
        "rhythm": rhythm_label(pace), "total_predicted": predicted, "final_score": f"{final_home}-{final_away}",
        "value": round(value, 1), "prob_over": prob_over, "prob_under": prob_under,
        "win_probability": prob_over if side == "OVER" else prob_under if side == "UNDER" else max(prob_over, prob_under),
        "reason": reason,
    }

def quality_for_bet(side, dec):
    p = dec["prob_over"] if side.upper() == "OVER" else dec["prob_under"]
    q = "Ottima" if p >= 80 else "Buona" if p >= 65 else "In bilico" if p >= 52 else "A rischio"
    return p, q

def format_signal(dec):
    side = dec.get("side") or "NO BET"
    return (
        f"🏀 <b>MultiBasket AI PRO</b>\n\n"
        f"{dec['teams']['home']} - {dec['teams']['away']}\n"
        f"Punteggio: <b>{dec['score']}</b>\n"
        f"Tempo: <b>{dec['clock']}</b>\n\n"
        f"Segnale: <b>{side} {dec['line'] if dec.get('side') else ''}</b>\n"
        f"Puntata: <b>{dec['stake']} €</b>\n"
        f"Affidabilità: <b>{dec['confidence']}/100</b>\n"
        f"Totale previsto: <b>{dec['total_predicted']}</b>\n"
        f"Finale stimato: <b>{dec['final_score']}</b>\n"
        f"Valore: <b>{dec['value']:+.1f}</b>\n"
        f"Ritmo: <b>{dec['rhythm']}</b>\n\n"
        f"{dec['reason']}"
    )

def monitor_loop():
    print("[MONITOR] started")
    while True:
        try:
            c = connect()
            watches = c.execute("SELECT fixture_id,bankroll,line,odds_over,odds_under,last_signal,last_confidence FROM watches WHERE active=1").fetchall()
            for fixture_id, bankroll, line, odds_o, odds_u, last_signal, last_conf in watches:
                try:
                    dec = model(get_fixture(fixture_id), bankroll, line, odds_o, odds_u)
                    if dec["signal"] == "BET" and (last_signal != "BET" or abs(dec["confidence"] - int(last_conf or 0)) >= 8):
                        msg = format_signal(dec)
                        tg_send(msg)
                        log_event("SIGNAL", fixture_id, msg)
                    c.execute("UPDATE watches SET last_signal=?,last_confidence=?,last_probability=? WHERE fixture_id=?",
                              (dec["signal"], dec["confidence"], dec["win_probability"], fixture_id))
                except Exception as e:
                    print("[WATCH ERROR]", fixture_id, e)

            bets = c.execute("SELECT id,fixture_id,side,line,stake,bankroll,last_probability,last_quality FROM bets WHERE active=1").fetchall()
            for bet_id, fixture_id, side, line, stake, bankroll, last_prob, last_quality in bets:
                try:
                    dec = model(get_fixture(fixture_id), bankroll, line)
                    p, q = quality_for_bet(side, dec)
                    notify = (not last_quality) or abs(p - int(last_prob or 0)) >= 10 or q != last_quality
                    if notify:
                        msg = (
                            f"📊 <b>Aggiornamento giocata</b>\n\n"
                            f"{dec['teams']['home']} - {dec['teams']['away']}\n"
                            f"Punteggio: <b>{dec['score']}</b>\n"
                            f"Tempo: <b>{dec['clock']}</b>\n\n"
                            f"La tua giocata: <b>{side.upper()} {line}</b>\n"
                            f"Puntata: <b>{stake} €</b>\n"
                            f"Totale previsto ora: <b>{dec['total_predicted']}</b>\n"
                            f"Probabilità stimata di vincita: <b>{p}%</b>\n"
                            f"Qualità: <b>{q}</b>"
                        )
                        tg_send(msg)
                        log_event("BET_QUALITY", fixture_id, msg)
                    c.execute("UPDATE bets SET last_probability=?,last_quality=? WHERE id=?", (p, q, bet_id))
                except Exception as e:
                    print("[BET ERROR]", fixture_id, e)
            c.commit()
            c.close()
        except Exception as e:
            print("[MONITOR ERROR]", e)
        time.sleep(CHECK_INTERVAL_SECONDS)

@app.route("/")
def home():
    return send_from_directory(".", "index.html")

@app.route("/api/health")
def health():
    return jsonify({"ok": True, "api_basketball": bool(API_BASKETBALL_KEY), "telegram": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID), "interval": CHECK_INTERVAL_SECONDS})

@app.route("/api/live/games")
def live_games():
    data = api_call("games", {"live": "all"})
    games = [normalize_game(g) for g in data.get("response", [])]
    return jsonify({"count": len(games), "games": games})

@app.route("/api/live/analyze", methods=["POST"])
def analyze_live():
    req = request.get_json(force=True)
    dec = model(get_fixture(req["fixture_id"]), float(req["bankroll"]), float(req["line"]), float(req.get("odds_over", 1.85)), float(req.get("odds_under", 1.85)))
    return jsonify(dec)

@app.route("/api/watch/start", methods=["POST"])
def watch_start():
    req = request.get_json(force=True)
    c = connect()
    c.execute("""INSERT OR REPLACE INTO watches(fixture_id,bankroll,line,odds_over,odds_under,active,created_at,last_signal,last_confidence,last_probability)
                 VALUES(?,?,?,?,?,1,?,?,0,0)""",
              (req["fixture_id"], float(req["bankroll"]), float(req["line"]), float(req.get("odds_over", 1.85)), float(req.get("odds_under", 1.85)), datetime.now().isoformat(), None))
    c.commit(); c.close()
    return jsonify({"ok": True, "message": "Monitoraggio automatico avviato. Riceverai notifiche Telegram se compare un segnale."})

@app.route("/api/bet/register", methods=["POST"])
def bet_register():
    req = request.get_json(force=True)
    c = connect()
    c.execute("INSERT INTO bets(fixture_id,side,line,stake,bankroll,active,created_at) VALUES(?,?,?,?,?,1,?)",
              (req["fixture_id"], req["side"].upper(), float(req["line"]), float(req["stake"]), float(req["bankroll"]), datetime.now().isoformat()))
    c.commit(); c.close()
    return jsonify({"ok": True, "message": "Giocata registrata. Riceverai notifiche Telegram sulla qualità della puntata."})

@app.route("/api/bet/quality")
def bet_quality():
    fixture_id = request.args.get("fixture_id")
    c = connect()
    row = c.execute("SELECT side,line,stake,bankroll FROM bets WHERE fixture_id=? AND active=1 ORDER BY id DESC LIMIT 1", (fixture_id,)).fetchone()
    c.close()
    if not row:
        return jsonify({"error": "Nessuna giocata attiva."})
    side, line, stake, bankroll = row
    dec = model(get_fixture(fixture_id), bankroll, line)
    p, q = quality_for_bet(side, dec)
    return jsonify({"score": dec["score"], "clock": dec["clock"], "total_predicted": dec["total_predicted"], "win_probability": p, "quality": q, "message": f"La partita è {dec['score']} a {dec['clock']}. La tua giocata {side} {line} ha probabilità stimata {p}%: qualità {q}."})

@app.route("/api/telegram/test", methods=["POST"])
def telegram_test():
    tg_send("✅ MultiBasket AI PRO: notifiche Telegram attive.")
    return jsonify({"ok": True, "message": "Messaggio Telegram inviato."})

@app.route("/api/events")
def events():
    c = connect()
    rows = c.execute("SELECT kind,fixture_id,message,created_at FROM events ORDER BY id DESC LIMIT 50").fetchall()
    c.close()
    return jsonify({"events": [{"kind": r[0], "fixture_id": r[1], "message": r[2], "created_at": r[3]} for r in rows]})

try:
    connect().close()
    threading.Thread(target=monitor_loop, daemon=True).start()
except Exception as e:
    print("[STARTUP ERROR]", e)
