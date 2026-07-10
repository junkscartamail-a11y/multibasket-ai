import os
from flask import Flask,request,jsonify,send_from_directory
from ai import extract_from_screenshot
from prediction import decide
from api_basket import api_ready,get_live_games,find_best_game,get_game_by_id,game_score_total,evaluate_bet_state
from telegram_bot import telegram_ready,send_telegram
OPENAI_API_KEY=os.getenv('OPENAI_API_KEY','')
app=Flask(__name__,static_folder='static',static_url_path='/static')
@app.route('/')
def home(): return send_from_directory('static','index.html')
@app.route('/api/health')
def health(): return jsonify({'ok':True,'openai':bool(OPENAI_API_KEY),'telegram':telegram_ready(),'basketball_api':api_ready(),'mode':'MultiBasket AI PRO 2.0'})
@app.route('/api/screenshot/analyze',methods=['POST'])
def screenshot_analyze():
    f=request.files.get('image'); bankroll=float(request.form.get('bankroll') or 25)
    if not f: return jsonify({'ok':False,'error':'Nessuna immagine caricata'}),400
    try:
        extracted=extract_from_screenshot(f.read(),f.mimetype or 'image/jpeg'); decision=decide(extracted,bankroll); return jsonify({'ok':True,'extracted':extracted,'decision':decision})
    except Exception as e: return jsonify({'ok':False,'error':str(e)}),500
@app.route('/api/live/list')
def live_list():
    try:
        games=get_live_games(); simplified=[]
        for g in games:
            h,a,total=game_score_total(g); teams=g.get('teams',{}); status=g.get('status',{})
            simplified.append({'id':g.get('id'),'home':teams.get('home',{}).get('name'),'away':teams.get('away',{}).get('name'),'score':f'{h}-{a}' if h is not None and a is not None else '-','total':total,'status':status.get('long') or status.get('short') or '-','league':(g.get('league') or {}).get('name'),'country':(g.get('country') or {}).get('name')})
        return jsonify({'ok':True,'count':len(simplified),'games':simplified})
    except Exception as e: return jsonify({'ok':False,'error':str(e),'games':[]}),500
@app.route('/api/live/find',methods=['POST'])
def live_find():
    p=request.get_json(force=True); home=p.get('homeTeam') or ''; away=p.get('awayTeam') or ''; hs=p.get('homeScore'); aw=p.get('awayScore')
    try:
        result=find_best_game(home,away,hs,aw)
        if not result['found']: return jsonify({'ok':False,'message':'Partita live non trovata con sicurezza','best_score':result['best_score'],'candidates':result['candidates'],'hint':'Se non compare nella lista live, API-Sports non la copre live.'}),404
        g=result['game']; h,a,total=game_score_total(g); teams=g.get('teams',{}); status=g.get('status',{})
        return jsonify({'ok':True,'game_id':g.get('id'),'home':teams.get('home',{}).get('name'),'away':teams.get('away',{}).get('name'),'score':f'{h}-{a}','total':total,'status':status,'match_score':result['best_score'],'candidates':result['candidates'],'message':'Partita agganciata correttamente'})
    except Exception as e: return jsonify({'ok':False,'error':str(e)}),500
@app.route('/api/live/check-now',methods=['POST'])
def live_check_now():
    p=request.get_json(force=True); game_id=p.get('game_id'); side=p.get('side'); line=p.get('line')
    if not game_id: return jsonify({'ok':False,'error':'game_id mancante'}),400
    try:
        game=get_game_by_id(game_id)
        if not game: return jsonify({'ok':False,'error':'Partita non trovata nell API'}),404
        h,a,total=game_score_total(game); teams=game.get('teams',{}); status=game.get('status',{}); bet_state=evaluate_bet_state(total,side,line) if side and line else 'Nessuna giocata registrata'
        msg=f"🏀 <b>{teams.get('home',{}).get('name')} - {teams.get('away',{}).get('name')}</b>\nRisultato live: <b>{h}-{a}</b>\nTotale attuale: <b>{total}</b>\nStato partita: {status.get('long') or status.get('short') or '-'}\nGiocata: <b>{str(side).upper()} {line}</b>\nSituazione: <b>{bet_state}</b>"
        sent=send_telegram(msg)
        return jsonify({'ok':True,'telegram_sent':sent,'message':'Aggiornamento Telegram inviato' if sent else 'Telegram non configurato o invio fallito','score':f'{h}-{a}','total':total,'bet_state':bet_state,'status':status})
    except Exception as e: return jsonify({'ok':False,'error':str(e)}),500
@app.route('/api/telegram/test',methods=['POST'])
def telegram_test():
    sent=send_telegram('✅ MultiBasket AI PRO 2.0: Telegram funziona.')
    if not sent: return jsonify({'ok':False,'message':'Telegram non configurato. Controlla TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID su Render.'}),400
    return jsonify({'ok':True,'message':'Messaggio Telegram inviato.'})
@app.route('/api/bet/register',methods=['POST'])
def bet_register():
    p=request.get_json(force=True); msg=f"✅ <b>Giocata registrata</b>\n{str(p.get('side')).upper()} {p.get('line')}\nPuntata: {p.get('stake')} €\nFonte: MultiBasket AI PRO 2.0"; sent=send_telegram(msg)
    return jsonify({'ok':True,'telegram_sent':sent,'message':'Giocata registrata.'})
if __name__=='__main__': app.run(host='0.0.0.0',port=int(os.getenv('PORT',10000)))
