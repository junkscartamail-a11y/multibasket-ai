import math


MARGINE_SICUREZZA = 7


def sf(value):
    """
    Converte un valore in float.
    Restituisce None quando il valore è vuoto o non valido.
    """
    try:
        if value is None or value == "":
            return None

        return float(value)

    except (TypeError, ValueError):
        return None


def clock_min(value):
    """
    Converte il cronometro M:SS in minuti decimali.
    """
    try:
        text = str(value or "0:00").strip().replace(" ", "")

        if text in ["0:0", "0.0", "0"]:
            return 0.0

        if ":" in text:
            minutes, seconds = text.split(":")[:2]

            return int(minutes) + int(seconds) / 60

        return float(text)

    except (TypeError, ValueError):
        return None


def ritmo(ppm):
    """
    Descrizione testuale del ritmo della partita.
    """
    if ppm < 2.45:
        return "molto lento"

    if ppm < 2.85:
        return "lento"

    if ppm < 3.25:
        return "medio"

    if ppm < 3.65:
        return "veloce"

    return "molto veloce"


def prob(value):
    """
    Trasforma il margine stimato in una probabilità indicativa.
    """
    return max(
        5,
        min(
            95,
            round(
                100 / (
                    1 + math.exp(-value / 5.8)
                )
            )
        )
    )


def stake(bankroll, confidence, value):
    """
    Calcola la puntata consigliata in base a:
    - bankroll;
    - affidabilità;
    - margine sulla linea.
    """
    try:
        bankroll = float(bankroll)
    except (TypeError, ValueError):
        bankroll = 25

    if bankroll <= 0:
        return 0

    if confidence < 68:
        return 0

    edge = abs(value)

    if edge >= 14 and confidence >= 82:
        percentage = 0.05

    elif edge >= 10 and confidence >= 74:
        percentage = 0.035

    else:
        percentage = 0.025

    suggested = bankroll * percentage

    if suggested < 1:
        return 1

    return round(suggested, 2)


def qtot(extracted, home_key, away_key):
    """
    Calcola il totale di un quarto.
    """
    home_points = sf(
        extracted.get(home_key)
    )

    away_points = sf(
        extracted.get(away_key)
    )

    if (
        home_points is None
        or away_points is None
    ):
        return None

    return home_points + away_points


def choose_line(extracted):
    """
    Legge le linee U/O visibili.
    Se sono presenti più linee, sceglie quella più bassa,
    mantenendo comunque tutte le linee disponibili.
    """
    valid_lines = []

    for item in extracted.get("ouLines") or []:
        line = sf(
            item.get("line")
        )

        over = sf(
            item.get("over")
        )

        under = sf(
            item.get("under")
        )

        if line is not None:
            valid_lines.append({
                "line": line,
                "over": over,
                "under": under
            })

    if valid_lines:
        chosen = sorted(
            valid_lines,
            key=lambda row: row["line"]
        )[0]

        return (
            chosen["line"],
            chosen["over"],
            chosen["under"],
            valid_lines
        )

    return (
        sf(extracted.get("lineOU")),
        sf(extracted.get("oddsOver")),
        sf(extracted.get("oddsUnder")),
        []
    )


def completed_quarters(quarter, time_left):
    """
    Determina quali quarti dovrebbero essere già completi.

    Esempi:
    - durante Q2: Q1 deve essere disponibile;
    - fine Q2/intervallo: devono esserci Q1 e Q2;
    - durante Q4: devono esserci Q1, Q2 e Q3;
    - fine Q4: devono esserci tutti i quarti.
    """
    if quarter is None:
        return []

    try:
        quarter = int(quarter)
    except (TypeError, ValueError):
        return []

    complete = list(
        range(1, quarter)
    )

    if (
        time_left is not None
        and time_left <= 0
        and 1 <= quarter <= 4
    ):
        complete.append(quarter)

    return sorted(
        set(complete)
    )


def missing_quarter_fields(
    extracted,
    quarter,
    time_left
):
    """
    Restituisce i campi mancanti dei quarti già conclusi.
    """
    missing = []

    for number in completed_quarters(
        quarter,
        time_left
    ):
        home_key = f"q{number}Home"
        away_key = f"q{number}Away"

        if sf(
            extracted.get(home_key)
        ) is None:
            missing.append(home_key)

        if sf(
            extracted.get(away_key)
        ) is None:
            missing.append(away_key)

    return missing


def missing_quarter_numbers(
    extracted,
    quarter,
    time_left
):
    """
    Restituisce i numeri dei quarti mancanti.
    """
    missing_numbers = []

    for number in completed_quarters(
        quarter,
        time_left
    ):
        home_value = sf(
            extracted.get(
                f"q{number}Home"
            )
        )

        away_value = sf(
            extracted.get(
                f"q{number}Away"
            )
        )

        if (
            home_value is None
            or away_value is None
        ):
            missing_numbers.append(
                number
            )

    return missing_numbers


def trend(q1, q2, q3, q4):
    """
    Analizza accelerazione o rallentamento
    fra i parziali dei quarti.
    """
    factor = 1.0
    descriptions = []

    quarter_values = [
        q1,
        q2,
        q3,
        q4
    ]

    available = [
        value
        for value in quarter_values
        if value is not None
    ]

    if len(available) < 2:
        return (
            factor,
            "trend quarti non disponibile"
        )

    previous = None
    previous_number = None

    for index, current in enumerate(
        quarter_values,
        start=1
    ):
        if current is None:
            continue

        if previous is not None:
            difference = (
                current - previous
            )

            if difference <= -8:
                factor *= 0.93

                descriptions.append(
                    f"forte rallentamento "
                    f"Q{previous_number}→Q{index} "
                    f"({difference:+.0f})"
                )

            elif difference <= -5:
                factor *= 0.96

                descriptions.append(
                    f"rallentamento "
                    f"Q{previous_number}→Q{index} "
                    f"({difference:+.0f})"
                )

            elif difference >= 8:
                factor *= 1.07

                descriptions.append(
                    f"forte accelerazione "
                    f"Q{previous_number}→Q{index} "
                    f"({difference:+.0f})"
                )

            elif difference >= 5:
                factor *= 1.04

                descriptions.append(
                    f"accelerazione "
                    f"Q{previous_number}→Q{index} "
                    f"({difference:+.0f})"
                )

            else:
                descriptions.append(
                    f"ritmo stabile "
                    f"Q{previous_number}→Q{index} "
                    f"({difference:+.0f})"
                )

        previous = current
        previous_number = index

    if not descriptions:
        description = (
            "trend quarti non disponibile"
        )

    else:
        description = " · ".join(
            descriptions
        )

    factor = max(
        0.84,
        min(
            1.16,
            factor
        )
    )

    return (
        factor,
        description
    )


def no_bet(
    home,
    away,
    score,
    clock,
    reason,
    why,
    line="-",
    extracted=None,
    missing_quarters=None
):
    """
    Risposta NO BET standard.
    """
    missing_quarters = (
        missing_quarters or []
    )

    return {
        "signal": "NO_BET",
        "action": "NO BET",
        "decision_text": "Non scommettere",
        "side": None,
        "line": line,
        "stake": 0,
        "confidence": 0,
        "score": score,
        "clock": clock,
        "teams": {
            "home": home,
            "away": away
        },
        "rhythm": "non valutabile",
        "ppm": "-",
        "played": "-",
        "remaining": "-",
        "total_predicted": "-",
        "final_score": "-",
        "value": 0,
        "prob_over": 50,
        "prob_under": 50,
        "over_wait_line": "-",
        "under_wait_line": "-",
        "q1_total": None,
        "q2_total": None,
        "q3_total": None,
        "q4_total": None,
        "trend_desc": "-",
        "reason": reason,
        "why": why,
        "source": "MultiBasket AI PRO 2.1",
        "extracted": extracted or {},
        "needs_quarters": bool(
            missing_quarters
        ),
        "missing_quarters": (
            missing_quarters
        )
    }


def decide(extracted, bankroll):
    """
    Funzione principale per il pronostico.

    Usa:
    - punteggio;
    - quarto;
    - cronometro;
    - linea bookmaker;
    - parziali dei quarti;
    - bankroll.
    """
    home = (
        extracted.get("homeTeam")
        or "Squadra A"
    )

    away = (
        extracted.get("awayTeam")
        or "Squadra B"
    )

    (
        line,
        odds_over,
        odds_under,
        all_lines
    ) = choose_line(extracted)

    required_data = {
        "homeScore": (
            extracted.get("homeScore")
        ),
        "awayScore": (
            extracted.get("awayScore")
        ),
        "quarter": (
            extracted.get("quarter")
        ),
        "timeRemaining": (
            extracted.get(
                "timeRemaining"
            )
        ),
        "lineOU": line
    }

    missing_data = [
        key
        for key, value
        in required_data.items()
        if value is None
    ]

    if missing_data:
        return no_bet(
            home=home,
            away=away,
            score="-",
            clock="-",
            reason=(
                "Dati mancanti: "
                + ", ".join(missing_data)
            ),
            why=[
                (
                    "Lo screenshot non ha "
                    "fornito tutti i dati "
                    "necessari"
                ),
                (
                    "Mostra JSON per vedere "
                    "quale dato manca"
                ),
                "Puntata consigliata 0 €"
            ],
            extracted=extracted
        )

    try:
        home_score = int(
            float(
                extracted["homeScore"]
            )
        )

        away_score = int(
            float(
                extracted["awayScore"]
            )
        )

        quarter = int(
            float(
                extracted["quarter"]
            )
        )

        line = float(line)

    except (TypeError, ValueError):
        return no_bet(
            home=home,
            away=away,
            score="-",
            clock="-",
            reason=(
                "Punteggio, quarto o linea "
                "non sono numeri validi."
            ),
            why=[
                "Dati numerici non validi",
                "Puntata consigliata 0 €"
            ],
            extracted=extracted
        )

    time_left = clock_min(
        extracted.get(
            "timeRemaining"
        )
    )

    if (
        time_left is None
        or quarter < 1
        or quarter > 4
        or time_left < 0
        or time_left > 10
    ):
        return no_bet(
            home=home,
            away=away,
            score=(
                f"{home_score}-"
                f"{away_score}"
            ),
            clock=(
                f"{extracted.get('timeRemaining')} "
                f"Q{quarter}"
            ),
            reason=(
                "Cronometro o quarto "
                "non affidabili."
            ),
            why=[
                "Controlla quarto e tempo",
                "Puntata consigliata 0 €"
            ],
            line=line,
            extracted=extracted
        )

    missing_quarters = (
        missing_quarter_numbers(
            extracted,
            quarter,
            time_left
        )
    )

    total = (
        home_score
        + away_score
    )

    played = (
        (quarter - 1) * 10
        + (10 - time_left)
    )

    remaining = max(
        0,
        40 - played
    )

    if played <= 0:
        played = 0.1

    ppm = total / played

    q1 = qtot(
        extracted,
        "q1Home",
        "q1Away"
    )

    q2 = qtot(
        extracted,
        "q2Home",
        "q2Away"
    )

    q3 = qtot(
        extracted,
        "q3Home",
        "q3Away"
    )

    q4 = qtot(
        extracted,
        "q4Home",
        "q4Away"
    )

    (
        trend_factor,
        trend_description
    ) = trend(
        q1,
        q2,
        q3,
        q4
    )

    phase = {
        1: 0.97,
        2: 0.95,
        3: 0.93,
        4: 0.88
    }.get(
        quarter,
        0.90
    )

    fatigue = (
        0.96
        if quarter >= 3
        else 1
    )

    blowout = (
        0.91
        if (
            abs(
                home_score
                - away_score
            ) >= 22
            and quarter >= 3
        )
        else 1
    )

    close_game = (
        0.03
        if (
            abs(
                home_score
                - away_score
            ) <= 8
            and quarter >= 4
        )
        else 0
    )

    corrected_ppm = max(
        1.70,
        min(
            4.85,
            ppm
            * phase
            * fatigue
            * blowout
            * (1 + close_game)
            * trend_factor
        )
    )

    raw_prediction = (
        total
        + remaining
        * corrected_ppm
    )

    shrink = (
        0.82
        if played < 20
        else 0.88
    )

    predicted_total = round(
        line
        + (
            raw_prediction
            - line
        )
        * shrink
    )

    value = (
        predicted_total
        - line
    )

    probability_over = prob(
        value
    )

    probability_under = (
        100 - probability_over
    )

    over_wait_line = round(
        predicted_total
        - MARGINE_SICUREZZA,
        1
    )

    under_wait_line = round(
        predicted_total
        + MARGINE_SICUREZZA,
        1
    )

    if (
        value
        >= MARGINE_SICUREZZA
    ):
        side = "OVER"

    elif (
        value
        <= -MARGINE_SICUREZZA
    ):
        side = "UNDER"

    else:
        side = None

    confidence = 22

    confidence += (
        18
        if (
            odds_over is not None
            and odds_under is not None
        )
        else 8
    )

    confidence += (
        20
        if played >= 8
        else 8
    )

    if abs(value) >= 12:
        confidence += 25

    elif abs(value) >= 9:
        confidence += 18

    elif abs(value) >= 7:
        confidence += 10

    if (
        home != "Squadra A"
        and away != "Squadra B"
    ):
        confidence += 8

    available_quarters = len([
        value
        for value in [
            q1,
            q2,
            q3,
            q4
        ]
        if value is not None
    ])

    confidence += min(
        12,
        available_quarters * 4
    )

    if missing_quarters:
        confidence -= min(
            16,
            len(missing_quarters) * 6
        )

    if played < 5:
        confidence -= 10

    confidence = max(
        0,
        min(
            90,
            round(confidence)
        )
    )

    suggested_stake = (
        stake(
            bankroll,
            confidence,
            value
        )
        if side
        else 0
    )

    home_share = (
        home_score / total
        if total
        else 0.5
    )

    predicted_home = round(
        predicted_total
        * home_share
    )

    predicted_away = round(
        predicted_total
        - predicted_home
    )

    if (
        side
        and confidence >= 68
        and suggested_stake > 0
    ):
        signal = "BET"
        action = f"GIOCA {side}"
        decision_text = (
            f"Scommetti {side} {line}"
        )

        reason = (
            f"Margine sufficiente. "
            f"Totale previsto "
            f"{predicted_total}, "
            f"linea {line}, "
            f"margine {value:+.1f}."
        )

    elif abs(value) >= 4:
        signal = "OBSERVE"
        action = "ASPETTA"
        decision_text = (
            "Aspetta una linea "
            "più conveniente"
        )

        reason = (
            f"Margine non abbastanza "
            f"sicuro. Totale previsto "
            f"{predicted_total}, "
            f"linea {line}, "
            f"margine {value:+.1f}."
        )

    else:
        signal = "NO_BET"
        action = "NO BET"
        decision_text = (
            "Non scommettere"
        )

        reason = (
            f"Linea troppo vicina "
            f"alla previsione. "
            f"Totale previsto "
            f"{predicted_total}, "
            f"linea {line}, "
            f"margine {value:+.1f}."
        )

    why = [
        (
            f"Totale previsto: "
            f"{predicted_total}"
        ),
        (
            f"Linea bookmaker scelta: "
            f"{line}"
        ),
        (
            f"Margine: "
            f"{value:+.1f}"
        ),
        (
            f"Ritmo attuale: "
            f"{ppm:.2f} punti/min"
        ),
        (
            f"Ritmo corretto: "
            f"{corrected_ppm:.2f} "
            f"punti/min"
        ),
        (
            f"Trend ritmo: "
            f"{trend_description}"
        ),
        (
            f"Q1: "
            f"{q1 if q1 is not None else '-'} "
            f"punti"
        ),
        (
            f"Q2: "
            f"{q2 if q2 is not None else '-'} "
            f"punti"
        ),
        (
            f"Q3: "
            f"{q3 if q3 is not None else '-'} "
            f"punti"
        ),
        (
            f"Q4: "
            f"{q4 if q4 is not None else '-'} "
            f"punti"
        ),
        (
            f"OVER giocabile a "
            f"{over_wait_line} o meno"
        ),
        (
            f"UNDER giocabile da "
            f"{under_wait_line} o più"
        )
    ]

    if missing_quarters:
        missing_text = ", ".join(
            f"Q{number}"
            for number
            in missing_quarters
        )

        why.insert(
            0,
            (
                "Inserisci manualmente "
                f"i parziali mancanti: "
                f"{missing_text}"
            )
        )

    return {
        "signal": signal,
        "action": action,
        "decision_text": (
            decision_text
        ),
        "side": side,
        "line": line,
        "stake": suggested_stake,
        "confidence": confidence,
        "score": (
            f"{home_score}-"
            f"{away_score}"
        ),
        "clock": (
            f"{extracted.get('timeRemaining')} "
            f"Q{quarter}"
        ),
        "teams": {
            "home": home,
            "away": away
        },
        "rhythm": ritmo(ppm),
        "ppm": round(
            ppm,
            2
        ),
        "corrected_ppm": round(
            corrected_ppm,
            2
        ),
        "played": round(
            played,
            1
        ),
        "remaining": round(
            remaining,
            1
        ),
        "total_predicted": (
            predicted_total
        ),
        "final_score": (
            f"{predicted_home}-"
            f"{predicted_away}"
        ),
        "value": round(
            value,
            1
        ),
        "prob_over": (
            probability_over
        ),
        "prob_under": (
            probability_under
        ),
        "over_wait_line": (
            over_wait_line
        ),
        "under_wait_line": (
            under_wait_line
        ),
        "q1_total": q1,
        "q2_total": q2,
        "q3_total": q3,
        "q4_total": q4,
        "trend_desc": (
            trend_description
        ),
        "trend_factor": round(
            trend_factor,
            3
        ),
        "all_lines": all_lines,
        "reason": reason,
        "why": why,
        "source": (
            "MultiBasket AI PRO 2.1"
        ),
        "extracted": extracted,
        "needs_quarters": bool(
            missing_quarters
        ),
        "missing_quarters": (
            missing_quarters
        )
    }
