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

    prompt = """Estrai dati da screenshot basket live bookmaker. Rispondi SOLO JSON valido:
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
"q1Home":number|null,
"q1Away":number|null,
"q2Home":number|null,
"q2Away":number|null,
"q3Home":number|null,
"q3Away":number|null,
"confidence":number
}
Non inventare dati non visibili.
timeRemaining è il tempo rimanente nel quarto.
Se il cronometro mostra 0:0 o 0:00 a fine secondo quarto, quarter deve essere 2 e timeRemaining "0:00".
Se vedi la tabella dei quarti, estrai i parziali Q1, Q2, Q3 quando presenti."""

    r = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Leggi lo screenshot."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
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


def quarter_totals(ex):
    q1_total = None
    q2_total = None
    q3_total = None

    if ex.get("q1Home") is not None and ex.get("q1Away") is not None:
        q1_total = float(ex.get("q1Home")) + float(ex.get("q1Away"))

    if ex.get("q2Home") is not None and ex.get("q2Away") is not None:
        q2_total = float(ex.get("q2Home")) + float(ex.get("q2Away"))

    if ex.get("q3Home") is not None and ex.get("q3Away") is not None:
        q3_total = float(ex.get("q3Home")) + float(ex.get("q3Away"))

    return q1_total, q2_total, q3_total


def trend_quarti(q1_total, q2_total, q3_total):
    trend_factor = 1.0
    descrizione = "non disponibile"

    if q1_total and q2_total:
        diff = q2_total - q1_total

        if diff <= -8:
            trend_factor = 0.92
            descrizione = "forte rallentamento tra Q1 e Q2"
        elif diff <= -5:
            trend_factor = 0.95
            descrizione = "rallentamento tra Q1 e Q2"
        elif diff >= 8:
            trend_factor = 1.08
            descrizione = "forte accelerazione tra Q1 e Q2"
        elif diff >= 5:
            trend_factor = 1.05
            descrizione = "accelerazione tra Q1 e Q2"
        else:
            descrizione = "ritmo abbastanza stabile tra Q1 e Q2"

    if q2_total and q3_total:
        diff = q3_total - q2_total

        if diff <= -8:
            trend_factor *= 0.94
            descrizione = "forte rallentamento recente"
        elif diff <= -5:
            trend_factor *= 0.97
            descrizione = "rallentamento recente"
        elif diff >= 8:
            trend_factor *= 1.06
            descrizione = "forte accelerazione recente"
        elif diff >= 5:
            trend_factor *= 1.03
            descrizione = "accelerazione recente"

    return trend_factor, descrizione


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

    q1_total, q2_total, q3_total = quarter_totals(ex)
    trend_factor, trend_desc = trend_quarti(q1_total, q2_total, q3_total)

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

    over_line_attesa = round(pred - MARGINE_SICUREZZA, 1)
    under_line_attesa = round(pred + MARGINE_SICUREZZA, 1)

    if value >= MARGINE_SICUREZZA:
        side = "OVER"
    elif value <= -MARGINE_SICUREZZA:
        side = "UNDER"
    else:
        side = None

    conf = 0
    conf += 22
    conf += 18 if ex.get("oddsOver") and ex.get("oddsUnder") else 8
    conf += 20 if played >= 8 else 8
    conf += 25 if abs(value) >= 12 else 18 if abs(value) >= 9 else 10 if abs(value) >= 7 else 0
    conf += 8 if home != "Squadra A" and away != "Squadra B" else 0
    conf += 6 if q1_total and q2_total else 0

    if played < 5:
        conf -= 10

    if q == 4 and rem < 3:
        conf += 5

    conf = max(0, min(90, round(conf)))

    st = stake(bankroll, conf, value) if side else 0

    share = h / total if total else .5
    fh = round(pred * share)
    fa = round(pred - fh)

    trend_line = f"Trend quarti: Q1 {q1_total if q1_total is not None else '-'} / Q2 {q2_total if q2_total is not None else '-'} / Q3 {q3_total if q3_total is not None else '-'}"

    if side and conf >= 68 and st > 0:
        signal = "BET"
        action = f"GIOCA {side}"
        text = f"Scommetti {side} {line}"

        reason = (
            f"Totale previsto {pred}. Linea bookmaker {line}. "
            f"Margine {value:+.1f}. Il margine supera la soglia di sicurezza di {MARGINE_SICUREZZA} punti. "
            f"Ritmo {ritmo(ppm)}, giocati {played:.1f} minuti, restano {rem:.1f}. "
            f"{trend_desc}. Stake consigliato {st} € su bankroll {bankroll:.2f} €."
        )

        why = [
            f"Pronostico: {side}",
            f"Totale previsto: {pred}",
            f"Linea attuale: {line}",
            f"Margine sulla linea: {value:+.1f}",
            trend_line,
            f"Andamento: {trend_desc}",
            f"Linea OVER conveniente fino a {over_line_attesa}",
            f"Linea UNDER conveniente da {under_line_attesa}",
            f"Stake consigliato: {st} €"
        ]

    elif abs(value) >= 5:
        signal = "OBSERVE"
        action = "ASPETTA"
        text = "Aspetta una linea più conveniente"

        reason = (
            f"Totale previsto {pred}. Linea attuale {line}. "
            f"Margine {value:+.1f}, non abbastanza sicuro per entrare ora. "
            f"{trend_desc}. "
            f"OVER conveniente solo a {over_line_attesa} o meno. "
            f"UNDER conveniente solo a {under_line_attesa} o più."
        )

        why = [
            "Non entrare adesso",
            f"Totale previsto: {pred}",
            f"Linea attuale: {line}",
            trend_line,
            f"Andamento: {trend_desc}",
            f"OVER giocabile solo se la linea scende a {over_line_attesa} o meno",
            f"UNDER giocabile solo se la linea sale a {under_line_attesa} o più",
            "Puntata consigliata 0 €"
        ]

    else:
        signal = "NO_BET"
        action = "NO BET"
        text = "Non scommettere"

        reason = (
            f"Totale previsto {pred}. Linea attuale {line}. "
            f"Margine {value:+.1f}: troppo vicino alla linea bookmaker. "
            f"{trend_desc}. "
            f"OVER conveniente solo a {over_line_attesa} o meno. "
            f"UNDER conveniente solo a {under_line_attesa} o più."
        )

        why = [
            "Edge insufficiente",
            f"Totale previsto: {pred}",
            f"Linea attuale: {line}",
            trend_line,
            f"Andamento: {trend_desc}",
            f"OVER conveniente fino a {over_line_attesa}",
            f"UNDER conveniente da {under_line_attesa}",
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
        "teams": {"home": home, "away": away},
        "rhythm": ritmo(ppm),
        "total_predicted": pred,
        "final_score": f"{fh}-{fa}",
        "value": round(value, 1),
        "prob_over": po,
        "prob_under": pu,
        "over_wait_line": over_line_attesa,
        "under_wait_line": under_line_attesa,
        "q1_total": q1_total,
        "q2_total": q2_total,
        "q3_total": q3_total,
        "trend_factor": round(trend_factor, 3),
        "trend_desc": trend_desc,
        "reason": reason,
        "why": why,
        "source": "screenshot-first + trend quarti"
    }


def nobet(home, away, score, clock, reason, why, line="-"):
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
        "total_predicted": "-",
        "final_score": "-",
        "value": 0,
        "prob_over": 50,
        "prob_under": 50,
        "over_wait_line": "-",
        "under_wait_line": "-",
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
        "mode": "screenshot-first + trend quarti"
    })


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
    return jsonify({"error": "Usa Analizza screenshot."}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
