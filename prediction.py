import math
MARGINE_SICUREZZA=7
def sf(x):
    try:
        if x is None or x=='': return None
        return float(x)
    except Exception: return None
def clock_min(x):
    try:
        s=str(x or '0:00').strip().replace(' ','')
        if s in ['0:0','0.0']: return 0
        if ':' in s:
            m,sec=s.split(':')[:2]; return int(m)+int(sec)/60
        return float(s)
    except Exception: return None
def ritmo(ppm):
    if ppm<2.45:return 'molto lento'
    if ppm<2.85:return 'lento'
    if ppm<3.25:return 'medio'
    if ppm<3.65:return 'veloce'
    return 'molto veloce'
def prob(v): return max(5,min(95,round(100/(1+math.exp(-v/5.8)))))
def stake(bankroll,conf,value):
    if conf<68:return 0
    e=abs(value); pct=.05 if e>=14 and conf>=82 else .035 if e>=10 and conf>=74 else .025
    return max(1,round(bankroll*pct))
def qtot(ex,h,a):
    x,y=sf(ex.get(h)),sf(ex.get(a)); return None if x is None or y is None else x+y
def choose_line(ex):
    valid=[]
    for it in ex.get('ouLines') or []:
        line,over,under=sf(it.get('line')),sf(it.get('over')),sf(it.get('under'))
        if line is not None: valid.append({'line':line,'over':over,'under':under})
    if valid:
        c=sorted(valid,key=lambda z:z['line'])[0]; return c['line'],c['over'],c['under'],valid
    return sf(ex.get('lineOU')),sf(ex.get('oddsOver')),sf(ex.get('oddsUnder')),[]
def trend(q1,q2,q3,q4):
    f=1.0; d='trend quarti non disponibile'
    if q1 is not None and q2 is not None:
        diff=q2-q1
        if diff<=-8:f*=.92; d=f'rallentamento tra Q1 e Q2 ({diff:+.0f} punti)'
        elif diff<=-5:f*=.95; d=f'leggero rallentamento tra Q1 e Q2 ({diff:+.0f} punti)'
        elif diff>=8:f*=1.08; d=f'accelerazione tra Q1 e Q2 ({diff:+.0f} punti)'
        elif diff>=5:f*=1.05; d=f'leggera accelerazione tra Q1 e Q2 ({diff:+.0f} punti)'
        else:d=f'ritmo stabile tra Q1 e Q2 ({diff:+.0f} punti)'
    if q2 is not None and q3 is not None:
        diff=q3-q2
        if diff<=-8:f*=.94; d=f'forte rallentamento recente ({diff:+.0f} punti)'
        elif diff<=-5:f*=.97; d=f'rallentamento recente ({diff:+.0f} punti)'
        elif diff>=8:f*=1.06; d=f'forte accelerazione recente ({diff:+.0f} punti)'
        elif diff>=5:f*=1.03; d=f'accelerazione recente ({diff:+.0f} punti)'
    return f,d
def nb(home,away,score,clock,reason,why,line='-',extracted=None):
    return {'signal':'NO_BET','action':'NO BET','decision_text':'Non scommettere','side':None,'line':line,'stake':0,'confidence':0,'score':score,'clock':clock,'teams':{'home':home,'away':away},'rhythm':'non valutabile','ppm':'-','played':'-','remaining':'-','total_predicted':'-','final_score':'-','value':0,'prob_over':50,'prob_under':50,'over_wait_line':'-','under_wait_line':'-','q1_total':None,'q2_total':None,'q3_total':None,'q4_total':None,'trend_desc':'-','reason':reason,'why':why,'source':'MultiBasket AI PRO 2.0','extracted':extracted or {}}
def decide(ex,bankroll):
    home=ex.get('homeTeam') or 'Squadra A'; away=ex.get('awayTeam') or 'Squadra B'; line,oo,ou,all_lines=choose_line(ex)
    missing=[k for k,v in {'homeScore':ex.get('homeScore'),'awayScore':ex.get('awayScore'),'quarter':ex.get('quarter'),'timeRemaining':ex.get('timeRemaining'),'lineOU':line}.items() if v is None]
    if missing: return nb(home,away,'-','-','Dati mancanti: '+', '.join(missing),['Mostra JSON per capire cosa manca','Puntata consigliata 0 €'],extracted=ex)
    h,a=int(float(ex['homeScore'])),int(float(ex['awayScore'])); total=h+a; q=int(float(ex['quarter'])); left=clock_min(ex['timeRemaining']); line=float(line)
    if left is None or q<1 or q>4 or left<0 or left>10: return nb(home,away,f'{h}-{a}',f"{ex.get('timeRemaining')} Q{q}",'Cronometro o quarto non affidabili.',['Puntata consigliata 0 €'],line,ex)
    played=(q-1)*10+(10-left); rem=max(0,40-played); played=.1 if played<=0 else played; ppm=total/played
    q1,q2,q3,q4=qtot(ex,'q1Home','q1Away'),qtot(ex,'q2Home','q2Away'),qtot(ex,'q3Home','q3Away'),qtot(ex,'q4Home','q4Away')
    tf,td=trend(q1,q2,q3,q4); phase={1:.97,2:.95,3:.93,4:.88}.get(q,.90); fatigue=.96 if q>=3 else 1; blowout=.91 if abs(h-a)>=22 and q>=3 else 1; close=.03 if abs(h-a)<=8 and q>=4 else 0
    cppm=max(1.70,min(4.85,ppm*phase*fatigue*blowout*(1+close)*tf)); raw=total+rem*cppm; shrink=.82 if played<20 else .88; pred=round(line+(raw-line)*shrink); value=pred-line
    po=prob(value); pu=100-po; ow=round(pred-MARGINE_SICUREZZA,1); uw=round(pred+MARGINE_SICUREZZA,1)
    side='OVER' if value>=MARGINE_SICUREZZA else 'UNDER' if value<=-MARGINE_SICUREZZA else None
    conf=22+(18 if oo and ou else 8)+(20 if played>=8 else 8)+(25 if abs(value)>=12 else 18 if abs(value)>=9 else 10 if abs(value)>=7 else 0)+(8 if home!='Squadra A' and away!='Squadra B' else 0)+(8 if q1 is not None and q2 is not None else 0)
    if played<5: conf-=10
    conf=max(0,min(90,round(conf))); st=stake(bankroll,conf,value) if side else 0; share=h/total if total else .5; fh=round(pred*share); fa=round(pred-fh)
    if side and conf>=68 and st>0: signal='BET'; action=f'GIOCA {side}'; text=f'Scommetti {side} {line}'; reason=f'Margine sufficiente. Totale previsto {pred}, linea {line}, margine {value:+.1f}.'
    elif abs(value)>=4: signal='OBSERVE'; action='ASPETTA'; text='Aspetta una linea più conveniente'; reason=f'Margine non abbastanza sicuro. Totale previsto {pred}, linea {line}, margine {value:+.1f}.'
    else: signal='NO_BET'; action='NO BET'; text='Non scommettere'; reason=f'Linea troppo vicina. Totale previsto {pred}, linea {line}, margine {value:+.1f}.'
    why=[f'Totale previsto: {pred}',f'Linea bookmaker scelta: {line}',f'Margine: {value:+.1f}',f'Ritmo attuale: {ppm:.2f} punti/min',f'Trend ritmo: {td}',f'Q1: {q1 if q1 is not None else "-"} punti',f'Q2: {q2 if q2 is not None else "-"} punti',f'Q3: {q3 if q3 is not None else "-"} punti',f'OVER giocabile a {ow} o meno',f'UNDER giocabile da {uw} o più']
    return {'signal':signal,'action':action,'decision_text':text,'side':side,'line':line,'stake':st,'confidence':conf,'score':f'{h}-{a}','clock':f"{ex.get('timeRemaining')} Q{q}",'teams':{'home':home,'away':away},'rhythm':ritmo(ppm),'ppm':round(ppm,2),'played':round(played,1),'remaining':round(rem,1),'total_predicted':pred,'final_score':f'{fh}-{fa}','value':round(value,1),'prob_over':po,'prob_under':pu,'over_wait_line':ow,'under_wait_line':uw,'q1_total':q1,'q2_total':q2,'q3_total':q3,'q4_total':q4,'trend_desc':td,'trend_factor':round(tf,3),'all_lines':all_lines,'reason':reason,'why':why,'source':'MultiBasket AI PRO 2.0','extracted':ex}
