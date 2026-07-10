const $ = (id) => document.getElementById(id);

let lastDecision = null;
let lastExtracted = null;
let liveGameId = null;


/* =========================
   FUNZIONI GENERICHE
========================= */

function formatValue(value) {
  if (
    value === null ||
    value === undefined ||
    value === ""
  ) {
    return "-";
  }

  return String(value);
}


function escapeHtml(value) {
  return String(value).replace(
    /[&<>"']/g,
    (character) => {
      const replacements = {
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      };

      return replacements[character];
    }
  );
}


function getResultClass(signal) {
  if (signal === "BET") {
    return "result result-green";
  }

  if (signal === "OBSERVE") {
    return "result result-amber";
  }

  if (signal === "NO_BET") {
    return "result result-red";
  }

  return "result result-blue";
}


function setProgressBar(id, value) {
  const element = $(id);
  const number = Number(value);

  if (!element) {
    return;
  }

  if (
    !Number.isFinite(number) ||
    number <= 0
  ) {
    element.style.width = "0%";
    return;
  }

  const percentage = Math.min(
    100,
    Math.round((number / 45) * 100)
  );

  element.style.width =
    `${percentage}%`;
}


async function postJson(url, body) {
  const response = await fetch(url, {
    method: "POST",

    headers: {
      "Content-Type": "application/json",
    },

    body: JSON.stringify(body),
  });

  let data;

  try {
    data = await response.json();
  } catch (error) {
    data = {
      error: "Il server ha restituito una risposta non valida.",
    };
  }

  if (!response.ok) {
    throw new Error(
      data.error ||
      data.message ||
      `Errore server ${response.status}`
    );
  }

  return data;
}


/* =========================
   DATA E ORA
========================= */

function getItalianDate() {
  return new Date().toLocaleDateString(
    "it-IT",
    {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    }
  );
}


function getItalianTime() {
  return new Date().toLocaleTimeString(
    "it-IT",
    {
      hour: "2-digit",
      minute: "2-digit",
    }
  );
}


/* =========================
   DEBUG
========================= */

function updateDebug(extra = null) {
  const debug = $("debug");

  if (!debug) {
    return;
  }

  debug.textContent = JSON.stringify(
    {
      extracted: lastExtracted,
      decision: lastDecision,
      liveGameId,
      extra,
    },
    null,
    2
  );
}


/* =========================
   RENDER DEL PRONOSTICO
========================= */

function renderDecision(decision) {
  lastDecision = decision;

  $("result").className =
    getResultClass(decision.signal);

  $("mainAction").textContent =
    decision.action || "NO BET";

  $("decisionText").textContent =
    decision.decision_text ||
    "Non scommettere";

  $("reason").textContent =
    decision.reason || "-";

  $("stake").textContent =
    `${decision.stake ?? 0} €`;

  $("probOver").textContent =
    `${decision.prob_over ?? 50}%`;

  $("probUnder").textContent =
    `${decision.prob_under ?? 50}%`;

  $("homeTeam").textContent =
    decision.teams?.home ||
    "Squadra A";

  $("awayTeam").textContent =
    decision.teams?.away ||
    "Squadra B";

  $("score").textContent =
    decision.score || "-";

  const clock =
    decision.clock || "-";

  if (clock.includes("Q")) {
    const parts = clock.split("Q");

    $("quarterBadge").textContent =
      `Q${parts[1] || "-"}`;
  } else {
    $("quarterBadge").textContent =
      "Q-";
  }

  $("clockBadge").textContent =
    clock.split(" ")[0] || "-";

  $("phaseBadge").textContent =
    clock.startsWith("0:")
      ? "Fine quarto / intervallo"
      : "Live";

  $("pred").textContent =
    formatValue(
      decision.total_predicted
    );

  $("bookLine").textContent =
    formatValue(decision.line);

  const value =
    Number(decision.value || 0);

  $("margin").textContent =
    `${value > 0 ? "+" : ""}` +
    `${formatValue(decision.value)}`;

  $("margin").className =
    `metric-value ${
      value > 0
        ? "success-text"
        : value < 0
        ? "danger-text"
        : ""
    }`;

  $("marginSub").textContent =
    value >= 0
      ? "sopra la linea"
      : "sotto la linea";

  $("conf").textContent =
    formatValue(
      decision.confidence
    );

  $("ppm").textContent =
    formatValue(decision.ppm);

  $("played").textContent =
    formatValue(decision.played);

  $("remaining").textContent =
    formatValue(
      decision.remaining
    );

  $("finalScore").textContent =
    formatValue(
      decision.final_score
    );

  $("finalTotal").textContent =
    `Totale ${formatValue(
      decision.total_predicted
    )}`;

  $("overWait").textContent =
    `≤ ${formatValue(
      decision.over_wait_line
    )}`;

  $("underWait").textContent =
    `≥ ${formatValue(
      decision.under_wait_line
    )}`;

  $("q1Text").textContent =
    `Q1 ${formatValue(
      decision.q1_total
    )}`;

  $("q2Text").textContent =
    `Q2 ${formatValue(
      decision.q2_total
    )}`;

  $("q3Text").textContent =
    `Q3 ${formatValue(
      decision.q3_total
    )}`;

  $("q4Text").textContent =
    `Q4 ${formatValue(
      decision.q4_total
    )}`;

  setProgressBar(
    "q1Bar",
    decision.q1_total
  );

  setProgressBar(
    "q2Bar",
    decision.q2_total
  );

  setProgressBar(
    "q3Bar",
    decision.q3_total
  );

  setProgressBar(
    "q4Bar",
    decision.q4_total
  );

  $("trendDesc").textContent =
    decision.trend_desc || "-";

  $("why").innerHTML =
    (decision.why || [])
      .map(
        (item) =>
          `<div>• ${escapeHtml(
            item
          )}</div>`
      )
      .join("");

  $("betSide").value =
    decision.side || "";

  $("betLine").value =
    decision.line || "";

  $("betStake").value =
    decision.stake || 0;

  updateDebug();
}


/* =========================
   CARICAMENTO SCREENSHOT
========================= */

$("shot").addEventListener(
  "change",
  () => {
    const file =
      $("shot").files[0];

    if (!file) {
      return;
    }

    $("preview").src =
      URL.createObjectURL(file);

    $("preview").style.display =
      "block";

    $("placeholder").style.display =
      "none";

    $("newShot").style.display =
      "block";

    $("uploadTitle").textContent =
      "✅ Screenshot caricato";

    $("shotStatus").textContent =
      "Pronto per l'analisi.";
  }
);


$("newShot").addEventListener(
  "click",
  () => {
    $("shot").value = "";

    $("preview").style.display =
      "none";

    $("placeholder").style.display =
      "grid";

    $("newShot").style.display =
      "none";

    $("uploadTitle").textContent =
      "📸 Screenshot live";

    $("shotStatus").textContent =
      "Carica un nuovo screenshot.";

    liveGameId = null;

    $("liveStatus").textContent =
      "Prima analizza uno screenshot, poi aggancia la partita live.";

    $("candidates").innerHTML = "";

    updateDebug();
  }
);


/* =========================
   ANALISI SCREENSHOT
========================= */

$("analyzeShot").addEventListener(
  "click",
  async () => {
    const file =
      $("shot").files[0];

    if (!file) {
      $("shotStatus").textContent =
        "Carica prima uno screenshot.";

      return;
    }

    const formData =
      new FormData();

    formData.append(
      "image",
      file
    );

    formData.append(
      "bankroll",
      parseFloat(
        $("bankroll").value
      ) || 25
    );

    $("shotStatus").textContent =
      "AI in lettura...";

    liveGameId = null;

    try {
      const response = await fetch(
        "/api/screenshot/analyze",
        {
          method: "POST",
          body: formData,
        }
      );

      const data =
        await response.json();

      if (!response.ok) {
        throw new Error(
          data.error ||
          "Errore durante l'analisi."
        );
      }

      lastExtracted =
        data.extracted || {};

      $("shotStatus").textContent =
        "Analisi completata.";

      renderDecision(
        data.decision
      );

      $("liveStatus").textContent =
        "Ora puoi premere TROVA PARTITA LIVE.";
    } catch (error) {
      $("shotStatus").textContent =
        `Errore analisi: ${error.message}`;

      updateDebug({
        screenshotError:
          error.message,
      });
    }
  }
);


/* =========================
   TROVA PARTITA LIVE
========================= */

$("findLive").addEventListener(
  "click",
  async () => {
    if (!lastExtracted) {
      $("liveStatus").textContent =
        "Prima analizza uno screenshot.";

      return;
    }

    $("liveStatus").textContent =
      "Ricerca partita live in corso...";

    $("candidates").innerHTML = "";

    try {
      const data = await postJson(
        "/api/live/find",
        {
          homeTeam:
            lastExtracted.homeTeam,

          awayTeam:
            lastExtracted.awayTeam,

          homeScore:
            lastExtracted.homeScore,

          awayScore:
            lastExtracted.awayScore,
        }
      );

      liveGameId =
        data.game_id;

      $("liveStatus").textContent =
        `✅ Partita agganciata: ` +
        `${data.home} - ${data.away}` +
        ` · Punteggio API: ${data.score}` +
        ` · Affinità: ${data.match_score}`;

      showCandidates(
        data.candidates || []
      );

      updateDebug(data);

    } catch (error) {
      $("liveStatus").textContent =
        `❌ ${error.message}. ` +
        `Premi MOSTRA PARTITE LIVE API ` +
        `per verificare se la competizione è coperta.`;

      updateDebug({
        findLiveError:
          error.message,
      });
    }
  }
);


/* =========================
   LISTA PARTITE LIVE API
========================= */

$("listLive").addEventListener(
  "click",
  async () => {
    $("liveStatus").textContent =
      "Caricamento partite live API...";

    $("candidates").innerHTML = "";

    try {
      const response = await fetch(
        "/api/live/list"
      );

      const data =
        await response.json();

      if (
        !response.ok ||
        !data.ok
      ) {
        throw new Error(
          data.error ||
          "Errore nel caricamento delle partite."
        );
      }

      $("liveStatus").textContent =
        `Partite live trovate dall'API: ${data.count}`;

      if (
        !data.games ||
        data.games.length === 0
      ) {
        $("candidates").innerHTML =
          `<div class="candidate">
            Nessuna partita live restituita da API-Sports.
            La competizione potrebbe non essere coperta.
          </div>`;

        updateDebug(data);
        return;
      }

      $("candidates").innerHTML =
        data.games
          .map(
            (game) => `
              <div class="candidate">
                <strong>
                  ${escapeHtml(game.home)} -
                  ${escapeHtml(game.away)}
                </strong>
                <br>

                Punteggio:
                ${escapeHtml(game.score)}
                <br>

                Stato:
                ${escapeHtml(game.status)}
                <br>

                Campionato:
                ${escapeHtml(
                  game.league || "-"
                )}
                <br>

                Paese:
                ${escapeHtml(
                  game.country || "-"
                )}
                <br>

                Game ID:
                ${escapeHtml(game.id)}
              </div>
            `
          )
          .join("");

      updateDebug(data);

    } catch (error) {
      $("liveStatus").textContent =
        `Errore lista live: ${error.message}`;

      updateDebug({
        liveListError:
          error.message,
      });
    }
  }
);


/* =========================
   CANDIDATI PARTITA
========================= */

function showCandidates(
  candidates
) {
  if (!candidates.length) {
    return;
  }

  $("candidates").innerHTML =
    `<div class="small">
      Migliori corrispondenze trovate:
    </div>` +

    candidates
      .map(
        (candidate) => `
          <div class="candidate">
            <strong>
              ${escapeHtml(
                candidate.home
              )} -
              ${escapeHtml(
                candidate.away
              )}
            </strong>
            <br>

            Punteggio API:
            ${escapeHtml(
              candidate.score
            )}
            <br>

            Affinità totale:
            ${escapeHtml(
              candidate.score_value
            )}
            <br>

            Affinità nomi:
            ${escapeHtml(
              candidate.name_score
            )}
            <br>

            Bonus punteggio:
            ${escapeHtml(
              candidate.score_bonus
            )}
            <br>

            Campionato:
            ${escapeHtml(
              candidate.league || "-"
            )}
            <br>

            Stato:
            ${escapeHtml(
              candidate.status || "-"
            )}
            <br>

            ID:
            ${escapeHtml(
              candidate.id
            )}
          </div>
        `
      )
      .join("");
}


/* =========================
   TELEGRAM: CONTROLLO LIVE
========================= */

$("checkNow").addEventListener(
  "click",
  async () => {
    if (!liveGameId) {
      $("liveStatus").textContent =
        "Prima premi TROVA PARTITA LIVE.";

      return;
    }

    const side =
      $("betSide")
        .value
        .trim()
        .toUpperCase();

    const line =
      parseFloat(
        $("betLine").value
      );

    const stake =
      parseFloat(
        $("betStake").value
      );

    if (
      !side ||
      !["OVER", "UNDER"].includes(side)
    ) {
      $("liveStatus").textContent =
        "Inserisci correttamente OVER oppure UNDER.";

      return;
    }

    if (!Number.isFinite(line)) {
      $("liveStatus").textContent =
        "Inserisci la linea giocata.";

      return;
    }

    $("liveStatus").textContent =
      "Invio aggiornamento Telegram...";

    try {
      const data = await postJson(
        "/api/live/check-now",
        {
          game_id:
            liveGameId,

          homeTeam:
            lastDecision?.teams?.home ||
            lastExtracted?.homeTeam ||
            "Squadra A",

          awayTeam:
            lastDecision?.teams?.away ||
            lastExtracted?.awayTeam ||
            "Squadra B",

          side,
          line,

          stake:
            Number.isFinite(stake)
              ? stake
              : 0,

          expectedScore:
            lastDecision?.final_score ||
            "-",

          expectedTotal:
            lastDecision?.total_predicted ||
            "-",

          confidence:
            lastDecision?.confidence ??
            0,
        }
      );

      $("liveStatus").textContent =
        `📲 ${data.message}` +
        ` · Punteggio: ${data.score}` +
        ` · Totale: ${data.total}` +
        ` · ${data.bet_state}`;

      updateDebug(data);

    } catch (error) {
      $("liveStatus").textContent =
        `Errore Telegram/live: ${error.message}`;

      updateDebug({
        telegramLiveError:
          error.message,
      });
    }
  }
);


/* =========================
   CONTROLLO SERVER
========================= */

$("health").addEventListener(
  "click",
  async () => {
    try {
      const response = await fetch(
        "/api/health"
      );

      const data =
        await response.json();

      $("status").textContent =
        `Telegram: ${
          data.telegram
            ? "SI"
            : "NO"
        }` +

        ` · OpenAI: ${
          data.openai
            ? "SI"
            : "NO"
        }` +

        ` · Basket API: ${
          data.basketball_api
            ? "SI"
            : "NO"
        }` +

        ` · ${data.mode || ""}`;

      updateDebug(data);

    } catch (error) {
      $("status").textContent =
        `Errore server: ${error.message}`;
    }
  }
);


/* =========================
   TEST TELEGRAM
========================= */

$("telegram").addEventListener(
  "click",
  async () => {
    try {
      const data = await postJson(
        "/api/telegram/test",
        {}
      );

      $("status").textContent =
        data.message ||
        "Telegram funzionante.";

      updateDebug(data);

    } catch (error) {
      $("status").textContent =
        `Telegram non configurato: ${error.message}`;

      updateDebug({
        telegramTestError:
          error.message,
      });
    }
  }
);


/* =========================
   MOSTRA JSON
========================= */

$("toggleDebug").addEventListener(
  "click",
  () => {
    const debug = $("debug");

    if (
      debug.style.display === "block"
    ) {
      debug.style.display = "none";
    } else {
      debug.style.display = "block";
    }
  }
);


/* =========================
   REGISTRA GIOCATA
========================= */

$("registerBet").addEventListener(
  "click",
  async () => {
    const side =
      $("betSide")
        .value
        .trim()
        .toUpperCase();

    const line =
      parseFloat(
        $("betLine").value
      );

    const stake =
      parseFloat(
        $("betStake").value
      );

    const bankroll =
      parseFloat(
        $("bankroll").value
      ) || 25;

    if (
      !side ||
      !["OVER", "UNDER"].includes(side)
    ) {
      $("qualityText").textContent =
        "Inserisci correttamente OVER oppure UNDER.";

      return;
    }

    if (!Number.isFinite(line)) {
      $("qualityText").textContent =
        "Inserisci la linea giocata.";

      return;
    }

    if (
      !Number.isFinite(stake) ||
      stake <= 0
    ) {
      $("qualityText").textContent =
        "Inserisci una puntata valida.";

      return;
    }

    if (!lastDecision) {
      $("qualityText").textContent =
        "Prima analizza uno screenshot.";

      return;
    }

    $("qualityText").textContent =
      "Registrazione e invio Telegram...";

    try {
      const data = await postJson(
        "/api/bet/register",
        {
          source:
            lastDecision.source ||
            "MultiBasket AI PRO 2.0",

          homeTeam:
            lastDecision.teams?.home ||
            lastExtracted?.homeTeam ||
            "Squadra A",

          awayTeam:
            lastDecision.teams?.away ||
            lastExtracted?.awayTeam ||
            "Squadra B",

          currentScore:
            lastDecision.score ||
            "-",

          quarterClock:
            lastDecision.clock ||
            "-",

          side,
          line,
          stake,
          bankroll,

          expectedScore:
            lastDecision.final_score ||
            "-",

          expectedTotal:
            lastDecision.total_predicted ||
            "-",

          confidence:
            lastDecision.confidence ??
            0,

          overWaitLine:
            lastDecision.over_wait_line ??
            "-",

          underWaitLine:
            lastDecision.under_wait_line ??
            "-",

          date:
            getItalianDate(),

          time:
            getItalianTime(),
        }
      );

      $("qualityText").textContent =
        data.message ||
        "Giocata registrata.";

      updateDebug(data);

    } catch (error) {
      $("qualityText").textContent =
        `Errore registrazione: ${error.message}`;

      updateDebug({
        registerBetError:
          error.message,
      });
    }
  }
);


/* =========================
   STATO INIZIALE
========================= */

updateDebug();
