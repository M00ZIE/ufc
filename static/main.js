/**
 * Parlay builder — usa /api/bet/potential e /api/bet/multi.
 * Depende de window.__ufcAnalyzeData preenchido pelo index após análise do card.
 */
(function () {
  const $ = (id) => document.getElementById(id);
  const LS_KEY = "ufc_parlay_draft_v1";

  function esc(s) {
    if (s == null) return "";
    const d = document.createElement("div");
    d.textContent = s;
    return d.innerHTML;
  }

  function riskClass(tier) {
    if (tier === "SAFE") return "parlay-risk--safe";
    if (tier === "RISKY") return "parlay-risk--risky";
    if (tier === "SKIP") return "parlay-risk--skip";
    return "";
  }

  function tierFromFight(f) {
    const ap = f && f.advanced_prediction;
    const r = ap && ap.risk;
    return (r && r.tier) || (f && f.risk_tier) || "—";
  }

  function buildRows(fights) {
    return (fights || [])
      .map((f, idx) => {
        const i = idx + 1;
        if (f.error || !f.red || !f.blue) {
          return `<tr class="parlay-row parlay-row--disabled"><td colspan="6">${esc(f.error || "—")}</td></tr>`;
        }
        const tier = tierFromFight(f);
        const skip = tier === "SKIP";
        const nm = `${esc(f.red.name)} × ${esc(f.blue.name)}`;
        const rc = riskClass(tier);
        return `<tr class="parlay-row" data-fight-index="${i}">
          <td><input type="checkbox" class="parlay-inc" ${skip ? "disabled" : ""} aria-label="Incluir luta ${i}" /></td>
          <td><span class="parlay-fi">#${i}</span> ${nm}</td>
          <td>
            <select class="parlay-type" ${skip ? "disabled" : ""}>
              <option value="final_result">Vencedor</option>
              <option value="method">Método (KO/Dec/Sub)</option>
              <option value="round_winner">Round</option>
            </select>
          </td>
          <td>
            <select class="parlay-side" ${skip ? "disabled" : ""}>
              <option value="red">Vermelho</option>
              <option value="blue">Azul</option>
            </select>
          </td>
          <td class="parlay-opt-cell">
            <select class="parlay-opt parlay-opt--method" hidden>
              <option value="KO">KO / TKO</option>
              <option value="Decisão">Decisão</option>
              <option value="Sub">Finalização</option>
            </select>
            <select class="parlay-opt parlay-opt--round" hidden>
              <option value="1">Round 1</option>
              <option value="2">Round 2</option>
              <option value="3">Round 3</option>
              <option value="4">Round 4</option>
              <option value="5">Round 5</option>
            </select>
            <span class="parlay-opt-na">—</span>
          </td>
          <td><span class="parlay-risk-tag ${rc}">${esc(String(tier))}</span></td>
        </tr>`;
      })
      .join("");
  }

  async function postJson(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    return { res, data };
  }

  function collectLegs(root) {
    const legs = [];
    root.querySelectorAll(".parlay-row[data-fight-index]").forEach((row) => {
      const cb = row.querySelector(".parlay-inc");
      if (!cb || !cb.checked) return;
      const fi = parseInt(row.getAttribute("data-fight-index"), 10);
      const betType = (row.querySelector(".parlay-type") || {}).value || "final_result";
      const side = (row.querySelector(".parlay-side") || {}).value || "red";
      const leg = { fight_index: fi, bet_type: betType, side };
      if (betType === "method") {
        const opt = row.querySelector(".parlay-opt--method");
        leg.option = opt ? opt.value : "KO";
      } else if (betType === "round_winner") {
        const opt = row.querySelector(".parlay-opt--round");
        leg.option = opt ? parseInt(opt.value, 10) : 1;
      }
      legs.push(leg);
    });
    return legs;
  }

  function syncOptionVisibility(row) {
    const t = (row.querySelector(".parlay-type") || {}).value || "final_result";
    const m = row.querySelector(".parlay-opt--method");
    const r = row.querySelector(".parlay-opt--round");
    const na = row.querySelector(".parlay-opt-na");
    if (m) m.hidden = t !== "method";
    if (r) r.hidden = t !== "round_winner";
    if (na) na.hidden = t === "method" || t === "round_winner";
  }

  function draftKey(eventUrl) {
    return `${LS_KEY}:${eventUrl || ""}`;
  }

  function saveDraft(root, eventUrl) {
    try {
      const stakeEl = root.querySelector(".parlay-stake");
      const q = root.querySelector(".parlay-search");
      const onlySel = root.querySelector(".parlay-only-selected");
      const payload = {
        stake: parseInt(stakeEl && stakeEl.value, 10) || 0,
        legs: collectLegs(root),
        search: (q && q.value) || "",
        onlySelected: !!(onlySel && onlySel.checked),
      };
      localStorage.setItem(draftKey(eventUrl), JSON.stringify(payload));
    } catch (_) {}
  }

  function loadDraft(eventUrl) {
    try {
      const raw = localStorage.getItem(draftKey(eventUrl));
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (_) {
      return null;
    }
  }

  function applyDraft(root, eventUrl, draft) {
    if (!draft) return;
    try {
      const stakeEl = root.querySelector(".parlay-stake");
      if (stakeEl && draft.stake != null) stakeEl.value = String(draft.stake);
      const q = root.querySelector(".parlay-search");
      if (q && draft.search != null) q.value = String(draft.search);
      const onlySel = root.querySelector(".parlay-only-selected");
      if (onlySel) onlySel.checked = !!draft.onlySelected;
      const legs = Array.isArray(draft.legs) ? draft.legs : [];
      // marcar checkboxes e restaurar selects por fight_index
      const byIndex = new Map();
      legs.forEach((l) => {
        if (!l || !l.fight_index) return;
        byIndex.set(Number(l.fight_index), l);
      });
      root.querySelectorAll(".parlay-row[data-fight-index]").forEach((row) => {
        const fi = parseInt(row.getAttribute("data-fight-index"), 10);
        const leg = byIndex.get(fi);
        const cb = row.querySelector(".parlay-inc");
        if (cb) cb.checked = !!leg;
        if (!leg) return;
        const t = row.querySelector(".parlay-type");
        const s = row.querySelector(".parlay-side");
        if (t && leg.bet_type) t.value = leg.bet_type;
        if (s && leg.side) s.value = leg.side;
        syncOptionVisibility(row);
        if (leg.bet_type === "method") {
          const opt = row.querySelector(".parlay-opt--method");
          if (opt && leg.option) opt.value = String(leg.option);
        } else if (leg.bet_type === "round_winner") {
          const opt = row.querySelector(".parlay-opt--round");
          if (opt && leg.option != null) opt.value = String(leg.option);
        }
      });
    } catch (_) {}
  }

  function filterRows(root) {
    const q = (root.querySelector(".parlay-search") || {}).value || "";
    const term = String(q).trim().toLowerCase();
    const onlySel = !!(root.querySelector(".parlay-only-selected") || {}).checked;
    let shown = 0;
    root.querySelectorAll(".parlay-row[data-fight-index]").forEach((row) => {
      const txt = (row.textContent || "").toLowerCase();
      const cb = row.querySelector(".parlay-inc");
      const isSel = !!(cb && cb.checked);
      const match = !term || txt.includes(term);
      const ok = match && (!onlySel || isSel);
      row.hidden = !ok;
      if (ok) shown += 1;
    });
    const c = root.querySelector(".parlay-count");
    if (c) c.textContent = String(shown);
  }

  function selectAllByTier(root, tier) {
    root.querySelectorAll(".parlay-row[data-fight-index]").forEach((row) => {
      const tag = row.querySelector(".parlay-risk-tag");
      const cb = row.querySelector(".parlay-inc");
      if (!cb || cb.disabled) return;
      const t = (tag && tag.textContent) || "";
      if (String(t).trim().toUpperCase() === String(tier).toUpperCase()) cb.checked = true;
    });
  }

  function clearAll(root) {
    root.querySelectorAll(".parlay-inc").forEach((cb) => (cb.checked = false));
  }

  async function refreshPreview(root, eventUrl) {
    const msg = root.querySelector(".parlay-preview-msg");
    const stakeEl = root.querySelector(".parlay-stake");
    const legs = collectLegs(root);
    const stake = parseInt(stakeEl && stakeEl.value, 10) || 0;
    if (msg) msg.textContent = "";
    if (legs.length < 2) {
      if (msg) msg.textContent = "Selecione pelo menos 2 lutas para formar um parlay.";
      return;
    }
    try {
      const { res, data } = await postJson("/api/bet/potential", {
        event_url: eventUrl,
        legs,
        stake: stake >= 1 ? stake : 0,
      });
      if (!data.ok) {
        if (msg) msg.textContent = data.message || data.error || "Prévia indisponível.";
        return;
      }
      const comb = data.combined_odds;
      const ret = data.estimated_return;
      const profit = data.estimated_profit;
      const maxS = data.max_stake;
      if (msg) {
        msg.innerHTML = `Odds combinadas <strong>${esc(String(comb))}</strong> · ` +
          (stake >= 1
            ? `Retorno estimado <strong class="parlay-yl">${esc(String(ret))}</strong> créditos ` +
              `(lucro <strong>${esc(String(profit))}</strong>)`
            : `Informe o stake — máximo permitido: <strong>${esc(String(maxS))}</strong> créditos (20% do saldo)`) +
          `.`;
      }
      if (stakeEl && maxS != null && stakeEl.max !== String(maxS)) {
        stakeEl.max = maxS > 0 ? maxS : 1;
      }
    } catch (e) {
      if (msg) msg.textContent = e.message || "Erro ao calcular.";
    }
  }

  function bind(root, eventUrl) {
    root.querySelectorAll(".parlay-type").forEach((sel) => {
      sel.addEventListener("change", () => {
        const row = sel.closest(".parlay-row");
        if (row) syncOptionVisibility(row);
      });
    });
    root.querySelectorAll(".parlay-row").forEach((row) => syncOptionVisibility(row));

    const stakeEl = root.querySelector(".parlay-stake");
    const debounce = (fn, ms) => {
      let t;
      return (...args) => {
        clearTimeout(t);
        t = setTimeout(() => fn(...args), ms);
      };
    };
    const prev = debounce(() => refreshPreview(root, eventUrl), 250);
    root.addEventListener("change", (ev) => {
      if (ev.target.matches(".parlay-inc, .parlay-type, .parlay-side, .parlay-opt")) prev();
    });
    if (stakeEl) stakeEl.addEventListener("input", prev);
    root.addEventListener("change", () => saveDraft(root, eventUrl));
    if (stakeEl) stakeEl.addEventListener("input", () => saveDraft(root, eventUrl));

    const btnP = root.querySelector(".parlay-btn-preview");
    if (btnP) btnP.addEventListener("click", () => refreshPreview(root, eventUrl));

    const btnC = root.querySelector(".parlay-btn-confirm");
    if (btnC) {
      btnC.addEventListener("click", async () => {
        const msg = root.querySelector(".parlay-preview-msg");
        const legs = collectLegs(root);
        const stake = parseInt(stakeEl && stakeEl.value, 10) || 0;
        if (legs.length < 2) {
          if (msg) msg.textContent = "Selecione pelo menos 2 lutas.";
          return;
        }
        if (stake < 1) {
          if (msg) msg.textContent = "Informe o stake (créditos).";
          return;
        }
        if (msg) msg.textContent = "Confirmando…";
        const { data } = await postJson("/api/bet/multi", {
          event_url: eventUrl,
          legs,
          stake,
        });
        if (!data.ok) {
          if (msg) msg.textContent = data.message || data.error || "Falha.";
          return;
        }
        if (msg) {
          msg.textContent = `Parlay #${data.parlay_id} registrado. Retorno potencial: ${data.payout_if_win} créditos.`;
          msg.classList.add("parlay-preview-msg--ok");
        }
        if (window.refreshAuthUI) await window.refreshAuthUI();
        if (stakeEl) stakeEl.value = "";
        void refreshPreview(root, eventUrl);
      });
    }

    const q = root.querySelector(".parlay-search");
    if (q) q.addEventListener("input", debounce(() => filterRows(root), 120));
    const onlySel = root.querySelector(".parlay-only-selected");
    if (onlySel) onlySel.addEventListener("change", () => filterRows(root));
    root.querySelector(".parlay-btn-safe")?.addEventListener("click", () => {
      selectAllByTier(root, "SAFE");
      filterRows(root);
      void refreshPreview(root, eventUrl);
    });
    root.querySelector(".parlay-btn-clear")?.addEventListener("click", () => {
      clearAll(root);
      filterRows(root);
      void refreshPreview(root, eventUrl);
    });
    root.querySelector(".parlay-btn-save")?.addEventListener("click", () => {
      saveDraft(root, eventUrl);
      const msg = root.querySelector(".parlay-preview-msg");
      if (msg) msg.textContent = "Rascunho salvo neste dispositivo.";
    });
  }

  function onCardLoaded(data) {
    const root = $("parlayRoot");
    if (!root || !data || !data.fights) return;
    const url = data.event_url || "";
    if (!url) {
      root.hidden = true;
      return;
    }
    root.hidden = false;
    root.innerHTML = `
      <section class="parlay-panel" aria-label="Aposta múltipla">
        <div class="parlay-head">
          <h2 class="parlay-title">Parlay (múltipla)</h2>
          <p class="parlay-sub">Marque lutas, escolha mercado e confirme. Mercados <span class="parlay-risk-tag parlay-risk--skip">SKIP</span> não entram no parlay.</p>
        </div>
        <div class="parlay-tools">
          <label class="parlay-search-lbl">Buscar
            <input type="search" class="parlay-search" placeholder="Nome do lutador, divisão…" />
          </label>
          <label class="parlay-only">
            <input type="checkbox" class="parlay-only-selected" />
            <span>somente selecionadas</span>
          </label>
          <div class="parlay-tools-btns">
            <button type="button" class="btn-parlay btn-parlay--ghost parlay-btn-safe">Selecionar SAFE</button>
            <button type="button" class="btn-parlay btn-parlay--ghost parlay-btn-clear">Limpar</button>
            <button type="button" class="btn-parlay btn-parlay--ghost parlay-btn-save">Salvar rascunho</button>
          </div>
          <div class="parlay-tools-meta">Mostrando <strong class="parlay-count">0</strong> lutas</div>
        </div>
        <table class="parlay-table">
          <thead>
            <tr>
              <th></th>
              <th>Luta</th>
              <th>Mercado</th>
              <th>Lado</th>
              <th>Opção</th>
              <th>Risco</th>
            </tr>
          </thead>
          <tbody>${buildRows(data.fights)}</tbody>
        </table>
        <div class="parlay-actions">
          <label class="parlay-stake-lbl">Stake (créditos)
            <input type="number" class="parlay-stake" min="1" step="1" value="10" />
          </label>
          <button type="button" class="btn-parlay btn-parlay--ghost parlay-btn-preview">Calcular retorno</button>
          <button type="button" class="btn-parlay parlay-btn-confirm">Confirmar parlay</button>
        </div>
        <p class="parlay-preview-msg" role="status" aria-live="polite"></p>
      </section>`;
    const draft = loadDraft(url);
    bind(root, url);
    applyDraft(root, url, draft);
    filterRows(root);
    void refreshPreview(root, url);
  }

  window.UfcParlay = { onCardLoaded };
})();
