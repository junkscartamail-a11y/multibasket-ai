import base64, json, os, re
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

app = FastAPI(title="MultiBasket AI Vision")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PROMPT = """Sei un estrattore di dati da screenshot basket live. Rispondi SOLO JSON:
{"homeScore":number|null,"awayScore":number|null,"quarter":1|2|3|4|null,"timeRemaining":"M:SS"|null,"lineOU":number|null,"oddsOver":number|null,"oddsUnder":number|null,"teams":{"home":string|null,"away":string|null},"confidence":number}
Non inventare dati non visibili. Se non vedi punteggio, quarto, tempo o linea, metti null."""

def parse_json(t):
    t = t.strip().replace("```json", "").replace("```", "").strip()
    m = re.search(r"\{.*\}", t, re.S)
    return json.loads(m.group(0) if m else t)

@app.get("/api/health")
def health():
    return {"ok": True}

@app.post("/api/analyze")
async def analyze(image: UploadFile = File(...)):
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return {
            "extracted": {
                "homeScore": None,
                "awayScore": None,
                "quarter": None,
                "timeRemaining": None,
                "lineOU": None,
                "oddsOver": None,
                "oddsUnder": None,
                "teams": {"home": None, "away": None},
                "confidence": 0
            },
            "error": "OPENAI_API_KEY missing"
        }

    raw = await image.read()
    b64 = base64.b64encode(raw).decode()
    mime = image.content_type or "image/jpeg"

    client = OpenAI(api_key=key)

    # Compatibile con la libreria OpenAI installata su Render.
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Estrai i dati live basket dallo screenshot. Rispondi solo JSON."},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
                ]
            }
        ],
        temperature=0
    )

    text = response.choices[0].message.content
    return {"extracted": parse_json(text)}

app.mount("/", StaticFiles(directory=".", html=True), name="static")
