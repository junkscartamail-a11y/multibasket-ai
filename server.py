import os, json, re, base64, math, requests
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

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
Leggi uno screenshot di una partita basket live bookmaker.

Rispondi SOLO con JSON valido.

Devi essere tollerante:
- Se il cronometro mostra 0:0 o 0:00, usa "0:00".
- Se siamo a fine secondo quarto/intervallo, quarter = 2 e timeRemaining = "0:00".
- Se vedi più linee Over/Under, scegli come lineOU la linea più bassa visibile tra quelle principali U/O.
- Estrai anche tutte le linee U/O visibili se possibile.
- Non inventare squadre o punteggi, ma se sono visibili devi leggerli.

Formato JSON:
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
  "ouLines": [
    {"line": number, "over": number|null, "under": number|null}
  ],
  "q1Home": number|null,
  "q1Away": number|null,
  "q2Home": number|null,
  "q2Away": number|null,
  "q3Home": number|null,
  "q3Away": number|null,
  "q4Home": number|null,
  "q4Away": number|null,
  "confidence": number
}
"""

    r = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Leggi questo screenshot e restituisci solo JSON."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                ]
            }
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
        # usa la linea più bassa visibile, così evita che lineOU resti null
        chosen = sorted(valid, key=lambda x: x["line"])[0]
        return chosen["line"], chosen["over"], chosen["under"], valid

    line = safe_float(ex.get("lineOU"))
    over = safe_float(ex.get("oddsOver"))
    under = safe_float(ex.get("oddsUnder"))

    return line, over, under, []


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
            home,
            away,
            "-",
            "-",
            "Dati mancanti dall'estrazione AI: " + ", ".join(missing) + ".",
            [
                "Lo screenshot può essere leggibile, ma il parser AI non ha restituito tutti i campi",
                "Serve aggiornare o ritagliare meglio lo screenshot",
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
            home,
            away,
            f"{h}-{a}",
            f"{ex.get('timeRemaining')} Q{q}",
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

    corr_ppm = max(
        1.70,
        min(4.85, ppm * phase * fatigue * blowout * (1 + close) * trend_factor)
    )

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
        reason = (
            f"Margine sufficiente per giocare ora. Totale previsto {pred}, "
            f"linea attuale {line}, margine {value:+.1f}."
        )
    elif abs(value) >= 4:
        signal = "OBSERVE"
        action = "ASPETTA"
        decision_text = "Aspetta una linea più conveniente"
        reason = (
            f"Margine insufficiente per giocare ora. Totale previsto {pred}, "
            f"linea attuale {line}, margine {value:+.1f}."
        )
    else:
        signal = "NO_BET"
        action = "NO BET"
        decision_text = "Non scommettere"
        reason = (
            f"Linea troppo vicina alla previsione. Totale previsto {pred}, "
            f"linea attuale {line}, margine {value:+.1f}."
        )

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


@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/api/health")
def health():
    return jsonify({
        "ok": True,
        "telegram": bool(TG_TOKEN and TG_CHAT),
        "openai": bool(OPENAI_API_KEY),
        "mode": "screenshot-first + trend quarti"
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

    return jsonify({
        "ok": True,
        "message": "Giocata registrata."
    })


@app.route("/api/bet/quality")
def quality():
    return jsonify({
        "message": "Aggiorna caricando un nuovo screenshot live."
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
    return jsonify({"error": "Usa Analizza screenshot."}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
