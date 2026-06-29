
import os, sqlite3, math, time, threading
from datetime import datetime
import requests
from flask import Flask, request, jsonify, send_from_directory

DB="multibasket_pro.db"
API_KEY=os.getenv("API_BASKETBALL_KEY","")
TG_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
TG_CHAT=os.getenv("TELEGRAM_CHAT_ID","")
INTERVAL=int(os.getenv("CHECK_INTERVAL_SECONDS","60"))

app=Flask(__name__, static_folder=".", static_url_path="")

def db():
    c=sqlite3.connect(DB,check_same_thread=False)
    c.execute("""CREATE TABLE IF NOT EXISTS watches(id INTEGER PRIMARY KEY AUTOINCREMENT,fixture_id TEXT UNIQUE,bankroll REAL,line REAL,odds_over REAL,odds_under REAL,active INTEGER DEFAULT 1,last_signal TEXT,last_confidence INTEGER DEFAULT 0,last_probability INTEGER DEFAULT 0,created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS bets(id INTEGER PRIMARY KEY AUTOINCREMENT,fixture_id TEXT,side TEXT,line REAL,stake REAL,bankroll REAL,active INTEGER DEFAULT 1,last_probability INTEGER DEFAULT 0,last_quality TEXT,created_at TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT,fixture_id TEXT,message TEXT,created_at TEXT)""")
    c.commit()
    return c

def log(kind, fixture_id, msg):
    try:
        c=db()
        c.execute("INSERT INTO events(kind,fixture_id,message,created_at) VALUES(?,?,?,?)",(kind,fixture_id,msg,datetime.now().isoformat()))
        c.commit(); c.close()
    except Exception as e:
        print("log error",e)

def tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        print("[TG MISSING]",msg); return False
    r=requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",json={"chat_id":TG_CHAT,"text":msg,"parse_mode":"HTML","disable_web_page_preview":True},timeout=20)
    r.raise_for_status()
    return True

def api(path, params):
    if not API_KEY: raise RuntimeError("API_BASKETBALL_KEY missing")
    r=requests.get(f"https://v1.basketball.api-sports.io/{path}",headers={"x-apisports-key":API_KEY},params=params,timeout=20)
    r.raise_for_status()
    return r.json()

def mins(clock):
    try:
        s=str(clock or "0:00")
        if ":" in s:
            a,b=s.split(":")[:2]; return int(a)+int(b)/60
        return float(s)
    except Exception:
        return 0.0

def norm(g):
    teams=g.get("teams",{}) or {}; scores=g.get("scores",{}) or {}; status=g.get("status",{}) or {}
    hs=(scores.get("home",{}) or {}).get("total") or 0
    aw=(scores.get("away",{}) or {}).get("total") or 0
    short=str(status.get("short") or "").upper()
    q=4
    for ch in short:
        if ch.isdigit(): q=int(ch); break
    if q<1 or q>4: q=4
    return {"fixture_id":str(g.get("id")),"home":(teams.get("home",{}) or {}).get("name") or "Casa","away":(teams.get("away",{}) or {}).get("name") or "Ospite","home_score":int(hs),"away_score":int(aw),"quarter":q,"clock":status.get("timer") or status.get("clock") or status.get("elapsed") or "0:00","league":(g.get("league") or {}).get("name") or "","status":status.get("long") or status.get("short") or ""}

def fixture(fid):
    data=api("games",{"id":fid})
    resp=data.get("response",[])
    if not resp: raise ValueError("Fixture non trovata")
    return norm(resp[0])

def logistic(x): return 1/(1+math.exp(-x))
def ritmo(p):
    if p<2.4: return "Molto lento"
    if p<2.8: return "Lento"
    if p<3.2: return "Medio"
    if p<3.6: return "Veloce"
    return "Molto veloce"
def stake(bankroll,conf):
    if conf>=88: pct=.06
    elif conf>=80: pct=.045
    elif conf>=70: pct=.03
    elif conf>=65: pct=.02
    else: pct=0
    return max(0,round(bankroll*pct))

def calc(live, bankroll, line, odds_over=1.85, odds_under=1.85):
    h=live["home_score"]; a=live["away_score"]; total=h+a
    q=int(live["quarter"]); left=mins(live["clock"])
    played=max(.1,(q-1)*10+(10-left)); rem=max(0,40-played)
    pace=total/played
    phase={1:.98,2:.96,3:.94,4:.90}.get(q,.90)
    adj=max(1.65,min(5.05 if q==4 else 5.35,pace*phase))
    raw=total+rem*adj
    corr=max(-3,min(3,(line-raw)*.10))
    pred=round(raw+corr); value=pred-line
    share=h/total if total else .5
    fh=round(pred*share); fa=round(pred-fh)
    po=round(logistic(value/5.2)*100); pu=100-po
    threshold=8 if played>=8 else 10
    side=None
    if value>=threshold: side="OVER"
    elif value<=-threshold: side="UNDER"
    conf=0
    if side:
        conf=round(min(100,min(55,abs(value)*4)+min(20,played/40*28)+15+(10 if bankroll>=20 else 5)))
    st=stake(bankroll,conf)
    signal="BET" if side and conf>=65 and st>0 else ("OBSERVE" if abs(value)>=4 else "NO_BET")
    reason=(f"{side} con valore {value:+.1f}: totale previsto {pred}, linea {line}." if signal=="BET" else f"Osserva: valore {value:+.1f}, non ancora sufficiente." if signal=="OBSERVE" else f"No bet: valore {value:+.1f} insufficiente rispetto alla linea {line}.")
    return {"signal":signal,"side":side,"line":line,"stake":st,"confidence":conf,"score":f"{h}-{a}","clock":f"{live['clock']} Q{q}","teams":{"home":live["home"],"away":live["away"]},"league":live["league"],"rhythm":ritmo(pace),"total_predicted":pred,"final_score":f"{fh}-{fa}","value":round(value,1),"prob_over":po,"prob_under":pu,"win_probability":po if side=="OVER" else pu if side=="UNDER" else max(po,pu),"reason":reason}

def msg_signal(d):
    side=d.get("side") or "NO BET"
    return f"🏀 <b>MultiBasket AI PRO</b>\n\n{d['teams']['home']} - {d['teams']['away']}\nPunteggio: <b>{d['score']}</b>\nTempo: <b>{d['clock']}</b>\n\nSegnale: <b>{side} {d['line'] if d.get('side') else ''}</b>\nPuntata: <b>{d['stake']} €</b>\nAffidabilità: <b>{d['confidence']}/100</b>\nTotale previsto: <b>{d['total_predicted']}</b>\nFinale stimato: <b>{d['final_score']}</b>\nValore: <b>{d['value']:+.1f}</b>\nRitmo: <b>{d['rhythm']}</b>\n\n{d['reason']}"

def quality(side,d):
    p=d["prob_over"] if side.upper()=="OVER" else d["prob_under"]
    q="Ottima" if p>=80 else "Buona" if p>=65 else "In bilico" if p>=52 else "A rischio"
    return p,q

def msg_bet(side,line,stake_v,d):
    p,q=quality(side,d)
    return f"📊 <b>Aggiornamento giocata</b>\n\n{d['teams']['home']} - {d['teams']['away']}\nPunteggio: <b>{d['score']}</b>\nTempo: <b>{d['clock']}</b>\n\nLa tua giocata: <b>{side.upper()} {line}</b>\nPuntata: <b>{stake_v} €</b>\nTotale previsto ora: <b>{d['total_predicted']}</b>\nProbabilità stimata di vincita: <b>{p}%</b>\nQualità: <b>{q}</b>",p,q

def monitor():
    print("[MONITOR] started")
    while True:
        try:
            c=db()
            watches=c.execute("SELECT fixture_id,bankroll,line,odds_over,odds_under,last_signal,last_confidence FROM watches WHERE active=1").fetchall()
            for fid,bankroll,line,oo,ou,last_signal,last_conf in watches:
                try:
                    d=calc(fixture(fid),bankroll,line,oo,ou)
                    if d["signal"]=="BET" and (last_signal!="BET" or abs(d["confidence"]-int(last_conf or 0))>=8):
                        m=msg_signal(d); tg(m); log("SIGNAL",fid,m)
                    c.execute("UPDATE watches SET last_signal=?,last_confidence=?,last_probability=? WHERE fixture_id=?",(d["signal"],d["confidence"],d["win_probability"],fid))
                except Exception as e: print("[WATCH ERROR]",fid,e)
            bets=c.execute("SELECT id,fixture_id,side,line,stake,bankroll,last_probability,last_quality FROM bets WHERE active=1").fetchall()
            for bid,fid,side,line,stake_v,bankroll,last_prob,last_quality in bets:
                try:
                    d=calc(fixture(fid),bankroll,line)
                    m,p,q=msg_bet(side,line,stake_v,d)
                    if (not last_quality) or abs(p-int(last_prob or 0))>=10 or q!=last_quality:
                        tg(m); log("BET_QUALITY",fid,m)
                    c.execute("UPDATE bets SET last_probability=?,last_quality=? WHERE id=?",(p,q,bid))
                except Exception as e: print("[BET ERROR]",fid,e)
            c.commit(); c.close()
        except Exception as e: print("[MONITOR ERROR]",e)
        time.sleep(INTERVAL)

@app.route("/")
def home(): return send_from_directory(".","index.html")

@app.route("/api/health")
def health(): return jsonify({"ok":True,"api_basketball":bool(API_KEY),"telegram":bool(TG_TOKEN and TG_CHAT),"interval":INTERVAL})

@app.route("/api/live/games")
def live_games():
    data=api("games",{"live":"all"})
    return jsonify({"count":len(data.get("response",[])),"games":[norm(g) for g in data.get("response",[])]})

@app.route("/api/live/analyze",methods=["POST"])
def analyze():
    r=request.get_json(force=True)
    return jsonify(calc(fixture(r["fixture_id"]),float(r["bankroll"]),float(r["line"]),float(r.get("odds_over",1.85)),float(r.get("odds_under",1.85))))

@app.route("/api/watch/start",methods=["POST"])
def watch_start():
    r=request.get_json(force=True)
    c=db()
    c.execute("""INSERT OR REPLACE INTO watches(fixture_id,bankroll,line,odds_over,odds_under,active,created_at,last_signal,last_confidence,last_probability) VALUES(?,?,?,?,?,1,?,?,0,0)""",(r["fixture_id"],float(r["bankroll"]),float(r["line"]),float(r.get("odds_over",1.85)),float(r.get("odds_under",1.85)),datetime.now().isoformat(),None))
    c.commit(); c.close()
    tg(f"✅ <b>Monitoraggio avviato</b>\n\nFixture ID: <b>{r['fixture_id']}</b>\nLinea: <b>{r['line']}</b>\nBankroll: <b>{r['bankroll']} €</b>\n\nTi avviso se compare un segnale valido.")
    return jsonify({"ok":True,"message":"Monitoraggio automatico avviato. Riceverai notifiche Telegram se compare un segnale."})

@app.route("/api/bet/register",methods=["POST"])
def bet_register():
    r=request.get_json(force=True)
    c=db()
    c.execute("INSERT INTO bets(fixture_id,side,line,stake,bankroll,active,created_at) VALUES(?,?,?,?,?,1,?)",(r["fixture_id"],r["side"].upper(),float(r["line"]),float(r["stake"]),float(r["bankroll"]),datetime.now().isoformat()))
    c.commit(); c.close()
    try:
        d=calc(fixture(r["fixture_id"]),float(r["bankroll"]),float(r["line"]))
        m,p,q=msg_bet(r["side"],float(r["line"]),float(r["stake"]),d)
        tg("✅ <b>Giocata registrata</b>\n\n"+m); log("BET_REGISTERED",r["fixture_id"],m)
    except Exception:
        tg(f"✅ <b>Giocata registrata</b>\n\n{r['side'].upper()} {r['line']}\nPuntata: {r['stake']} €\nMonitoraggio attivo.")
    return jsonify({"ok":True,"message":"Giocata registrata. Riceverai notifiche Telegram sulla qualità della puntata."})

@app.route("/api/bet/quality")
def bet_quality():
    fid=request.args.get("fixture_id")
    c=db()
    row=c.execute("SELECT side,line,stake,bankroll FROM bets WHERE fixture_id=? AND active=1 ORDER BY id DESC LIMIT 1",(fid,)).fetchone()
    c.close()
    if not row: return jsonify({"error":"Nessuna giocata attiva."})
    side,line,stake_v,bankroll=row
    d=calc(fixture(fid),bankroll,line)
    m,p,q=msg_bet(side,line,stake_v,d)
    return jsonify({"score":d["score"],"clock":d["clock"],"total_predicted":d["total_predicted"],"win_probability":p,"quality":q,"message":f"La partita è {d['score']} a {d['clock']}. La tua giocata {side} {line} ha probabilità stimata {p}%: qualità {q}."})

@app.route("/api/telegram/test",methods=["POST"])
def telegram_test():
    tg("✅ MultiBasket AI PRO: notifiche Telegram attive.")
    return jsonify({"ok":True,"message":"Messaggio Telegram inviato."})

@app.route("/api/events")
def events():
    c=db(); rows=c.execute("SELECT kind,fixture_id,message,created_at FROM events ORDER BY id DESC LIMIT 50").fetchall(); c.close()
    return jsonify({"events":[{"kind":r[0],"fixture_id":r[1],"message":r[2],"created_at":r[3]} for r in rows]})

try:
    db().close()
    threading.Thread(target=monitor,daemon=True).start()
except Exception as e:
    print("[STARTUP ERROR]",e)
