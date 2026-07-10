import os, requests
TG_TOKEN=os.getenv('TELEGRAM_BOT_TOKEN','')
TG_CHAT=os.getenv('TELEGRAM_CHAT_ID','')
def telegram_ready(): return bool(TG_TOKEN and TG_CHAT)
def send_telegram(message):
    if not telegram_ready(): return False
    try:
        r=requests.post(f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage',json={'chat_id':TG_CHAT,'text':message,'parse_mode':'HTML','disable_web_page_preview':True},timeout=20)
        r.raise_for_status(); return True
    except Exception: return False
