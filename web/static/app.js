// nda-review-cli demo — vanilla JS, no framework

(function () {
  "use strict";

  // ---------- helpers ----------

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  function setBusy(button, busy) {
    button.disabled = busy;
    button.dataset.label = button.dataset.label || button.textContent;
    button.textContent = busy ? "Running..." : button.dataset.label;
  }

  function showError(card, message) {
    let el = $(".error", card);
    if (!el) {
      el = document.createElement("div");
      el.className = "error";
      card.appendChild(el);
    }
    el.textContent = message;
  }

  function clearError(card) {
    const el = $(".error", card);
    if (el) el.remove();
  }

  async function postJSON(path, body) {
    const resp = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    let data;
    try { data = await resp.json(); }
    catch (e) { throw new Error("Server returned non-JSON response (" + resp.status + ")"); }
    if (!resp.ok || data.ok === false) {
      throw new Error(data && data.error ? data.error : "Request failed (" + resp.status + ")");
    }
    return data;
  }

  // ---------- card 1: draft ----------

  const draftCard = $("#card-draft");
  const draftForm = $("#form-draft", draftCard);
  const draftResult = $(".result", draftCard);
  const draftOutput = $(".output", draftCard);
  const draftDownload = $(".download", draftCard);
  const partyAB = $(".party-fields:not(.oneway-fields)", draftCard);
  const partyOneway = $(".oneway-fields", draftCard);
  const cpOnlyField = $(".cp-only", draftCard);
  const templateSelect = draftForm.elements.template;

  function syncTemplateUI() {
    const v = templateSelect.value;
    partyOneway.hidden = (v !== "one-way-out");
    partyAB.hidden = (v === "one-way-out");
    cpOnlyField.hidden = (v !== "common-paper-mutual");
  }
  templateSelect.addEventListener("change", syncTemplateUI);
  syncTemplateUI();

  draftForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    clearError(draftCard);
    const button = draftForm.querySelector("button");
    setBusy(button, true);

    const payload = {
      template: templateSelect.value,
      purpose: draftForm.elements.purpose.value,
    };
    if (templateSelect.value === "one-way-out") {
      payload.disclosing_party = draftForm.elements.disclosing_party.value;
      payload.disclosing_party_address = draftForm.elements.disclosing_party_address.value;
      payload.receiving_party = draftForm.elements.receiving_party.value;
      payload.receiving_party_address = draftForm.elements.receiving_party_address.value;
    } else {
      payload.party_a = draftForm.elements.party_a.value;
      payload.party_a_address = draftForm.elements.party_a_address.value;
      payload.party_b = draftForm.elements.party_b.value;
      payload.party_b_address = draftForm.elements.party_b_address.value;
    }
    if (templateSelect.value === "common-paper-mutual") {
      payload.governing_law = draftForm.elements.governing_law.value;
    }

    try {
      const data = await postJSON("/api/draft", payload);
      draftOutput.textContent = data.markdown;
      draftDownload.href = data.download_docx;
      draftDownload.download = "draft.docx";
      draftResult.hidden = false;
      draftResult.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      showError(draftCard, err.message);
    } finally {
      setBusy(button, false);
    }
  });

  // ---------- card 2: review ----------

  const reviewCard = $("#card-review");
  const reviewForm = $("#form-review", reviewCard);
  const reviewResult = $(".result", reviewCard);
  const reviewSummary = $(".summary", reviewCard);
  const reviewFindings = $(".findings", reviewCard);

  reviewForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    clearError(reviewCard);
    const button = reviewForm.querySelector("button");
    setBusy(button, true);

    try {
      const data = await postJSON("/api/review", {
        why: reviewForm.elements.why.checked,
      });
      const decisionClass = (data.decision || "").toLowerCase();
      reviewSummary.innerHTML =
        '<span class="decision ' + decisionClass + '">Decision: ' + (data.decision || "?") + '</span>' +
        '<span class="risk-score">Risk score: ' + (data.risk_score != null ? data.risk_score : "?") + '</span>';
      reviewFindings.innerHTML = "";
      (data.findings || []).forEach(function (f) {
        const li = document.createElement("li");
        const severity = f.severity || "low";
        const concern = f.concern || "";
        const snippet = f.snippet || "";
        const ruleHits = (f.rule_hits || []).join(", ");

        const head = document.createElement("div");
        const name = document.createElement("span");
        name.className = "clause-name";
        name.textContent = f.clause || "(unknown)";
        head.appendChild(name);
        const sev = document.createElement("span");
        sev.className = "severity " + severity;
        sev.textContent = severity;
        head.appendChild(sev);
        li.appendChild(head);

        if (concern) {
          const p = document.createElement("p");
          p.className = "concern";
          p.textContent = concern;
          li.appendChild(p);
        }
        if (snippet) {
          const code = document.createElement("code");
          code.className = "snippet";
          code.textContent = snippet;
          li.appendChild(code);
        }
        if (ruleHits) {
          const r = document.createElement("p");
          r.className = "concern";
          r.textContent = "Why flagged: " + ruleHits;
          li.appendChild(r);
        }
        reviewFindings.appendChild(li);
      });
      if (!data.findings || !data.findings.length) {
        const li = document.createElement("li");
        li.textContent = "No findings — the sample NDA passed all clause checks against the demo policy.";
        reviewFindings.appendChild(li);
      }
      reviewResult.hidden = false;
      reviewResult.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      showError(reviewCard, err.message);
    } finally {
      setBusy(button, false);
    }
  });

  // ---------- card 3: simulate ----------

  const simCard = $("#card-simulate");
  const simForm = $("#form-simulate", simCard);
  const simResult = $(".result", simCard);
  const simSummary = $(".summary", simCard);
  const simTbody = $(".trajectory tbody", simCard);
  const simWinners = $(".winners", simCard);
  const simBlock = $(".block-diagnosis", simCard);

  simForm.addEventListener("submit", async function (e) {
    e.preventDefault();
    clearError(simCard);
    const button = simForm.querySelector("button");
    setBusy(button, true);

    try {
      const data = await postJSON("/api/simulate", {
        stance_a: simForm.elements.stance_a.value,
        stance_b: simForm.elements.stance_b.value,
        diverge_b: simForm.elements.diverge_b.checked,
      });

      const stances = data.stances || {};
      simSummary.innerHTML =
        "<strong>" + (stances.a || "?") + " × " + (stances.b || "?") + "</strong>" +
        " → outcome: <code>" + (data.outcome || "?") + "</code>" +
        " in " + (data.rounds_used || "?") + " round(s)";

      simTbody.innerHTML = "";
      (data.trajectory || []).forEach(function (t) {
        const tr = document.createElement("tr");
        ["round", "proposer", "amendment_source", "stance", "agreed", "disputed", "proposed"].forEach(function (k) {
          const td = document.createElement("td");
          let v = t[k];
          if (k === "proposer" && v) v = String(v).toUpperCase();
          if (v == null) v = "—";
          td.textContent = v;
          tr.appendChild(td);
        });
        simTbody.appendChild(tr);
      });

      const winners = data.winner_per_clause || {};
      const winnerKeys = Object.keys(winners);
      if (winnerKeys.length) {
        let html = "<h4>Winner per agreed clause</h4>";
        winnerKeys.sort().forEach(function (clause) {
          const w = winners[clause];
          html += '<span class="winner-line">' + clause + " → Party " + (w ? String(w).toUpperCase() : "?") + "</span>";
        });
        simWinners.innerHTML = html;
        simWinners.hidden = false;
      } else {
        simWinners.innerHTML = "";
        simWinners.hidden = true;
      }

      const diag = data.block_diagnosis;
      if (diag) {
        simBlock.innerHTML =
          "<h4>Block diagnosis</h4>" +
          "<p>Rounds without progress: " + (diag.rounds_without_progress || "?") +
          " (threshold: " + (diag.threshold || "?") + ")</p>" +
          "<p>Stuck clauses: <code>" + (diag.stuck_clauses || []).join(", ") + "</code></p>" +
          "<p>" + (diag.note || "") + "</p>";
        simBlock.hidden = false;
      } else {
        simBlock.hidden = true;
      }

      simResult.hidden = false;
      simResult.scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (err) {
      showError(simCard, err.message);
    } finally {
      setBusy(button, false);
    }
  });
})();
