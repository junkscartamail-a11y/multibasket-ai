import os,json,re,base64
from openai import OpenAI
OPENAI_API_KEY=os.getenv('OPENAI_API_KEY','')
OPENAI_MODEL=os.getenv('OPENAI_MODEL','gpt-4o-mini')
def parse_json(t):
    c=(t or '').replace('```json','').replace('```','').strip(); m=re.search(r'\{.*\}',c,re.S); return json.loads(m.group(0) if m else c)
def extract_from_screenshot(raw,mime):
    if not OPENAI_API_KEY: raise RuntimeError('OPENAI_API_KEY missing')
    b64=base64.b64encode(raw).decode(); client=OpenAI(api_key=OPENAI_API_KEY)
    prompt='''Leggi screenshot basket live bookmaker. Rispondi SOLO JSON valido. Se dato non leggibile usa null. Se cronometro 0:0 usa 0:00. Se intervallo/fine secondo quarto: quarter=2,timeRemaining="0:00". Se vedi più linee U/O mettile in ouLines e scegli come lineOU la più bassa visibile. JSON: {"homeTeam":string|null,"awayTeam":string|null,"homeScore":number|null,"awayScore":number|null,"quarter":number|null,"timeRemaining":"M:SS"|null,"lineOU":number|null,"oddsOver":number|null,"oddsUnder":number|null,"ouLines":[{"line":number,"over":number|null,"under":number|null}],"q1Home":number|null,"q1Away":number|null,"q2Home":number|null,"q2Away":number|null,"q3Home":number|null,"q3Away":number|null,"q4Home":number|null,"q4Away":number|null,"confidence":number}'''
    r=client.chat.completions.create(model=OPENAI_MODEL,messages=[{'role':'system','content':prompt},{'role':'user','content':[{'type':'text','text':'Leggi lo screenshot e restituisci solo JSON.'},{'type':'image_url','image_url':{'url':f'data:{mime};base64,{b64}'}}]}],temperature=0)
    return parse_json(r.choices[0].message.content)
