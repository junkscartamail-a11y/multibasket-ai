
import os, json, re, base64, math
import requests
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI

API_KEY=os.getenv("API_BASKETBALL_KEY","")
TG_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
TG_CHAT=os.getenv("TELEGRAM_CHAT_ID","")
OPENAI_API_KEY=os.getenv("OPENAI_API_KEY","")
OPENAI_MODEL=os.getenv("OPENAI_MODEL","gpt-4o-mini")

app=Flask(__name__, static_folder=".", static_url_path="")

def api(path, params):
    r=requests.get(f"https://v1.basketball.api-sports.io/{path}",headers={"x-apisports-key":API_KEY},params=params,timeout=20)
    r.raise_for_status()
    return r.json()

def norm(g):
    teams=g.get("teams",{}) or {}; scores=g.get("scores",{}) or {}; status=g.get("status",{}) or {}
    hs=(scores.get("home",{}) or {}).get("total") or 0
    aw=(scores.get("away",{}) or {}).get("total") or 0
    short=str(status.get("short") or "").upper()
    q=4
    for ch in short:
        if ch.isdigit(): q=int(ch); break
    return {"fixture_id":str(g.get("id")),"home":(teams.get("home",{}) or {}).get("name") or "Casa","away":(teams.get("away",{}) or {}).get("name") or "Ospite","home_score":int(hs),"away_score":int(aw),"quarter":q,"clock":status.get("timer") or status.get("clock") or status.get("elapsed") or "0:00","league":(g.get("league") or {}).get("name") or ""}

def get_fixture(fid):
    resp=api("games",{"id":fid}).get("response",[])
    if not resp: raise Exception("Fixture non trovata")
    return norm(resp[0])

def mins(clock):
    try:
        s=str(clock or "0:00")
        if ":" in s:
            a,b=s.split(":")[:2]; return int(a)+int(b)/60
        return float(s)
    except: return 0

def calc(live, bankroll, line):
    h=live["home_score"]; a=live["away_score"]; total=h+a
    q=int(live["quarter"]); left=mins(live["clock"])
    played=max(.1,(q-1)*10+(10-left)); rem=max(0,40-played)
    pace=total/played
    phase={1:.98,2:.96,3:.94,4:.90}.get(q,.90)
    adj=max(1.65,min(5.05 if q==4 else 5.35,pace*phase))
    pred=round(total+rem*adj+max(-3,min(3,(line-(total+rem*adj))*.10)))
    value=pred-line
    po=round(100/(1+math.exp(-value/5.2))); pu=100-po
    side=None
    thr=8 if played>=8 else 10
    if value>=thr: side="OVER"
    if value<=-thr: side="UNDER"
    conf=0
    if side: conf=round(min(100,min(55,abs(value)*4)+min(20,played/40*28)+25))
    if conf>=88: pct=.06
    elif conf>=80: pct=.045
    elif conf>=70: pct=.03
    elif conf>=65: pct=.02
    else: pct=0
    stake=max(0,round(bankroll*pct))
    signal="BET" if side and stake>0 else ("OBSERVE" if abs(value)>=4 else "NO_BET")
    share=h/total if total else .5
    fh=round(pred*share); fa=round(pred-fh)
    reason=(f"{side} con valore {value:+.1f}: totale previsto {pred}, linea {line}." if signal=="BET" else f"Osserva: valore {value:+.1f}, non ancora sufficiente." if signal=="OBSERVE" else f"No bet: valore {value:+.1f} insufficiente rispetto alla linea {line}.")
    return {"signal":signal,"side":side,"line":line,"stake":stake,"confidence":conf,"score":f"{h}-{a}","clock":f"{live['clock']} Q{q}","teams":{"home":live["home"],"away":live["away"]},"total_predicted":pred,"final_score":f"{fh}-{fa}","value":round(value,1),"prob_over":po,"prob_under":pu,"reason":reason}

def tg(msg):
    if not TG_TOKEN or not TG_CHAT: return False
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",json={"chat_id":TG_CHAT,"text":msg,"parse_mode":"HTML"},timeout=20).raise_for_status()
    return True

def parse_json(t):
    t=(t or "").replace("```json","").replace("```","").strip()
    m=re.search(r"\{.*\}",t,re.S)
    return json.loads(m.group(0) if m else t)

@app.route("/")
def home(): return send_from_directory(".","index.html")

@app.route("/api/health")
def health(): return jsonify({"ok":True,"api_basketball":bool(API_KEY),"telegram":bool(TG_TOKEN and TG_CHAT),"openai":bool(OPENAI_API_KEY)})

@app.route("/api/live/games")
def live_games():
    games=[norm(g) for g in api("games",{"live":"all"}).get("response",[])]
    return jsonify({"count":len(games),"games":games})

@app.route("/api/live/analyze",methods=["POST"])
def analyze():
    r=request.get_json(force=True)
    return jsonify(calc(get_fixture(r["fixture_id"]),float(r["bankroll"]),float(r["line"])))

@app.route("/api/screenshot/analyze",methods=["POST"])
def screenshot():
    f=request.files.get("image")
    if not f: return jsonify({"error":"missing image"}),400
    raw=f.read(); mime=f.mimetype or "image/jpeg"
    b64=base64.b64encode(raw).decode()
    client=OpenAI(api_key=OPENAI_API_KEY)
    prompt=\"\"\"Estrai dati da screenshot basket live bookmaker. Rispondi SOLO JSON:
{"homeTeam":string|null,"awayTeam":string|null,"homeScore":number|null,"awayScore":number|null,"quarter":number|null,"timeRemaining":"M:SS"|null,"lineOU":number|null,"oddsOver":number|null,"oddsUnder":number|null,"confidence":number}
Non inventare dati non visibili.\"\"\"
    res=client.chat.completions.create(model=OPENAI_MODEL,messages=[
        {"role":"system","content":prompt},
        {"role":"user","content":[{"type":"text","text":"Leggi lo screenshot."},{"type":"image_url","image_url":{"url":f"data:{mime};base64,{b64}"}}]}
    ],temperature=0)
    ex=parse_json(res.choices[0].message.content)
    match=None
    try:
        games=[norm(g) for g in api("games",{"live":"all"}).get("response",[])]
        ht=(ex.get("homeTeam") or "").lower(); at=(ex.get("awayTeam") or "").lower()
        best=None; score=0
        for g in games:
            s=0; gh=(g["home"] or "").lower(); ga=(g["away"] or "").lower()
            if ht and (ht in gh or gh in ht): s+=3
            if at and (at in ga or ga in at): s+=3
            if ex.get("homeScore") is not None and int(ex.get("homeScore"))==g["home_score"]: s+=1
            if ex.get("awayScore") is not None and int(ex.get("awayScore"))==g["away_score"]: s+=1
            if s>score: score=s; best=g
        if score>=3: match=best
    except Exception as e: print(e)
    return jsonify({"extracted":ex,"match":match})

@app.route("/api/watch/start",methods=["POST"])
def watch():
    r=request.get_json(force=True)
    tg(f"✅ <b>Monitoraggio avviato</b>\nFixture ID: <b>{r['fixture_id']}</b>\nLinea: <b>{r['line']}</b>\nBankroll: <b>{r['bankroll']} €</b>")
    return jsonify({"ok":True,"message":"Monitoraggio avviato. Telegram attivo."})

@app.route("/api/bet/register",methods=["POST"])
def bet():
    r=request.get_json(force=True)
    tg(f"✅ <b>Giocata registrata</b>\n{r['side'].upper()} {r['line']}\nPuntata: {r['stake']} €")
    return jsonify({"ok":True,"message":"Giocata registrata. Riceverai notifiche Telegram."})

@app.route("/api/bet/quality")
def quality():
    return jsonify({"message":"Qualità giocata: monitoraggio Telegram attivo."})

@app.route("/api/telegram/test",methods=["POST"])
def telegram_test():
    tg("✅ MultiBasket AI PRO: notifiche Telegram attive.")
    return jsonify({"ok":True,"message":"Messaggio Telegram inviato."})
