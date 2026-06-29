import os, sqlite3, math, time, threading, requests
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB='multibasket_pro.db'
API_BASKETBALL_KEY=os.getenv('API_BASKETBALL_KEY','')
TELEGRAM_BOT_TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','')
TELEGRAM_CHAT_ID=os.getenv('TELEGRAM_CHAT_ID','')
CHECK_INTERVAL_SECONDS=int(os.getenv('CHECK_INTERVAL_SECONDS','60'))

app=FastAPI(title='MultiBasket AI PRO')
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

def con():
    c=sqlite3.connect(DB,check_same_thread=False)
    c.execute("CREATE TABLE IF NOT EXISTS watches(id INTEGER PRIMARY KEY AUTOINCREMENT, fixture_id TEXT UNIQUE, bankroll REAL, line REAL, odds_over REAL, odds_under REAL, active INTEGER DEFAULT 1, last_signal TEXT, last_confidence INTEGER DEFAULT 0, last_probability INTEGER DEFAULT 0, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS bets(id INTEGER PRIMARY KEY AUTOINCREMENT, fixture_id TEXT, side TEXT, line REAL, stake REAL, bankroll REAL, active INTEGER DEFAULT 1, last_probability INTEGER DEFAULT 0, last_quality TEXT, created_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, fixture_id TEXT, message TEXT, created_at TEXT)")
    c.commit(); return c

def log_event(kind, fixture_id, message):
    try:
        c=con(); c.execute('INSERT INTO events(kind,fixture_id,message,created_at) VALUES(?,?,?,?)',(kind,fixture_id,message,datetime.now().isoformat())); c.commit(); c.close()
    except Exception as e: print('log error',e)

class WatchReq(BaseModel):
    fixture_id:str; bankroll:float; line:float; odds_over:float=1.85; odds_under:float=1.85
class BetReq(BaseModel):
    fixture_id:str; side:str; line:float; stake:float; bankroll:float

def tg_send(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print('[TELEGRAM NOT CONFIGURED]',msg); return False
    r=requests.post(f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',json={'chat_id':TELEGRAM_CHAT_ID,'text':msg,'parse_mode':'HTML'},timeout=20)
    r.raise_for_status(); return True

def api_call(path,params):
    if not API_BASKETBALL_KEY: raise RuntimeError('API_BASKETBALL_KEY missing')
    r=requests.get(f'https://v1.basketball.api-sports.io/{path}',headers={'x-apisports-key':API_BASKETBALL_KEY},params=params,timeout=20)
    r.raise_for_status(); return r.json()

def clock_minutes(clock):
    if clock is None: return 0.0
    s=str(clock).strip()
    try:
        if ':' in s:
            m,sec=s.split(':')[:2]; return int(m)+int(sec)/60
        return float(s)
    except Exception: return 0.0

def normalize(g):
    teams=g.get('teams',{}) or {}; scores=g.get('scores',{}) or {}; status=g.get('status',{}) or {}
    hs=(scores.get('home',{}) or {}).get('total') or 0; aw=(scores.get('away',{}) or {}).get('total') or 0
    short=str(status.get('short') or '').upper(); q=4
    for ch in short:
        if ch.isdigit(): q=int(ch); break
    if q<1 or q>4: q=4
    return {'fixture_id':str(g.get('id')),'home':(teams.get('home',{}) or {}).get('name') or 'Casa','away':(teams.get('away',{}) or {}).get('name') or 'Ospite','home_score':int(hs),'away_score':int(aw),'quarter':q,'clock':status.get('timer') or status.get('clock') or status.get('elapsed') or '0:00','status':status.get('long') or status.get('short') or '', 'league':(g.get('league') or {}).get('name') or ''}

def get_fixture(fixture_id):
    data=api_call('games',{'id':fixture_id}); resp=data.get('response',[])
    if not resp: raise ValueError('Fixture non trovata')
    return normalize(resp[0])

def logistic(x): return 1/(1+math.exp(-x))
def rhythm(p):
    if p<2.4: return 'Molto lento'
    if p<2.8: return 'Lento'
    if p<3.2: return 'Medio'
    if p<3.6: return 'Veloce'
    return 'Molto veloce'
def stake_from_bankroll(bankroll,conf):
    pct=.06 if conf>=88 else .045 if conf>=80 else .03 if conf>=70 else .02 if conf>=65 else 0
    return max(0,round(bankroll*pct))

def model(live,bankroll,line,odds_over=1.85,odds_under=1.85):
    h=live['home_score']; a=live['away_score']; total=h+a; q=int(live['quarter']); left=clock_minutes(live['clock'])
    played=max(.1,(q-1)*10+(10-left)); rem=max(0,40-played); pace=total/played
    phase={1:.98,2:.96,3:.94,4:.90}.get(q,.90); adj=max(1.65,min(5.05 if q==4 else 5.35, pace*phase))
    raw=total+rem*adj; corr=max(-3,min(3,(line-raw)*.10)); pred=round(raw+corr); value=pred-line
    share=h/total if total else .5; fh=round(pred*share); fa=round(pred-fh)
    po=round(logistic(value/5.2)*100); pu=100-po; threshold=8 if played>=8 else 10
    side='OVER' if value>=threshold else 'UNDER' if value<=-threshold else None
    conf=round(min(100,min(55,abs(value)*4)+min(20,played/40*28)+15+(10 if bankroll>=20 else 5))) if side else 0
    st=stake_from_bankroll(bankroll,conf); signal='BET' if side and conf>=65 and st>0 else ('OBSERVE' if abs(value)>=4 else 'NO_BET')
    reason=(f'{side} con valore {value:+.1f}: totale previsto {pred}, linea {line}.' if signal=='BET' else f'Osserva: valore {value:+.1f}, non ancora sufficiente.' if signal=='OBSERVE' else f'No bet: valore {value:+.1f} insufficiente rispetto alla linea {line}.')
    return {'signal':signal,'side':side,'line':line,'stake':st,'confidence':conf,'score':f'{h}-{a}','clock':f"{live['clock']} Q{q}",'teams':{'home':live['home'],'away':live['away']},'league':live['league'],'rhythm':rhythm(pace),'total_predicted':pred,'final_score':f'{fh}-{fa}','value':round(value,1),'prob_over':po,'prob_under':pu,'win_probability':po if side=='OVER' else pu if side=='UNDER' else max(po,pu),'reason':reason,'live':live}

def fmt_signal(d):
    side=d.get('side') or 'NO BET'
    return f"🏀 <b>MultiBasket AI</b>\n\n{d['teams']['home']} - {d['teams']['away']}\nPunteggio: <b>{d['score']}</b>\nTempo: <b>{d['clock']}</b>\n\nSegnale: <b>{side} {d['line'] if d.get('side') else ''}</b>\nPuntata: <b>{d['stake']} €</b>\nAffidabilità: <b>{d['confidence']}/100</b>\nTotale previsto: <b>{d['total_predicted']}</b>\nFinale stimato: <b>{d['final_score']}</b>\nValore: <b>{d['value']:+.1f}</b>\nRitmo: <b>{d['rhythm']}</b>\n\n{d['reason']}"

def quality(side,d):
    p=d['prob_over'] if side.upper()=='OVER' else d['prob_under']
    q='Ottima' if p>=80 else 'Buona' if p>=65 else 'In bilico' if p>=52 else 'A rischio'
    return p,q

def monitor_loop():
    print('[MONITOR] started')
    while True:
        try:
            c=con()
            for fixture_id,bankroll,line,oo,ou,last_signal,last_conf,last_prob in c.execute('SELECT fixture_id,bankroll,line,odds_over,odds_under,last_signal,last_confidence,last_probability FROM watches WHERE active=1').fetchall():
                try:
                    d=model(get_fixture(fixture_id),bankroll,line,oo,ou)
                    if d['signal']=='BET' and (last_signal!='BET' or abs(d['confidence']-int(last_conf or 0))>=8):
                        msg=fmt_signal(d); tg_send(msg); log_event('SIGNAL',fixture_id,msg)
                    c.execute('UPDATE watches SET last_signal=?,last_confidence=?,last_probability=? WHERE fixture_id=?',(d['signal'],d['confidence'],d['win_probability'],fixture_id))
                except Exception as e: print('[WATCH ERROR]',fixture_id,e)
            for bet_id,fixture_id,side,line,bet_stake,bankroll,last_prob,last_q in c.execute('SELECT id,fixture_id,side,line,stake,bankroll,last_probability,last_quality FROM bets WHERE active=1').fetchall():
                try:
                    d=model(get_fixture(fixture_id),bankroll,line); p,q=quality(side,d)
                    if (not last_q) or abs(p-int(last_prob or 0))>=10 or q!=last_q:
                        msg=f"📊 <b>Aggiornamento giocata</b>\n\n{d['teams']['home']} - {d['teams']['away']}\nPunteggio: <b>{d['score']}</b>\nTempo: <b>{d['clock']}</b>\n\nLa tua giocata: <b>{side.upper()} {line}</b>\nPuntata: <b>{bet_stake} €</b>\nTotale previsto ora: <b>{d['total_predicted']}</b>\nProbabilità stimata di vincita: <b>{p}%</b>\nQualità: <b>{q}</b>"
                        tg_send(msg); log_event('BET_QUALITY',fixture_id,msg)
                    c.execute('UPDATE bets SET last_probability=?,last_quality=? WHERE id=?',(p,q,bet_id))
                except Exception as e: print('[BET ERROR]',fixture_id,e)
            c.commit(); c.close()
        except Exception as e: print('[MONITOR ERROR]',e)
        time.sleep(CHECK_INTERVAL_SECONDS)

@app.on_event('startup')
def startup():
    c=con(); c.close(); threading.Thread(target=monitor_loop,daemon=True).start()

@app.get('/api/health')
def health(): return {'ok':True,'api_basketball':bool(API_BASKETBALL_KEY),'telegram':bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),'interval':CHECK_INTERVAL_SECONDS}
@app.get('/api/live/games')
def live_games():
    data=api_call('games',{'live':'all'}); games=[normalize(g) for g in data.get('response',[])]
    return {'count':len(games),'games':games}
@app.post('/api/live/analyze')
def analyze(req:WatchReq): return model(get_fixture(req.fixture_id),req.bankroll,req.line,req.odds_over,req.odds_under)
@app.post('/api/watch/start')
def watch_start(req:WatchReq):
    c=con(); c.execute('INSERT INTO watches(fixture_id,bankroll,line,odds_over,odds_under,active,created_at) VALUES(?,?,?,?,?,1,?) ON CONFLICT(fixture_id) DO UPDATE SET bankroll=excluded.bankroll,line=excluded.line,odds_over=excluded.odds_over,odds_under=excluded.odds_under,active=1',(req.fixture_id,req.bankroll,req.line,req.odds_over,req.odds_under,datetime.now().isoformat())); c.commit(); c.close()
    return {'ok':True,'message':'Monitoraggio automatico avviato. Riceverai notifiche Telegram se compare un segnale.'}
@app.post('/api/bet/register')
def bet_register(req:BetReq):
    c=con(); c.execute('INSERT INTO bets(fixture_id,side,line,stake,bankroll,active,created_at) VALUES(?,?,?,?,?,1,?)',(req.fixture_id,req.side.upper(),req.line,req.stake,req.bankroll,datetime.now().isoformat())); c.commit(); c.close()
    return {'ok':True,'message':'Giocata registrata. Riceverai notifiche Telegram sulla qualità della puntata.'}
@app.get('/api/bet/quality')
def bet_quality(fixture_id:str):
    c=con(); row=c.execute('SELECT side,line,stake,bankroll FROM bets WHERE fixture_id=? AND active=1 ORDER BY id DESC LIMIT 1',(fixture_id,)).fetchone(); c.close()
    if not row: return {'error':'Nessuna giocata attiva.'}
    side,line,st,bankroll=row; d=model(get_fixture(fixture_id),bankroll,line); p,q=quality(side,d)
    return {'score':d['score'],'clock':d['clock'],'total_predicted':d['total_predicted'],'win_probability':p,'quality':q,'message':f"La partita è {d['score']} a {d['clock']}. La tua giocata {side} {line} ha probabilità stimata {p}%: qualità {q}."}
@app.post('/api/telegram/test')
def telegram_test(): tg_send('✅ MultiBasket AI: notifiche Telegram attive.'); return {'ok':True,'message':'Messaggio Telegram inviato.'}
@app.get('/api/events')
def events():
    c=con(); rows=c.execute('SELECT kind,fixture_id,message,created_at FROM events ORDER BY id DESC LIMIT 50').fetchall(); c.close()
    return {'events':[{'kind':r[0],'fixture_id':r[1],'message':r[2],'created_at':r[3]} for r in rows]}
app.mount('/', StaticFiles(directory='.', html=True), name='static')
