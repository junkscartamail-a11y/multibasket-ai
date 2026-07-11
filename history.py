import json
import os

FILE = "history.json"


def load_history():
    if not os.path.exists(FILE):
        return []

    with open(FILE, "r") as f:
        try:
            return json.load(f)
        except:
            return []


def save_history(data):
    with open(FILE, "w") as f:
        json.dump(data, f, indent=2)


def add_bet(bet):
    data = load_history()
    data.append(bet)
    save_history(data)


def update_result(index, final_total):
    data = load_history()

    if index >= len(data):
        return None

    bet = data[index]

    line = bet.get("line")
    side = bet.get("side")

    if line is None or side is None:
        return None

    result = None

    if side == "OVER":
        result = "WIN" if final_total > line else "LOSS"
    elif side == "UNDER":
        result = "WIN" if final_total < line else "LOSS"

    bet["final_total"] = final_total
    bet["result"] = result

    save_history(data)

    return bet


def get_stats():
    data = load_history()

    if not data:
        return {
            "bets": 0,
            "wins": 0,
            "losses": 0,
            "roi": 0,
            "winrate": 0
        }

    wins = 0
    losses = 0
    profit = 0

    for bet in data:
        if bet.get("result") == "WIN":
            wins += 1
            profit += bet.get("stake", 0)
        elif bet.get("result") == "LOSS":
            losses += 1
            profit -= bet.get("stake", 0)

    total = wins + losses

    roi = (profit / total) if total > 0 else 0
    winrate = (wins / total * 100) if total > 0 else 0

    return {
        "bets": total,
        "wins": wins,
        "losses": losses,
        "roi": round(roi, 2),
        "winrate": round(winrate, 1)
    }
