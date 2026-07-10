import os,re,unicodedata,requests
from difflib import SequenceMatcher
from datetime import datetime,timezone
BASKETBALL_API_KEY=os.getenv('BASKETBALL_API_KEY','')
BASKETBALL_API_URL='https://v1.basketball.api-sports.io'
ALIASES={'capo verde':'cape verde','cabo verde':'cape verde','cape verde islands':'cape verde','sudan del sud':'south sudan','sud sudan':'south sudan','usa':'united states','u s a':'united states','bosnia':'bosnia and herzegovina'}
def api_ready(): return bool(BASKETBALL_API_KEY)
def normalize(t):
    t=str(t or '').lower().strip(); t=unicodedata.normalize('NFD',t); t=''.join(c for c in t if unicodedata.category(c)!='Mn'); t=re.sub(r'[^a-z0-9 ]',' ',t); t=re.sub(r'\s+',' ',t).strip()
    for k,v in ALIASES.items(): t=t.replace(k,v)
    remove={'bc','basket','club','national','team','women','w','u20','u18'}
    return ' '.join(w for w in t.split() if w not in remove).strip()
def similarity(a,b):
    a,b=normalize(a),normalize(b)
    if not a or not b:return 0
    if a in b or b in a:return .92
    return SequenceMatcher(None,a,b).ratio()
def api_get(path,params=None):
    if not BASKETBALL_API_KEY: raise RuntimeError('BASKETBALL_API_KEY missing')
    r=requests.get(f'{BASKETBALL_API_URL}{path}',headers={'x-apisports-key':BASKETBALL_API_KEY},params=params or {},timeout=20)
    if r.status_code==401: raise RuntimeError('API-Sports 401: chiave API non valida o non abilitata per Basketball')
    if r.status_code==429: raise RuntimeError('API-Sports limite richieste raggiunto')
    r.raise_for_status(); data=r.json()
    if data.get('errors'): raise RuntimeError(f"API-Sports error: {data.get('errors')}")
    return data
def get_live_games(): return api_get('/games',{'live':'all'}).get('response',[])
def get_today_games(): return api_get('/games',{'date':datetime.now(timezone.utc).strftime('%Y-%m-%d')}).get('response',[])
def get_game_by_id(game_id):
    games=api_get('/games',{'id':game_id}).get('response',[]); return games[0] if games else None
def game_score_total(g):
    scores=g.get('scores',{}); home=scores.get('home',{}); away=scores.get('away',{})
    h=home.get('total'); a=away.get('total')
    if h is None:h=home.get('score')
    if a is None:a=away.get('score')
    try: h=int(h); a=int(a); return h,a,h+a
    except Exception: return None,None,None
def score_bonus(api_h,api_a,th,ta):
    try: th=int(float(th)); ta=int(float(ta))
    except Exception: return 0
    if api_h is None or api_a is None:return 0
    if api_h==th and api_a==ta:return .70
    if api_h==ta and api_a==th:return .55
    diff=min(abs(api_h-th)+abs(api_a-ta),abs(api_h-ta)+abs(api_a-th))
    if diff<=2:return .45
    if diff<=5:return .25
    return 0
def candidate(g,home,away,hs,as_):
    teams=g.get('teams',{}); gh=teams.get('home',{}).get('name',''); ga=teams.get('away',{}).get('name','')
    direct=similarity(home,gh)+similarity(away,ga); reverse=similarity(home,ga)+similarity(away,gh); api_h,api_a,total=game_score_total(g); sb=score_bonus(api_h,api_a,hs,as_); base=max(direct,reverse); final=base+sb
    return {'score_value':round(final,3),'name_score':round(base,3),'score_bonus':round(sb,3),'id':g.get('id'),'home':gh,'away':ga,'score':f'{api_h}-{api_a}' if api_h is not None and api_a is not None else '-','total':total,'status':(g.get('status') or {}).get('long') or (g.get('status') or {}).get('short') or '-','league':(g.get('league') or {}).get('name'),'country':(g.get('country') or {}).get('name'),'game':g}
def find_best_game(home,away,hs=None,as_=None):
    games=get_live_games(); source='live'
    if not games: games=get_today_games(); source='today'
    cands=sorted([candidate(g,home,away,hs,as_) for g in games],key=lambda x:x['score_value'],reverse=True)
    public=[{k:v for k,v in c.items() if k!='game'} for c in cands[:8]]
    if not cands: return {'found':False,'best_score':0,'game':None,'source':source,'candidates':[]}
    best=cands[0]; found=best['score_value']>=1.05 or (best['score_bonus']>=.45 and best['name_score']>=.55)
    return {'found':found,'best_score':best['score_value'],'game':best['game'] if found else None,'source':source,'candidates':public}
def evaluate_bet_state(total,side,line):
    side=str(side or '').upper(); line=float(line)
    if total is None: return 'Dati punteggio non disponibili'
    if side=='OVER':
        if total>line:return '✅ già sopra linea'
        if total>=line-8:return '⚠️ viva ma al limite'
        return '❌ in difficoltà'
    if side=='UNDER':
        if total<line:return '✅ in vantaggio'
        if total<=line+8:return '⚠️ ancora viva ma al limite'
        return '❌ in difficoltà'
    return 'NO BET'
