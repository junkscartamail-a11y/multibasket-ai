import os
from datetime import datetime

from flask import Flask, request, jsonify, send_from_directory

from ai import extract_from_screenshot
from prediction import decide
from api_basket import (
    api_ready,
    get_live_games,
    find_best_game,
    get_game_by_id,
    game_score_total,
    evaluate_bet_state,
)
from telegram_bot import telegram_ready, send_telegram
from history import add_bet, update_result, get_stats

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

app = Flask(
    __name__,
    static_folder="static",
    static_url_path="/static"
)


@app.route("/")
def home():
    return send_from_directory("static", "index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "openai": bool(OPENAI_API_KEY),
        "telegram": telegram_ready(),
        "basketball_api": api_ready(),
        "mode": "MultiBasket AI PRO 2.0"
    })


@app.route("/api/screenshot/analyze", methods=["POST"])
def screenshot_analyze():
    image_file = request.files.get("image")

    try:
        bankroll = float(request.form.get("bankroll") or 25)
    except (TypeError, ValueError):
        bankroll = 25

    if not image_file:
        return jsonify({
            "ok": False,
            "error": "Nessuna immagine caricata."
        }), 400

    try:
        raw_image = image_file.read()
        mime_type = image_file.mimetype or "image/jpeg"

        extracted = extract_from_screenshot(
            raw_image,
            mime_type
        )

        decision = decide(
            extracted,
            bankroll
        )

        return jsonify({
            "ok": True,
            "extracted": extracted,
            "decision": decision
        })

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500


@app.route("/api/live/list", methods=["GET"])
def live_list():
    try:
        games = get_live_games()
        simplified_games = []

        for game in games:
            home_score, away_score, total = game_score_total(game)

            teams = game.get("teams", {})
            status = game.get("status", {})
            league = game.get("league") or {}
            country = game.get("country") or {}

            home_name = (
                teams.get("home", {}).get("name")
                or "Squadra casa"
            )

            away_name = (
                teams.get("away", {}).get("name")
                or "Squadra ospite"
            )

            if home_score is not None and away_score is not None:
                score_text = f"{home_score}-{away_score}"
            else:
                score_text = "-"

            simplified_games.append({
                "id": game.get("id"),
                "home": home_name,
                "away": away_name,
                "score": score_text,
                "total": total,
                "status": (
                    status.get("long")
                    or status.get("short")
                    or "-"
                ),
                "league": league.get("name"),
                "country": country.get("name")
            })

        return jsonify({
            "ok": True,
            "count": len(simplified_games),
            "games": simplified_games
        })

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error),
            "games": []
        }), 500


@app.route("/api/live/find", methods=["POST"])
def live_find():
    payload = request.get_json(force=True) or {}

    home_team = payload.get("homeTeam") or ""
    away_team = payload.get("awayTeam") or ""
    home_score = payload.get("homeScore")
    away_score = payload.get("awayScore")

    try:
        result = find_best_game(
            home_team,
            away_team,
            home_score,
            away_score
        )

        if not result.get("found"):
            return jsonify({
                "ok": False,
                "message": "Partita live non trovata con sicurezza.",
                "best_score": result.get("best_score", 0),
                "candidates": result.get("candidates", []),
                "hint": (
                    "Controlla la lista delle partite live. "
                    "Se la gara non compare, API-Sports probabilmente "
                    "non sta coprendo quella competizione."
                )
            }), 404

        game = result["game"]

        home_api_score, away_api_score, total = game_score_total(game)

        teams = game.get("teams", {})
        status = game.get("status", {})

        home_api_name = (
            teams.get("home", {}).get("name")
            or home_team
        )

        away_api_name = (
            teams.get("away", {}).get("name")
            or away_team
        )

        if home_api_score is not None and away_api_score is not None:
            score_text = f"{home_api_score}-{away_api_score}"
        else:
            score_text = "-"

        return jsonify({
            "ok": True,
            "game_id": game.get("id"),
            "home": home_api_name,
            "away": away_api_name,
            "score": score_text,
            "total": total,
            "status": status,
            "match_score": result.get("best_score", 0),
            "candidates": result.get("candidates", []),
            "message": "Partita agganciata correttamente."
        })

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500


@app.route("/api/live/check-now", methods=["POST"])
def live_check_now():
    payload = request.get_json(force=True) or {}

    game_id = payload.get("game_id")
    side = str(payload.get("side") or "").upper()
    line = payload.get("line")
    stake = payload.get("stake")
    expected_score = payload.get("expectedScore")
    expected_total = payload.get("expectedTotal")
    confidence = payload.get("confidence")
    original_home = payload.get("homeTeam")
    original_away = payload.get("awayTeam")

    if not game_id:
        return jsonify({
            "ok": False,
            "error": "game_id mancante."
        }), 400

    try:
        game = get_game_by_id(game_id)

        if not game:
            return jsonify({
                "ok": False,
                "error": "Partita non trovata nell'API."
            }), 404

        home_score, away_score, total = game_score_total(game)

        teams = game.get("teams", {})
        status = game.get("status", {})

        home_name = (
            teams.get("home", {}).get("name")
            or original_home
            or "Squadra casa"
        )

        away_name = (
            teams.get("away", {}).get("name")
            or original_away
            or "Squadra ospite"
        )

        try:
            bet_state = evaluate_bet_state(
                total,
                side,
                line
            )
        except Exception:
            bet_state = "Stato giocata non calcolabile."

        now_text = datetime.now().strftime(
            "%d/%m/%Y %H:%M"
        )

        score_text = (
            f"{home_score}-{away_score}"
            if home_score is not None and away_score is not None
            else "-"
        )

        message = (
            f"📡 <b>AGGIORNAMENTO LIVE</b>\n\n"
            f"🏀 <b>{home_name} - {away_name}</b>\n"
            f"📅 Data e ora: <b>{now_text}</b>\n"
            f"⏱ Stato: <b>{status.get('long') or status.get('short') or '-'}</b>\n"
            f"🔢 Risultato attuale: <b>{score_text}</b>\n"
            f"📊 Totale attuale: <b>{total if total is not None else '-'}</b>\n\n"
            f"🎯 Giocata: <b>{side} {line}</b>\n"
            f"💶 Puntata: <b>{stake if stake is not None else '-'} €</b>\n"
            f"📈 Situazione: <b>{bet_state}</b>\n\n"
            f"🔮 Risultato atteso: <b>{expected_score or '-'}</b>\n"
            f"📊 Totale atteso: <b>{expected_total or '-'}</b>\n"
            f"🧠 Affidabilità iniziale: <b>{confidence if confidence is not None else '-'}/100</b>\n\n"
            f"📌 MultiBasket AI PRO 2.0"
        )

        telegram_sent = send_telegram(message)

        return jsonify({
            "ok": True,
            "telegram_sent": telegram_sent,
            "message": (
                "Aggiornamento Telegram inviato."
                if telegram_sent
                else "Aggiornamento calcolato, ma Telegram non è stato inviato."
            ),
            "score": score_text,
            "total": total,
            "bet_state": bet_state,
            "status": status
        })

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500


@app.route("/api/telegram/test", methods=["POST"])
def telegram_test():
    message = (
        "✅ <b>MultiBasket AI PRO 2.0</b>\n\n"
        "Telegram è configurato e funziona correttamente."
    )

    telegram_sent = send_telegram(message)

    if not telegram_sent:
        return jsonify({
            "ok": False,
            "message": (
                "Telegram non configurato. "
                "Controlla TELEGRAM_BOT_TOKEN e "
                "TELEGRAM_CHAT_ID su Render."
            )
        }), 400

    return jsonify({
        "ok": True,
        "message": "Messaggio Telegram inviato."
    })


@app.route("/api/bet/register", methods=["POST"])
def bet_register():
    payload = request.get_json(force=True) or {}

    home_team = (
        payload.get("homeTeam")
        or "Squadra A"
    )

    away_team = (
        payload.get("awayTeam")
        or "Squadra B"
    )

    side = str(
        payload.get("side")
        or "-"
    ).upper()

    line = payload.get("line")
    stake = payload.get("stake")
    bankroll = payload.get("bankroll")

    current_score = (
        payload.get("currentScore")
        or "-"
    )

    quarter_clock = (
        payload.get("quarterClock")
        or "-"
    )

    expected_score = (
        payload.get("expectedScore")
        or "-"
    )

    expected_total = (
        payload.get("expectedTotal")
        or "-"
    )

    confidence = payload.get("confidence")

    over_wait_line = payload.get("overWaitLine")
    under_wait_line = payload.get("underWaitLine")

    date_text = (
        payload.get("date")
        or datetime.now().strftime("%d/%m/%Y")
    )

    time_text = (
        payload.get("time")
        or datetime.now().strftime("%H:%M")
    )

    message = (
        f"✅ <b>GIOCATA REGISTRATA</b>\n\n"
        f"🏀 <b>{home_team} - {away_team}</b>\n"
        f"📅 Data: <b>{date_text}</b>\n"
        f"🕒 Ora: <b>{time_text}</b>\n"
        f"⏱ Situazione: <b>{current_score}</b> · <b>{quarter_clock}</b>\n\n"
        f"🎯 Giocata scelta: <b>{side} {line}</b>\n"
        f"💶 Puntata: <b>{stake} €</b>\n"
        f"💰 Bankroll: <b>{bankroll} €</b>\n\n"
        f"🔮 Risultato atteso: <b>{expected_score}</b>\n"
        f"📊 Totale atteso: <b>{expected_total}</b>\n"
        f"🧠 Affidabilità: <b>{confidence if confidence is not None else '-'}/100</b>\n\n"
        f"📉 Linea OVER conveniente: <b>{over_wait_line if over_wait_line is not None else '-'}</b>\n"
        f"📈 Linea UNDER conveniente: <b>{under_wait_line if under_wait_line is not None else '-'}</b>\n\n"
        f"📌 Fonte: MultiBasket AI PRO 2.0"
    )

    telegram_sent = send_telegram(message)
add_bet({
    "match": f"{home_team} - {away_team}",
    "side": side,
    "line": line,
    "stake": stake,
    "predicted_total": expected_total
})
    return jsonify({
        "ok": True,
        "telegram_sent": telegram_sent,
        "message": (
            "Giocata registrata e riepilogo inviato su Telegram."
            if telegram_sent
            else (
                "Giocata registrata, ma il riepilogo Telegram "
                "non è stato inviato."
            )
        )
    })
    
@app.route(
    "/api/recalculate",
    methods=["POST"]
)
def recalculate():
    payload = request.get_json(
        force=True
    ) or {}

    try:
        bankroll = float(
            payload.get(
                "bankroll"
            ) or 25
        )

    except (
        TypeError,
        ValueError
    ):
        bankroll = 25

    try:
        decision = decide(
            payload,
            bankroll
        )

        return jsonify({
            "ok": True,
            "decision": decision
        })

    except Exception as error:
        return jsonify({
            "ok": False,
            "error": str(error)
        }), 500
@app.route("/api/bet/result", methods=["POST"])
def bet_result():
    data = request.get_json()

    index = data.get("index")
    final_total = data.get("final_total")

    updated = update_result(index, final_total)

    if not updated:
        return jsonify({
            "ok": False,
            "error": "Indice non valido o dati mancanti"
        })

    return jsonify({
        "ok": True,
        "updated": updated
    })
@app.route("/api/stats", methods=["GET"])
def stats():
    return jsonify({
        "ok": True,
        "stats": get_stats()
    })
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 10000))
    )
