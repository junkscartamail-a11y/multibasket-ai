import os
import json
import re
import base64
import math
import sqlite3
import time
import threading
from datetime import datetime

import requests
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI


DB = "multibasket_pro.db"

API_KEY = os.getenv("API_BASKETBALL_KEY", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))

app = Flask(__name__, static_folder=".", static_url_path="")


def db():
    con = sqlite3.connect(DB, check_same_thread=False)
    con.execute(
        """CREATE TABLE IF NOT EXISTS watches(
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
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS bets(
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
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            fixture_id TEXT,
            message TEXT,
            created_at TEXT
        )"""
    )
    con.commit()
    return con


def log_event(kind, fixture_id, message):
    try:
        con = db()
        con.execute(
            "INSERT INTO events(kind, fixture_id, message, created_at) VALUES(?,?,?,?)",
            (kind, fixture_id, message, datetime.now().isoformat()),
        )
        con.commit()
        con.close()
    except Exception as exc:
        print("[LOG ERROR]", exc)


def tg_send(message):
    if not TG_TOKEN or not TG_CHAT:
        print("[TG MISSING]", message)
        return False

    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": TG_CHAT,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    response.raise_for_status()
    return True


def api_call(path, params):
    if not API_KEY:
        raise RuntimeError("API_BASKETBALL_KEY missing")

    response = requests.get(
        f"https://v1.basketball.api-sports.io/{path}",
        headers={"x-apisports-key": API_KEY},
        params=params,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def normalize_game(game):
    teams = game.get("teams", {}) or {}
    scores = game.get("scores", {}) or {}
    status = game.get("status", {}) or {}

    home_score = (scores.get("home", {}) or {}).get("total") or 0
    away_score = (scores.get("away", {}) or {}).get("total") or 0

    short = str(status.get("short") or "").upper()
    quarter = 4
    for ch in short:
        if ch.isdigit():
            quarter = int(ch)
            break
    if quarter < 1 or quarter > 4:
        quarter = 4

    return {
        "fixture_id": str(game.get("id")),
        "home": (teams.get("home", {}) or {}).get("name") or "Casa",
        "away": (teams.get("away", {}) or {}).get("name") or "Ospite",
        "home_score": int(home_score),
        "away_score": int(away_score),
        "quarter": quarter,
        "clock": status.get("timer") or status.get("clock") or status.get("elapsed") or "0:00",
        "status": status.get("long") or status.get("short") or "",
        "league": (game.get("league") or {}).get("name") or "",
    }


def get_fixture(fixture_id):
    data = api_call("games", {"id": fixture_id})
    response = data.get("response", [])
    if not response:
        raise ValueError("Fixture non trovata")
    return normalize_game(response[0])


def clock_minutes(clock):
    if clock is None:
        return 0.0
    text = str(clock).strip()
    try:
        if ":" in text:
            minutes, seconds = text.split(":")[:2]
            return int(minutes) + int(seconds) / 60
        return float(text)
    except Exception:
        return 0.0


def rhythm_label(pace):
    if pace < 2.4:
        return "Molto lento"
    if pace < 2.8:
        return "Lento"
    if pace < 3.2:
        return "Medio"
    if pace < 3.6:
        return "Veloce"
    return "Molto veloce"


def stake_from_bankroll(bankroll, confidence):
    if confidence >= 88:
        pct = 0.06
    elif confidence >= 80:
        pct = 0.045
    elif confidence >= 70:
        pct = 0.03
    elif confidence >= 65:
        pct = 0.02
    else:
        pct = 0
    return max(0, round(bankroll * pct))


def model_decision(live, bankroll, line, odds_over=1.85, odds_under=1.85):
    home_score = live["home_score"]
    away_score = live["away_score"]
    total_now = home_score + away_score

    quarter = int(live["quarter"])
    left = clock_minutes(live["clock"])
    played = max(0.1, (quarter - 1) * 10 + (10 - left))
    remaining = max(0, 40 - played)

    pace = total_now / played
    phase_factor = {1: 0.98, 2: 0.96, 3: 0.94, 4: 0.90}.get(quarter, 0.90)
    adjusted_pace = pace * phase_factor
    adjusted_pace = max(1.65, min(5.05 if quarter == 4 else 5.35, adjusted_pace))

    raw_total = total_now + remaining * adjusted_pace
    correction = max(-3, min(3, (line - raw_total) * 0.10))
    predicted_total = round(raw_total + correction)
    value = predicted_total - line

    share = home_score / total_now if total_now else 0.5
    final_home = round(predicted_total * share)
    final_away = round(predicted_total - final_home)

    prob_over = round(100 / (1 + math.exp(-value / 5.2)))
    prob_under = 100 - prob_over

    threshold = 8 if played >= 8 else 10
    side = None
    if value >= threshold:
        side = "OVER"
    elif value <= -threshold:
        side = "UNDER"

    confidence = 0
    if side:
        confidence = round(
            min(
                100,
                min(55, abs(value) * 4)
                + min(20, played / 40 * 28)
                + 15
                + (10 if bankroll >= 20 else 5),
            )
        )

    stake = stake_from_bankroll(bankroll, confidence)
    signal = "BET" if side and confidence >= 65 and stake > 0 else (
        "OBSERVE" if abs(value) >= 4 else "NO_BET"
    )

    if signal == "BET":
        reason = f"{side} con valore {value:+.1f}: totale previsto {predicted_total}, linea {line}."
    elif signal == "OBSERVE":
        reason = f"Osserva: valore {value:+.1f}, non ancora sufficiente."
    else:
        reason = f"No bet: valore {value:+.1f} insufficiente rispetto alla linea {line}."

    return {
        "signal": signal,
        "side": side,
        "line": line,
        "stake": stake,
        "confidence": confidence,
        "score": f"{home_score}-{away_score}",
        "clock": f"{live['clock']} Q{quarter}",
        "teams": {"home": live["home"], "away": live["away"]},
        "league": live["league"],
        "rhythm": rhythm_label(pace),
        "total_predicted": predicted_total,
        "final_score": f"{final_home}-{final_away}",
        "value": round(value, 1),
        "prob_over": prob_over,
        "prob_under": prob_under,
        "win_probability": prob_over if side == "OVER" else prob_under if side == "UNDER" else max(prob_over, prob_under),
        "reason": reason,
    }


def quality_for_bet(side, decision):
    probability = decision["prob_over"] if side.upper() == "OVER" else decision["prob_under"]
    if probability >= 80:
        quality = "Ottima"
    elif probability >= 65:
        quality = "Buona"
    elif probability >= 52:
        quality = "In bilico"
    else:
        quality = "A rischio"
    return probability, quality


def telegram_signal_message(decision):
    side = decision.get("side") or "NO BET"
    return (
        f"🏀 <b>MultiBasket AI PRO</b>\n\n"
        f"{decision['teams']['home']} - {decision['teams']['away']}\n"
        f"Punteggio: <b>{decision['score']}</b>\n"
        f"Tempo: <b>{decision['clock']}</b>\n\n"
        f"Segnale: <b>{side} {decision['line'] if decision.get('side') else ''}</b>\n"
        f"Puntata: <b>{decision['stake']} €</b>\n"
        f"Affidabilità: <b>{decision['confidence']}/100</b>\n"
        f"Totale previsto: <b>{decision['total_predicted']}</b>\n"
        f"Finale stimato: <b>{decision['final_score']}</b>\n"
        f"Valore: <b>{decision['value']:+.1f}</b>\n"
        f"Ritmo: <b>{decision['rhythm']}</b>\n\n"
        f"{decision['reason']}"
    )


def telegram_bet_message(side, line, stake, decision):
    probability, quality = quality_for_bet(side, decision)
    return (
        f"📊 <b>Aggiornamento giocata</b>\n\n"
        f"{decision['teams']['home']} - {decision['teams']['away']}\n"
        f"Punteggio: <b>{decision['score']}</b>\n"
        f"Tempo: <b>{decision['clock']}</b>\n\n"
        f"La tua giocata: <b>{side.upper()} {line}</b>\n"
        f"Puntata: <b>{stake} €</b>\n"
        f"Totale previsto ora: <b>{decision['total_predicted']}</b>\n"
        f"Probabilità stimata di vincita: <b>{probability}%</b>\n"
        f"Qualità: <b>{quality}</b>"
    ), probability, quality


def parse_json_text(text):
    clean = (text or "").replace("```json", "").replace("```", "").strip()
    match = re.search(r"\{.*\}", clean, re.S)
    return json.loads(match.group(0) if match else clean)


def extract_from_screenshot(raw_bytes, mime):
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")

    image_b64 = base64.b64encode(raw_bytes).decode("utf-8")
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = """Estrai dati da screenshot basket live bookmaker.
Rispondi SOLO JSON valido, senza testo aggiuntivo:
{
  "homeTeam": string|null,
  "awayTeam": string|null,
  "homeScore": number|null,
  "awayScore": number|null,
  "quarter": number|null,
  "timeRemaining": "M:SS"|null,
  "lineOU": number|null,
  "oddsOver": number|null,
  "oddsUnder": number|null,
  "confidence": number
}
Non inventare dati non visibili.
La linea O/U è la linea punti totali del bookmaker.
"""

    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Leggi questo screenshot e restituisci solo il JSON richiesto."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                ],
            },
        ],
        temperature=0,
    )

    return parse_json_text(response.choices[0].message.content)


def find_matching_live_game(extracted):
    data = api_call("games", {"live": "all"})
    games = [normalize_game(game) for game in data.get("response", [])]

    home_text = (extracted.get("homeTeam") or "").lower()
    away_text = (extracted.get("awayTeam") or "").lower()

    best = None
    best_score = 0

    for game in games:
        score = 0
        game_home = (game.get("home") or "").lower()
        game_away = (game.get("away") or "").lower()

        if home_text and (home_text in game_home or game_home in home_text):
            score += 3
        if away_text and (away_text in game_away or game_away in away_text):
            score += 3

        if extracted.get("homeScore") is not None:
            try:
                if int(extracted["homeScore"]) == game["home_score"]:
                    score += 1
            except Exception:
                pass

        if extracted.get("awayScore") is not None:
            try:
                if int(extracted["awayScore"]) == game["away_score"]:
                    score += 1
            except Exception:
                pass

        if score > best_score:
            best_score = score
            best = game

    return best if best_score >= 3 else None


def monitor_loop():
    print("[MONITOR] started")
    while True:
        try:
            con = db()

            watches = con.execute(
                "SELECT fixture_id,bankroll,line,odds_over,odds_under,last_signal,last_confidence FROM watches WHERE active=1"
            ).fetchall()

            for fixture_id, bankroll, line, odds_over, odds_under, last_signal, last_confidence in watches:
                try:
                    decision = model_decision(get_fixture(fixture_id), bankroll, line, odds_over, odds_under)
                    if decision["signal"] == "BET" and (
                        last_signal != "BET"
                        or abs(decision["confidence"] - int(last_confidence or 0)) >= 8
                    ):
                        message = telegram_signal_message(decision)
                        tg_send(message)
                        log_event("SIGNAL", fixture_id, message)

                    con.execute(
                        "UPDATE watches SET last_signal=?, last_confidence=?, last_probability=? WHERE fixture_id=?",
                        (decision["signal"], decision["confidence"], decision["win_probability"], fixture_id),
                    )
                except Exception as exc:
                    print("[WATCH ERROR]", fixture_id, exc)

            bets = con.execute(
                "SELECT id,fixture_id,side,line,stake,bankroll,last_probability,last_quality FROM bets WHERE active=1"
            ).fetchall()

            for bet_id, fixture_id, side, line, stake, bankroll, last_probability, last_quality in bets:
                try:
                    decision = model_decision(get_fixture(fixture_id), bankroll, line)
                    message, probability, quality = telegram_bet_message(side, line, stake, decision)

                    should_notify = (
                        not last_quality
                        or abs(probability - int(last_probability or 0)) >= 10
                        or quality != last_quality
                    )

                    if should_notify:
                        tg_send(message)
                        log_event("BET_QUALITY", fixture_id, message)

                    con.execute(
                        "UPDATE bets SET last_probability=?, last_quality=? WHERE id=?",
                        (probability, quality, bet_id),
                    )
                except Exception as exc:
                    print("[BET ERROR]", fixture_id, exc)

            con.commit()
            con.close()
        except Exception as exc:
            print("[MONITOR ERROR]", exc)

        time.sleep(CHECK_INTERVAL_SECONDS)


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "api_basketball": bool(API_KEY),
            "telegram": bool(TG_TOKEN and TG_CHAT),
            "openai": bool(OPENAI_API_KEY),
            "interval": CHECK_INTERVAL_SECONDS,
        }
    )


@app.route("/api/live/games")
def live_games():
    data = api_call("games", {"live": "all"})
    games = [normalize_game(game) for game in data.get("response", [])]
    return jsonify({"count": len(games), "games": games})


@app.route("/api/live/analyze", methods=["POST"])
def analyze_live():
    payload = request.get_json(force=True)
    decision = model_decision(
        get_fixture(payload["fixture_id"]),
        float(payload["bankroll"]),
        float(payload["line"]),
        float(payload.get("odds_over", 1.85)),
        float(payload.get("odds_under", 1.85)),
    )
    return jsonify(decision)


@app.route("/api/screenshot/analyze", methods=["POST"])
def screenshot_analyze():
    uploaded = request.files.get("image")
    if not uploaded:
        return jsonify({"error": "Nessuna immagine ricevuta"}), 400

    raw = uploaded.read()
    mime = uploaded.mimetype or "image/jpeg"
    extracted = extract_from_screenshot(raw, mime)

    match = None
    try:
        match = find_matching_live_game(extracted)
    except Exception as exc:
        print("[MATCH ERROR]", exc)

    return jsonify({"extracted": extracted, "match": match})


@app.route("/api/watch/start", methods=["POST"])
def watch_start():
    payload = request.get_json(force=True)

    con = db()
    con.execute(
        """INSERT OR REPLACE INTO watches(
            fixture_id,bankroll,line,odds_over,odds_under,active,created_at,last_signal,last_confidence,last_probability
        ) VALUES(?,?,?,?,?,1,?,?,0,0)""",
        (
            payload["fixture_id"],
            float(payload["bankroll"]),
            float(payload["line"]),
            float(payload.get("odds_over", 1.85)),
            float(payload.get("odds_under", 1.85)),
            datetime.now().isoformat(),
            None,
        ),
    )
    con.commit()
    con.close()

    tg_send(
        f"✅ <b>Monitoraggio avviato</b>\n\n"
        f"Fixture ID: <b>{payload['fixture_id']}</b>\n"
        f"Linea: <b>{payload['line']}</b>\n"
        f"Bankroll: <b>{payload['bankroll']} €</b>\n\n"
        f"Ti avviso se compare un segnale valido."
    )

    return jsonify({"ok": True, "message": "Monitoraggio avviato. Telegram attivo."})


@app.route("/api/bet/register", methods=["POST"])
def bet_register():
    payload = request.get_json(force=True)

    con = db()
    con.execute(
        "INSERT INTO bets(fixture_id,side,line,stake,bankroll,active,created_at) VALUES(?,?,?,?,?,1,?)",
        (
            payload["fixture_id"],
            payload["side"].upper(),
            float(payload["line"]),
            float(payload["stake"]),
            float(payload["bankroll"]),
            datetime.now().isoformat(),
        ),
    )
    con.commit()
    con.close()

    try:
        decision = model_decision(
            get_fixture(payload["fixture_id"]),
            float(payload["bankroll"]),
            float(payload["line"]),
        )
        message, _, _ = telegram_bet_message(
            payload["side"],
            float(payload["line"]),
            float(payload["stake"]),
            decision,
        )
        tg_send("✅ <b>Giocata registrata</b>\n\n" + message)
        log_event("BET_REGISTERED", payload["fixture_id"], message)
    except Exception:
        tg_send(
            f"✅ <b>Giocata registrata</b>\n\n"
            f"{payload['side'].upper()} {payload['line']}\n"
            f"Puntata: {payload['stake']} €\n"
            f"Monitoraggio attivo."
        )

    return jsonify({"ok": True, "message": "Giocata registrata. Riceverai notifiche Telegram."})


@app.route("/api/bet/quality")
def bet_quality():
    fixture_id = request.args.get("fixture_id")

    con = db()
    row = con.execute(
        "SELECT side,line,stake,bankroll FROM bets WHERE fixture_id=? AND active=1 ORDER BY id DESC LIMIT 1",
        (fixture_id,),
    ).fetchone()
    con.close()

    if not row:
        return jsonify({"error": "Nessuna giocata attiva."})

    side, line, stake, bankroll = row
    decision = model_decision(get_fixture(fixture_id), bankroll, line)
    _, probability, quality = telegram_bet_message(side, line, stake, decision)

    return jsonify(
        {
            "score": decision["score"],
            "clock": decision["clock"],
            "total_predicted": decision["total_predicted"],
            "win_probability": probability,
            "quality": quality,
            "message": f"La partita è {decision['score']} a {decision['clock']}. La tua giocata {side} {line} ha probabilità stimata {probability}%: qualità {quality}.",
        }
    )


@app.route("/api/telegram/test", methods=["POST"])
def telegram_test():
    tg_send("✅ MultiBasket AI PRO: notifiche Telegram attive.")
    return jsonify({"ok": True, "message": "Messaggio Telegram inviato."})


@app.route("/api/events")
def events():
    con = db()
    rows = con.execute(
        "SELECT kind,fixture_id,message,created_at FROM events ORDER BY id DESC LIMIT 50"
    ).fetchall()
    con.close()

    return jsonify(
        {
            "events": [
                {"kind": row[0], "fixture_id": row[1], "message": row[2], "created_at": row[3]}
                for row in rows
            ]
        }
    )


try:
    db().close()
    threading.Thread(target=monitor_loop, daemon=True).start()
except Exception as exc:
    print("[STARTUP ERROR]", exc)
