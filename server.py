
import os, json, re, base64, math, requests, time
import pandas as pd
from io import BytesIO
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

app = Flask(__name__, static_folder=".", static_url_path="")

STORICO_CACHE = {"url": None, "time": 0, "data": None}
CACHE_SECONDS = 300


def onedrive_download_url(url):
    encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    return f"https://api.onedrive.com/v1.0/shares/u!{encoded}/root/content"


def leggi_storico_da_onedrive(url):
    now = time.time()

    if (
        STORICO_CACHE["url"] == url
        and STORICO_CACHE["data"] is not None
        and now - STORICO_CACHE["time"] < CACHE_SECONDS
    ):
        return STORICO_CACHE["data"]

    download_url = onedrive_download_url(url)

    r = requests.get(download_url, timeout=30)
    r.raise_for_status()

    excel_file = BytesIO(r.content)

    df = pd.read_excel(excel_file, sheet_name="STORICO")
    df = df.fillna("")

    data = df.to_dict(orient="records")

    STORICO_CACHE["url"] = url
    STORICO_CACHE["time"] = now
    STORICO_CACHE["data"] = data

    return data


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

    prompt = """Estrai dati da screenshot basket live bookmaker. Rispondi SOLO JSON valido:
{"homeTeam":string|null,"awayTeam":string|null,"homeScore":number|null,"awayScore":number|null,"quarter":number|null,"timeRemaining":"M:SS"|null,"lineOU":number|null,"oddsOver":number|null,"oddsUnder":number|null,"confidence":number}
Non inventare dati non visibili. timeRemaining è il tempo rimanente nel quarto."""

    r = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Leggi lo screenshot."},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{b64}"
                        }
                    }
                ]
            }
        ],
        temperature=0
    )

    return parse_json(r.choices[0].message.content)


def clock_min(x):
    try:
        s = str(x or "0:00").strip()
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


def stake(bankroll, conf):
    if conf < 68:
        return 0

    pct = .05 if conf >= 86 else .035 if conf >= 78 else .025

    return max(1, round(bankroll * pct))


def decide(ex, bankroll):
    home = ex.get("homeTeam") or "Squadra A"
    away = ex.get("awayTeam") or "Squadra B"

    need = ["homeScore", "awayScore", "quarter", "timeRemaining", "lineOU"]
    miss = [k for k in need if ex.get(k) is None]

    if miss:
        return nobet(
            home,
            away,
            "-",
            "-",
            "Dati insufficienti nello screenshot: devono essere leggibili punteggio, quarto, cronometro e linea bookmaker.",
            [
                "Screenshot incompleto",
                "Non faccio pronostici inventati",
                "Puntata consigliata 0 €"
            ]
        )

    h = int(ex["homeScore"])
    a = int(ex["awayScore"])
    total = h + a
    q = int(ex["quarter"])
    left = clock_min(ex["timeRemaining"])
    line = float(ex["lineOU"])

    if left is None or q < 1 or q > 4 or left < 0 or left > 10:
        return nobet(
            home,
            away,
            f"{h}-{a}",
            f"{ex.get('timeRemaining')} Q{q}",
            "Cronometro o quarto non affidabili: senza tempo corretto la stima sarebbe casuale.",
            [
                "Tempo non validato",
                "Rischio calcolo alto",
                "Puntata consigliata 0 €"
            ],
            line
        )

    played = (q - 1) * 10 + (10 - left)
    rem = max(0, 40 - played)

    if played <= 0:
        played = .1

    ppm = total / played

    phase = {1: .97, 2: .95, 3: .93, 4: .88}.get(q, .90)
    fatigue = .96 if q >= 3 else 1
    blowout = .91 if abs(h - a) >= 22 and q >= 3 else 1
    close = .03 if abs(h - a) <= 8 and q >= 4 else 0

    corr_ppm = max(1.70, min(4.85, ppm * phase * fatigue * blowout * (1 + close)))
    raw = total + rem * corr_ppm

    shrink = .82 if played < 20 else .88

    pred = round(line + (raw - line) * shrink)
    value = pred - line

    po = prob(value)
    pu = 100 - po

    side = "OVER" if value >= 8 else "UNDER" if value <= -8 else None

    conf = 0
    conf += 22
    conf += 18 if ex.get("oddsOver") and ex.get("oddsUnder") else 8
    conf += 20 if played >= 8 else 8
    conf += 20 if abs(value) >= 10 else 10 if abs(value) >= 7 else 0
    conf += 8 if home != "Squadra A" and away != "Squadra B" else 0

    if played < 5:
        conf -= 10

    conf = max(0, min(88, round(conf)))

    st = stake(bankroll, conf) if side else 0

    share = h / total if total else .5
    fh = round(pred * share)
    fa = round(pred - fh)

    if side and conf >= 68 and st > 0:
        signal = "BET"
        action = f"GIOCA {side}"
        text = f"Scommetti {side} {line}"
        reason = (
            f"Stima screenshot-first: totale previsto {pred} contro linea {line}, "
            f"valore {value:+.1f}. Ritmo {ritmo(ppm)}, giocati {played:.1f} minuti, "
            f"restano {rem:.1f}. Stake prudente perché la fonte è lo screenshot."
        )
        why = [
            f"Ritmo attuale: {ritmo(ppm)}",
            f"Scostamento dalla linea: {value:+.1f}",
            f"Totale previsto realistico: {pred}",
            "Puntata ridotta perché non c'è feed live continuo"
        ]

    elif abs(value) >= 5:
        signal = "OBSERVE"
        action = "OSSERVA"
        text = "Aspetta o rifai screenshot tra 1-2 minuti"
        reason = (
            f"C'è valore teorico {value:+.1f}, ma affidabilità {conf}/100: "
            f"non basta per giocare soldi veri."
        )
        why = [
            "Valore presente ma non solido",
            "Mancano dati su falli, possessi e ritmo ultimi minuti",
            "Puntata consigliata 0 €"
        ]

    else:
        signal = "NO_BET"
        action = "NON GIOCARE"
        text = "Non scommettere"
        reason = f"Valore {value:+.1f}: troppo debole o incerto. La scelta realistica è non giocare."
        why = [
            "Edge insufficiente",
            "Rischio non compensato",
            "Puntata consigliata 0 €"
        ]

    return {
        "signal": signal,
        "action": action,
        "decision_text": text,
        "side": side,
        "line": line,
        "stake": st,
        "confidence": conf,
        "score": f"{h}-{a}",
        "clock": f"{ex.get('timeRemaining')} Q{q}",
        "teams": {
            "home": home,
            "away": away
        },
        "rhythm": ritmo(ppm),
        "total_predicted": pred,
        "final_score": f"{fh}-{fa}",
        "value": round(value, 1),
        "prob_over": po,
        "prob_under": pu,
        "reason": reason,
        "why": why,
        "source": "screenshot-first"
    }


def nobet(home, away, score, clock, reason, why, line="-"):
    return {
        "signal": "NO_BET",
        "action": "NON GIOCARE",
        "decision_text": "Non scommettere",
        "side": None,
        "line": line,
        "stake": 0,
        "confidence": 0,
        "score": score,
        "clock": clock,
        "teams": {
            "home": home,
            "away": away
        },
        "rhythm": "non valutabile",
        "total_predicted": "-",
        "final_score": "-",
        "value": 0,
        "prob_over": 50,
        "prob_under": 50,
        "reason": reason,
        "why": why,
        "source": "screenshot-first"
    }


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "telegram": bool(TG_TOKEN and TG_CHAT),
        "openai": bool(OPENAI_API_KEY),
        "mode": "screenshot-first",
        "storico_onedrive": True
    })


@app.route("/api/storico/onedrive", methods=["POST"])
def storico_onedrive():
    try:
        p = request.get_json(force=True)
        url = p.get("url")

        if not url:
            return jsonify({
                "ok": False,
                "error": "Link OneDrive mancante"
            }), 400

        storico = leggi_storico_da_onedrive(url)

        return jsonify({
            "ok": True,
            "rows": len(storico),
            "storico": storico,
            "message": "Foglio STORICO letto correttamente da OneDrive"
        })

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


@app.route("/api/screenshot/analyze", methods=["POST"])
def shot():
    f = request.files.get("image")
    bankroll = float(request.form.get("bankroll") or 25)

    if not f:
        return jsonify({"error": "Nessuna immagine"}), 400

    ex = extract(f.read(), f.mimetype or "image/jpeg")

    return jsonify({
        "extracted": ex,
        "decision": decide(ex, bankroll),
        "mode": "screenshot-first"
    })


@app.route("/api/telegram/test", methods=["POST"])
def telegram_test():
    tg("✅ MultiBasket AI PRO: notifiche Telegram attive.")

    return jsonify({
        "ok": True,
        "message": "Messaggio Telegram inviato."
    })


@app.route("/api/watch/start", methods=["POST"])
def watch():
    msg = "⚠️ Modalità screenshot-first: per aggiornare la giocata devi caricare un nuovo screenshot."
    tg(msg)

    return jsonify({
        "ok": True,
        "message": msg
    })


@app.route("/api/bet/register", methods=["POST"])
def bet():
    p = request.get_json(force=True)

    tg(
        f"✅ <b>Giocata registrata</b>\\n"
        f"{str(p.get('side')).upper()} {p.get('line')}\\n"
        f"Puntata: {p.get('stake')} €\\n"
        f"Fonte: screenshot-first. Aggiorna con nuovo screenshot."
    )

    return jsonify({
        "ok": True,
        "message": "Giocata registrata. Per rivalutarla carica un nuovo screenshot."
    })


@app.route("/api/bet/quality")
def quality():
    return jsonify({
        "message": "In modalità screenshot-first la qualità si aggiorna caricando un nuovo screenshot."
    })


@app.route("/api/live/games")
def live_games():
    return jsonify({
        "count": 0,
        "games": [],
        "message": "Modalità screenshot-first."
    })


@app.route("/api/live/analyze", methods=["POST"])
def live_analyze():
    return jsonify({
        "error": "Usa Analizza screenshot."
    }), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
