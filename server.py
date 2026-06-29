
import os,json,re,base64,math,statistics,requests
from flask import Flask,request,jsonify,send_from_directory
from openai import OpenAI

API_KEY=os.getenv("API_BASKETBALL_KEY","")
TG_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN","")
TG_CHAT=os.getenv("TELEGRAM_CHAT_ID","")
OPENAI_API_KEY=os.getenv("OPENAI_API_KEY","")
OPENAI_MODEL=os.getenv("OPENAI_MODEL","gpt-4o-mini")
app=Flask(__name__,static_folder=".",static_url_path="")

def tg(msg):
    if not TG_TOKEN or not TG_CHAT:return False
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",json={"chat_id":TG_CHAT,"text":msg,"parse_mode":"HTML"},timeout=20).raise_for_status()
    return True

def api(path,params):
    if not API_KEY: raise RuntimeError("API_BASKETBALL_KEY missing")
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
        if ch.isdigit():q=int(ch);break
    return {"fixture_id":str(g.get("id")),"home":(teams.get("home",{}) or {}).get("name") or "Casa","away":(teams.get("away",{}) or {}).get("name") or "Ospite","home_id":(teams.get("home",{}) or {}).get("id"),"away_id":(teams.get("away",{}) or {}).get("id"),"home_score":int(hs),"away_score":int(aw),"quarter":q,"clock":status.get("timer") or status.get("clock") or status.get("elapsed") or "0:00","league":(g.get("league") or {}).get("name") or "","source":"api-live"}

def fixture(fid):
    resp=api("games",{"id":fid}).get("response",[])
    if not resp:raise Exception("Fixture non trovata")
    return norm(resp[0])

def clock_min(x):
    try:
        s=str(x or "0:00")
        if ":" in s:
            m,sec=s.split(":")[:2]; return int(m)+int(sec)/60
        return float(s)
    except Exception:return 0.0

def ritmo(p):
    if p<2.4:return"Molto lento"
    if p<2.8:return"Lento"
    if p<3.2:return"Medio"
    if p<3.6:return"Veloce"
    return"Molto veloce"

def stake(bankroll,conf):
    if conf>=88:p=.06
    elif conf>=80:p=.045
    elif conf>=70:p=.03
    elif conf>=65:p=.02
    else:p=0
    return 0 if p==0 else max(1,round(bankroll*p))

def live_decide(live,bankroll,line):
    h=int(live.get("home_score") or 0); a=int(live.get("away_score") or 0); total=h+a
    q=int(live.get("quarter") or 4); left=clock_min(live.get("clock") or "0:00")
    played=max(.1,(q-1)*10+(10-left)); rem=max(0,40-played)
    pace=total/played
    adj=max(1.65,min(5.05 if q==4 else 5.35,pace*{1:.98,2:.96,3:.94,4:.90}.get(q,.90)))
    raw=total+rem*adj
    pred=round(raw+max(-3,min(3,(line-raw)*.10)))
    value=pred-line
    po=round(100/(1+math.exp(-value/5.2))); pu=100-po
    side=None; thr=8 if played>=8 else 10
    if value>=thr:side="OVER"
    elif value<=-thr:side="UNDER"
    conf=0
    if side:conf=round(min(100,min(55,abs(value)*4)+min(20,played/40*28)+15+(10 if bankroll>=20 else 5)))
    st=stake(bankroll,conf)
    if side and conf>=65:
        signal="BET"; action=f"GIOCA {side}"; text=f"Scommetti {side} {line}"
    elif abs(value)>=4:
        signal="OBSERVE"; action="OSSERVA"; text="Non entrare ancora"
    else:
        signal="NO_BET"; action="NON GIOCARE"; text="Non scommettere"
    share=h/total if total else .5
    fh=round(pred*share); fa=round(pred-fh)
    why=["Dato live disponibile","Calcolo sul ritmo reale","Stake prudente sul bankroll"]
    reason=f"Analisi live: totale previsto {pred}, valore {value:+.1f} sulla linea {line}, ritmo {ritmo(pace).lower()}."
    return {"signal":signal,"action":action,"decision_text":text,"side":side,"line":line,"stake":st,"confidence":conf,"score":f"{h}-{a}","clock":f"{live.get('clock')} Q{q}","teams":{"home":live.get("home") or "Casa","away":live.get("away") or "Ospite"},"rhythm":ritmo(pace),"total_predicted":pred,"final_score":f"{fh}-{fa}","value":round(value,1),"prob_over":po,"prob_under":pu,"reason":reason,"why":why,"source":"api-live"}

def parse_json(t):
    c=(t or "").replace("```json","").replace("```","").strip()
    m=re.search(r"\{.*\}",c,re.S)
    return json.loads(m.group(0) if m else c)

def extract(raw,mime):
    b64=base64.b64encode(raw).decode()
    client=OpenAI(api_key=OPENAI_API_KEY)
    prompt=\"\"\"Estrai dati da screenshot basket live bookmaker. Rispondi SOLO JSON valido:
{"homeTeam":string|null,"awayTeam":string|null,"homeScore":number|null,"awayScore":number|null,"quarter":number|null,"timeRemaining":"M:SS"|null,"lineOU":number|null,"oddsOver":number|null,"oddsUnder":number|null,"confidence":number}
Non inventare dati non visibili. La linea O/U è la linea punti totali.\"\"\"
    res=client.chat.completions.create(model=OPENAI_MODEL,messages=[
        {"role":"system","content":prompt},
        {"role":"user","content":[{"type":"text","text":"Leggi screenshot."},{"type":"image_url","image_url":{"url":f"data:{mime};base64,{b64}"}}]}
    ],temperature=0)
    return parse_json(res.choices[0].message.content)

def match_live(ex):
    games=[norm(g) for g in api("games",{"live":"all"}).get("response",[])]
    ht=(ex.get("homeTeam") or "").lower(); at=(ex.get("awayTeam") or "").lower()
    best=None; bests=0
    for g in games:
        s=0; gh=g["home"].lower(); ga=g["away"].lower()
        if ht and (ht in gh or gh in ht):s+=3
        if at and (at in ga or ga in at):s+=3
        try:
            if ex.get("homeScore") is not None and int(ex["homeScore"])==g["home_score"]:s+=1
            if ex.get("awayScore") is not None and int(ex["awayScore"])==g["away_score"]:s+=1
        except Exception:pass
        if s>bests:best,bests=g,s
    return best if bests>=3 else None

def clean(x):return re.sub(r"[^a-z0-9 ]"," ",(x or "").lower()).strip()

def team_id(name):
    if not name:return None
    try:
        res=api("teams",{"search":name}).get("response",[])
        nc=clean(name); best=None; bests=0
        for item in res:
            t=item.get("name") or (item.get("team") or {}).get("name") or ""
            tid=item.get("id") or (item.get("team") or {}).get("id")
            tc=clean(t); s=0
            if nc and nc in tc:s+=4
            if tc and tc in nc:s+=3
            s+=len(set(nc.split())&set(tc.split()))
            if s>bests:best,bests=tid,s
        return best
    except Exception as e:
        print("[TEAM SEARCH]",e); return None

def game_total(g):
    scores=g.get("scores",{}) or {}
    h=(scores.get("home",{}) or {}).get("total"); a=(scores.get("away",{}) or {}).get("total")
    try:
        if h is None or a is None:return None
        h=int(h); a=int(a)
        if h<=0 or a<=0:return None
        return h+a
    except Exception:return None

def recent_totals(tid,limit=12):
    out=[]
    if not tid:return out
    for season in ["2025-2026","2025","2024-2025","2024","2023-2024","2023"]:
        if len(out)>=limit:break
        try:
            for g in api("games",{"team":tid,"season":season}).get("response",[]):
                tot=game_total(g)
                if tot:out.append(tot)
                if len(out)>=limit:break
        except Exception as e:print("[TEAM GAMES]",tid,season,e)
    return out[:limit]

def h2h_totals(hid,aid,limit=6):
    out=[]
    if not hid or not aid:return out
    try:
        for g in api("games",{"h2h":f"{hid}-{aid}"}).get("response",[]):
            tot=game_total(g)
            if tot:out.append(tot)
            if len(out)>=limit:break
    except Exception as e:print("[H2H]",e)
    return out[:limit]

def historical_decide(ex,bankroll,line):
    home=ex.get("homeTeam") or "Casa"; away=ex.get("awayTeam") or "Ospite"
    hid=team_id(home); aid=team_id(away)
    totals=recent_totals(hid,10)+recent_totals(aid,10)+h2h_totals(hid,aid,6)
    sample=len(totals)
    score=f"{ex.get('homeScore') or '-'}-{ex.get('awayScore') or '-'}"
    clock=f"{ex.get('timeRemaining') or '-'} Q{ex.get('quarter') or '-'}"
    if sample<8:
        return {"signal":"NO_BET","action":"NON GIOCARE","decision_text":"Dati storici insufficienti","side":None,"line":line,"stake":0,"confidence":0,"score":score,"clock":clock,"teams":{"home":home,"away":away},"rhythm":"Non valutabile","total_predicted":"-","final_score":"-","value":0,"prob_over":50,"prob_under":50,"reason":"Partita non trovata live e storico reale insufficiente. Non invento pronostici: NO BET.","why":["Match non trovato live","Campione storico insufficiente","Nessuna puntata consigliata"],"source":"historical-fallback"}
    avg=statistics.mean(totals); med=statistics.median(totals)
    pred=round(avg*.65+med*.35); value=pred-line
    over_rate=sum(1 for t in totals if t>line)/sample
    po=round(over_rate*100); pu=100-po
    side=None
    if value>=7 and po>=62:side="OVER"
    elif value<=-7 and pu>=62:side="UNDER"
    conf=round(min(86,55+min(20,abs(value)*2)+min(15,max(0,sample-8)*2))) if side else round(min(60,45+abs(value)))
    st=stake(bankroll,conf) if side and conf>=65 else 0
    if side and st>0:
        signal="BET"; action=f"GIOCA {side}"; text=f"Scommetti {side} {line}"
        reason=f"Fallback storico reale: media {avg:.1f}, mediana {med:.1f}, previsione {pred}, valore {value:+.1f} sulla linea {line}. Campione: {sample} partite."
        why=["Partita non trovata live","Usati risultati storici reali disponibili","Affidabilità limitata dal fallback storico","Stake prudente sul bankroll"]
    elif abs(value)>=4:
        signal="OBSERVE"; action="OSSERVA"; text="Non entrare ancora"
        reason=f"Fallback storico reale: previsione {pred}, valore {value:+.1f}, ma edge non abbastanza forte."
        why=["Dati storici presenti ma vantaggio non forte","Nessun monitoraggio live disponibile","Puntata consigliata 0 €"]
    else:
        signal="NO_BET"; action="NON GIOCARE"; text="Non scommettere"
        reason=f"Fallback storico reale: previsione {pred}, valore {value:+.1f}. Edge insufficiente."
        why=["Valore basso","Nessun live tracking","No bet prudenziale"]
    return {"signal":signal,"action":action,"decision_text":text,"side":side,"line":line,"stake":st,"confidence":conf,"score":score,"clock":clock,"teams":{"home":home,"away":away},"rhythm":"Storico","total_predicted":pred,"final_score":"stima storica","value":round(value,1),"prob_over":po,"prob_under":pu,"reason":reason,"why":why,"source":"historical-fallback","historical_sample":sample}

@app.route("/")
def home():return send_from_directory(".","index.html")

@app.route("/api/health")
def health():return jsonify({"ok":True,"api_basketball":bool(API_KEY),"telegram":bool(TG_TOKEN and TG_CHAT),"openai":bool(OPENAI_API_KEY)})

@app.route("/api/live/games")
def live_games():
    games=[norm(g) for g in api("games",{"live":"all"}).get("response",[])]
    return jsonify({"count":len(games),"games":games})

@app.route("/api/live/analyze",methods=["POST"])
def live_analyze():
    p=request.get_json(force=True)
    return jsonify(live_decide(fixture(p["fixture_id"]),float(p["bankroll"]),float(p["line"])))

@app.route("/api/screenshot/analyze",methods=["POST"])
def shot_analyze():
    f=request.files.get("image"); bankroll=float(request.form.get("bankroll") or 25)
    if not f:return jsonify({"error":"Nessuna immagine"}),400
    ex=extract(f.read(),f.mimetype or "image/jpeg")
    m=None
    try:m=match_live(ex)
    except Exception as e:print("[MATCH]",e)
    line=ex.get("lineOU"); decision=None
    if line is not None:
        decision=live_decide(m,bankroll,float(line)) if m else historical_decide(ex,bankroll,float(line))
    return jsonify({"extracted":ex,"match":m,"decision":decision,"mode":"api-live" if m else "historical-fallback"})

@app.route("/api/watch/start",methods=["POST"])
def watch():
    p=request.get_json(force=True)
    if p.get("source")=="historical-fallback" or p.get("fixture_id")=="historical-fallback":
        msg="⚠️ Monitoraggio live non disponibile: partita non trovata live. Per aggiornare, carica un nuovo screenshot."
        tg(msg); return jsonify({"ok":True,"message":msg})
    tg(f"✅ <b>Monitoraggio avviato</b>\nFixture ID: <b>{p.get('fixture_id')}</b>\nLinea: <b>{p.get('line')}</b>\nBankroll: <b>{p.get('bankroll')} €</b>")
    return jsonify({"ok":True,"message":"Monitoraggio avviato. Telegram attivo."})

@app.route("/api/bet/register",methods=["POST"])
def bet():
    p=request.get_json(force=True)
    tg(f"✅ <b>Giocata registrata</b>\n{str(p.get('side')).upper()} {p.get('line')}\nPuntata: {p.get('stake')} €\nFonte: {p.get('source') or 'app'}")
    return jsonify({"ok":True,"message":"Giocata registrata. Telegram attivo."})

@app.route("/api/bet/quality")
def quality():return jsonify({"message":"Se non c'è live tracking, ricarica uno screenshot aggiornato per rivalutare."})

@app.route("/api/telegram/test",methods=["POST"])
def telegram_test():
    tg("✅ MultiBasket AI PRO: notifiche Telegram attive.")
    return jsonify({"ok":True,"message":"Messaggio Telegram inviato."})
