/**
 * Painel admin — /api/admin/* (sessão admin_panel).
 */
const $ = (id) => document.getElementById(id);

function toast(msg, ok) {
  const el = $("toolbarStatus");
  if (!el) return;
  el.textContent = msg || "";
  el.classList.toggle("admin-msg--ok", !!ok);
}

async function apiGet(path) {
  const res = await fetch(path, { credentials: "same-origin", headers: { Accept: "application/json" } });
  return res.json();
}

async function apiPatch(path, body) {
  const res = await fetch(path, {
    method: "PATCH",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}

async function apiPut(path, body) {
  const res = await fetch(path, {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body || {}),
  });
  return res.json();
}

function esc(s) {
  if (s == null) return "";
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

function riskClass(tier) {
  const t = (tier || "").toUpperCase();
  if (t.includes("SKIP") || t === "SKIP") return "admin-tag admin-tag--skip";
  if (t.includes("RISKY") || t === "RISKY") return "admin-tag admin-tag--risky";
  return "admin-tag admin-tag--safe";
}

function statusClass(st) {
  if (st === "open") return "admin-tag admin-tag--open";
  if (st === "won") return "admin-tag admin-tag--won";
  if (st === "lost") return "admin-tag admin-tag--lost";
  return "admin-tag";
}

function showModal(message, onOk) {
  const ov = document.getElementById("confirmModal");
  const tx = document.getElementById("confirmText");
  const ok = document.getElementById("confirmOk");
  const cancel = document.getElementById("confirmCancel");
  if (!ov || !tx || !ok || !cancel) {
    if (window.confirm(message)) onOk();
    return;
  }
  tx.textContent = message;
  ov.hidden = false;
  const clean = () => {
    ok.removeEventListener("click", onOkClick);
    cancel.removeEventListener("click", onCancel);
  };
  const onOkClick = () => {
    clean();
    ov.hidden = true;
    onOk();
  };
  const onCancel = () => {
    clean();
    ov.hidden = true;
  };
  ok.addEventListener("click", onOkClick, { once: true });
  cancel.addEventListener("click", onCancel, { once: true });
}

let usersData = [];
let betsData = [];
let rankData = [];
let iptvChannels = [];

function sortTable(data, key, numeric) {
  const copy = data.slice();
  copy.sort((a, b) => {
    const va = a[key];
    const vb = b[key];
    if (numeric) return (Number(va) || 0) - (Number(vb) || 0);
    return String(va || "").localeCompare(String(vb || ""), "pt-BR");
  });
  return copy;
}

function bindSort(tableId, dataRef, onSorted) {
  const table = document.getElementById(tableId);
  if (!table) return;
  let dir = 1;
  let lastKey = "";
  table.querySelectorAll("th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.getAttribute("data-sort");
      if (!key) return;
      if (lastKey === key) dir *= -1;
      else {
        lastKey = key;
        dir = 1;
      }
      const num =
        th.classList.contains("num") ||
        ["id", "balance", "total_bets", "stake", "odds_taken", "payout"].includes(key);
      const sorted = sortTable(dataRef(), key, num);
      if (dir < 0) sorted.reverse();
      onSorted(sorted);
    });
  });
}

async function loadStats() {
  const data = await apiGet("/api/admin/stats");
  if (!data.ok) return;
  const u = $("stUsers");
  const o = $("stOpen");
  const s = $("stSettled");
  const c = $("stCredits");
  if (u) u.textContent = String(data.total_users ?? "—");
  if (o) o.textContent = String(data.bets_open ?? "—");
  if (s) s.textContent = String(data.bets_settled ?? "—");
  if (c) c.textContent = String(data.credits_total ?? "—");
  toast("Atualizado.", true);
}

async function loadHero() {
  const st = $("heroStatus");
  const block = $("heroBlock");
  try {
    const ev = await apiGet("/api/ufc/events");
    if (!ev.ok || !ev.future_events || !ev.future_events.length) {
      if (st) st.textContent = "Nenhum evento futuro na lista.";
      return;
    }
    const next = ev.next_future || ev.future_events[0];
    const url = (next.url || "").trim();
    if (!url) {
      if (st) st.textContent = "URL do evento indisponível.";
      return;
    }
    const meta = await apiGet("/api/ufc/event-meta?url=" + encodeURIComponent(url));
    if (st) st.textContent = "";
    if (block) block.hidden = false;
    const img = $("heroImg");
    const title = $("heroTitle");
    const when = $("heroWhen");
    const link = $("heroLink");
    if (img && meta.hero_image_url) {
      img.src = "/api/proxy-image?url=" + encodeURIComponent(meta.hero_image_url);
      img.alt = meta.event_title || "Evento";
    } else if (img) {
      img.removeAttribute("src");
    }
    if (title) title.textContent = meta.event_title || next.title || "Evento UFC";
    if (when) when.textContent = meta.event_starts_at ? "Início: " + meta.event_starts_at : "";
    if (link) {
      link.href = "/?event_url=" + encodeURIComponent(url);
      link.target = "_blank";
      link.rel = "noopener";
    }
  } catch (e) {
    if (st) st.textContent = e.message || "Erro ao carregar evento.";
  }
}

function renderUsers(users) {
  const tbody = $("usersBody");
  const wrap = $("usersWrap");
  const msg = $("usersMsg");
  if (!tbody) return;
  tbody.innerHTML = users
    .map((u) => {
      const st = u.blocked ? "bloqueado" : "ativo";
      return `<tr data-uid="${esc(String(u.id))}">
        <td>${esc(String(u.id))}</td>
        <td>${esc(u.email)}</td>
        <td class="num">${esc(String(u.balance))}</td>
        <td>${u.blocked ? '<span class="admin-neg">bloqueado</span>' : '<span class="admin-pos">ativo</span>'}</td>
        <td class="num">${esc(String(u.total_bets ?? 0))}</td>
        <td class="admin-actions-cell">
          <input type="number" class="admin-input admin-input--narrow" data-bal="${u.id}" value="${esc(String(u.balance))}" min="0" />
          <button type="button" class="button-adjust" data-save="${u.id}">Salvar saldo</button>
          <button type="button" class="${u.blocked ? "button-unblock" : "button-block"}" data-block="${u.id}" data-isblocked="${u.blocked ? "1" : "0"}">${u.blocked ? "Desbloquear" : "Bloquear"}</button>
        </td>
      </tr>`;
    })
    .join("");
  if (wrap) wrap.hidden = false;
  if (msg) msg.textContent = users.length + " usuário(s).";
}

async function loadUsers() {
  const q = $("userSearch");
  const qs = q && q.value.trim() ? "?q=" + encodeURIComponent(q.value.trim()) : "";
  const data = await apiGet("/api/admin/users" + qs);
  const msg = $("usersMsg");
  if (!data.ok) {
    if (msg) msg.textContent = data.message || "Sem permissão.";
    return;
  }
  usersData = data.users || [];
  renderUsers(usersData);
}

function drawRankChart(ranking) {
  const canvas = document.getElementById("rankChart");
  if (!canvas || !ranking || !ranking.length) return;
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.fillStyle = "#1e1e1e";
  ctx.fillRect(0, 0, w, h);
  const top = ranking.slice(0, 10);
  const n = top.length;
  if (!n) return;
  const maxPct = Math.max(...top.map((r) => r.win_rate_pct || 0), 1);
  const barW = (w - 40) / n - 4;
  top.forEach((r, i) => {
    const pct = r.win_rate_pct || 0;
    const bh = (pct / maxPct) * (h - 50);
    const x = 20 + i * (barW + 4);
    const y = h - 30 - bh;
    ctx.fillStyle = pct >= 50 ? "#2ecc71" : "#f1c40f";
    ctx.fillRect(x, y, barW, bh);
    ctx.fillStyle = "#aaa";
    ctx.font = "10px Arial";
    ctx.fillText(String(pct).slice(0, 5), x, h - 12);
  });
}

async function loadRanking() {
  const data = await apiGet("/api/admin/ranking?limit=100");
  const body = document.getElementById("rankBody");
  if (!data.ok || !body) return;
  rankData = data.ranking || [];
  drawRankChart(rankData);
  body.innerHTML = rankData
    .map((r, i) => {
      const net = r.net_credits ?? 0;
      const netCls = net >= 0 ? "admin-pos" : "admin-neg";
      return `<tr>
        <td>${i + 1}</td>
        <td>${esc(r.email)}</td>
        <td class="num">${esc(String(r.win_rate_pct))}%</td>
        <td class="num">${esc(String(r.wins))}</td>
        <td class="num">${esc(String(r.losses))}</td>
        <td class="num ${netCls}">${net >= 0 ? "+" : ""}${esc(String(net))}</td>
      </tr>`;
    })
    .join("");
}

function renderBets(bets) {
  const tbody = document.getElementById("betsBody");
  const wrap = document.getElementById("betsWrap");
  const msg = document.getElementById("betsMsg");
  if (!tbody) return;
  tbody.innerHTML = bets
    .map((b) => {
      const fight = `${esc(b.red_name || "?")} × ${esc(b.blue_name || "?")}`;
      const edge = b.value_edge != null ? String(b.value_edge) : "—";
      const risk = b.risk_tier || "—";
      const rc = riskClass(risk);
      const payout = b.payout != null ? b.payout : 0;
      return `<tr>
        <td>${esc(String(b.id))}</td>
        <td>${esc(b.user_email || b.user_id)}</td>
        <td>${fight}</td>
        <td>${esc(b.side)}</td>
        <td class="num">${esc(String(b.stake))}</td>
        <td class="num">${esc(String(b.odds_taken))}</td>
        <td class="num">${edge}</td>
        <td><span class="${rc}">${esc(risk)}</span></td>
        <td><span class="${statusClass(b.status)}">${esc(b.status)}</span></td>
        <td class="num">${esc(String(payout))}</td>
      </tr>`;
    })
    .join("");
  if (wrap) wrap.hidden = false;
  if (msg) msg.textContent = bets.length + " linha(s).";
}

function betsQueryString() {
  const uid = document.getElementById("fltUid") && document.getElementById("fltUid").value.trim();
  const url = document.getElementById("fltUrl") && document.getElementById("fltUrl").value.trim();
  const search = document.getElementById("fltSearch") && document.getElementById("fltSearch").value.trim();
  const status = document.getElementById("fltStatus") && document.getElementById("fltStatus").value;
  let q = "/api/admin/bets?limit=300";
  if (uid) q += "&user_id=" + encodeURIComponent(uid);
  if (url) q += "&event_url=" + encodeURIComponent(url);
  if (search) q += "&search=" + encodeURIComponent(search);
  if (status) q += "&status=" + encodeURIComponent(status);
  return q;
}

async function loadBets() {
  const msg = document.getElementById("betsMsg");
  if (msg) msg.textContent = "Carregando…";
  const data = await apiGet(betsQueryString());
  if (!data.ok) {
    if (msg) msg.textContent = data.message || "Erro.";
    return;
  }
  betsData = data.bets || [];
  renderBets(betsData);
}

function updateCsvHref() {
  const a = document.getElementById("btnCsv");
  if (a) a.href = betsQueryString() + "&format=csv";
}

function renderIptvChannelSelect(filtered) {
  const sel = document.getElementById("iptvChannelSelect");
  if (!sel) return;
  const list = Array.isArray(filtered) ? filtered : [];
  const max = 1500; // evitar travar UI com playlists enormes
  const shown = list.slice(0, max);
  sel.innerHTML =
    `<option value="">— selecione —</option>` +
    shown
      .map((c, i) => {
        const name = c.name || "Canal";
        const url = c.url || "";
        const label = name + (c.group ? " · " + c.group : "");
        // value = índice no array filtrado (vamos procurar por url também)
        return `<option value="${esc(String(i))}" data-url="${esc(url)}" data-name="${esc(name)}">${esc(label)}</option>`;
      })
      .join("");
  const msg = document.getElementById("iptvAdminMsg");
  if (msg) msg.textContent = `Playlist carregada: ${list.length} canais (mostrando ${shown.length}).`;
}

function applyIptvSearch() {
  const q = document.getElementById("iptvChannelSearch");
  const term = (q && q.value ? q.value.trim().toLowerCase() : "") || "";
  if (!term) {
    renderIptvChannelSelect(iptvChannels);
    return;
  }
  const filtered = (iptvChannels || []).filter((c) => String(c.name || "").toLowerCase().includes(term));
  renderIptvChannelSelect(filtered);
}

function setPickedPreview(name, url) {
  const p = document.getElementById("iptvPickedPreview");
  if (!p) return;
  if (!url) {
    p.textContent = "";
    return;
  }
  p.textContent = `Selecionado: ${name || "Canal"} · ${url}`;
}

async function loadIptvAdmin() {
  const msg = document.getElementById("iptvAdminMsg");
  try {
    const data = await apiGet("/api/admin/iptv-settings");
    if (!data.ok) {
      if (msg) msg.textContent = data.message || "Sem permissão.";
      return;
    }
    const s = data.settings || {};
    const url = document.getElementById("iptvPlaylistUrl");
    const en = document.getElementById("iptvAutoplayEnabled");
    if (url) {
      // Nunca receber URL real do servidor; mostrar só mascarada
      url.value = s.playlist_url_masked || "";
      url.placeholder = s.has_playlist_url ? "playlist salva (mascarada)" : "https://.../get.php?...";
    }
    if (en) en.checked = !!s.autoplay_enabled;
    setPickedPreview(s.selected_channel_name || "", s.selected_channel_url || "");
    if (msg) msg.textContent = "Config IPTV carregada. Carregue a playlist para escolher um canal.";
  } catch (e) {
    if (msg) msg.textContent = e.message || "Erro ao carregar IPTV.";
  }
}

async function loadIptvPlaylist() {
  const msg = document.getElementById("iptvAdminMsg");
  const url = document.getElementById("iptvPlaylistUrl");
  if (msg) msg.textContent = "Carregando playlist…";
  // Se o admin colou uma nova URL aqui, ele precisa salvar antes (para não expor credenciais depois).
  const raw = (url && url.value ? url.value.trim() : "") || "";
  if (raw && raw.includes("***")) {
    // valor mascarado; usar a salva no servidor
  }
  if (raw && !raw.includes("***") && /^https?:\/\//i.test(raw)) {
    // salvar nova URL e só então carregar do servidor
    const en = document.getElementById("iptvAutoplayEnabled");
    const payload = {
      playlist_url: raw,
      autoplay_enabled: !!(en && en.checked),
      selected_channel_name: "",
      selected_channel_url: "",
    };
    const saved = await apiPut("/api/admin/iptv-settings", payload);
    if (!saved.ok) {
      if (msg) msg.textContent = saved.message || saved.error || "Falha ao salvar playlist.";
      return;
    }
    // atualizar campo com versão mascarada
    await loadIptvAdmin();
  }
  const data = await apiGet("/api/admin/iptv-playlist");
  if (!data.ok) {
    if (msg) {
      const tried = Array.isArray(data.attempted_urls) && data.attempted_urls.length
        ? " URLs tentadas: " + data.attempted_urls.join(" | ")
        : "";
      msg.textContent = (data.message || "Falha ao carregar playlist.") + tried;
    }
    return;
  }
  iptvChannels = Array.isArray(data.channels) ? data.channels : [];
  applyIptvSearch();
}

async function saveIptvAdmin() {
  const msg = document.getElementById("iptvAdminMsg");
  const url = document.getElementById("iptvPlaylistUrl");
  const en = document.getElementById("iptvAutoplayEnabled");
  const sel = document.getElementById("iptvChannelSelect");
  let pickedName = "";
  let pickedUrl = "";
  if (sel) {
    const opt = sel.options && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex] : null;
    if (opt) {
      pickedName = opt.getAttribute("data-name") || "";
      pickedUrl = opt.getAttribute("data-url") || "";
    }
  }
  const payload = {
    // Campo vazio = manter URL salva no servidor.
    playlist_url:
      url && url.value && url.value.includes("***")
        ? ""
        : ((url && url.value ? url.value.trim() : "") || ""),
    autoplay_enabled: !!(en && en.checked),
    selected_channel_name: pickedName,
    selected_channel_url: pickedUrl,
  };
  if (msg) msg.textContent = "Salvando…";
  const data = await apiPut("/api/admin/iptv-settings", payload);
  if (!data.ok) {
    if (msg) msg.textContent = data.message || data.error || "Falha ao salvar.";
    return;
  }
  await loadIptvAdmin();
  setPickedPreview(pickedName, pickedUrl);
  if (msg) msg.textContent = "IPTV salvo.";
}

document.addEventListener("DOMContentLoaded", () => {
  if (!window.__ADMIN_BOOT__) return;

  loadStats();
  loadHero();
  loadUsers();
  loadBets();
  loadRanking();
  loadIptvAdmin();

  const poll = window.setInterval(loadStats, 30000);
  window.addEventListener("beforeunload", () => clearInterval(poll));

  $("btnRefreshStats") && $("btnRefreshStats").addEventListener("click", loadStats);
  $("userSearch") &&
    $("userSearch").addEventListener(
      "input",
      debounce(() => loadUsers(), 300)
    );
  $("btnLoadBets") && $("btnLoadBets").addEventListener("click", loadBets);
  $("fltStatus") &&
    $("fltStatus").addEventListener("change", () => {
      updateCsvHref();
    });
  $("fltUid") && $("fltUid").addEventListener("change", updateCsvHref);
  $("fltUrl") && $("fltUrl").addEventListener("change", updateCsvHref);
  $("fltSearch") && $("fltSearch").addEventListener("change", updateCsvHref);
  updateCsvHref();

  $("usersBody") &&
    $("usersBody").addEventListener("click", async (ev) => {
      const save = ev.target.getAttribute("data-save");
      if (save) {
        const inp = document.querySelector(`input[data-bal="${save}"]`);
        const v = parseInt(inp && inp.value, 10);
        showModal("Ajustar saldo deste usuário para " + v + " créditos?", async () => {
          const data = await apiPatch("/api/admin/users/" + save, { balance: v, note: "Painel admin" });
          toast(data.ok ? "Saldo atualizado." : data.message || "Erro", !!data.ok);
          loadUsers();
          loadStats();
        });
        return;
      }
      const bbtn = ev.target.closest("[data-block]");
      if (bbtn) {
        const id = bbtn.getAttribute("data-block");
        const was = bbtn.getAttribute("data-isblocked") === "1";
        showModal(was ? "Desbloquear este usuário?" : "Bloquear este usuário?", async () => {
          const data = await apiPatch("/api/admin/users/" + id, { blocked: !was });
          toast(data.ok ? "OK." : data.message || "Erro", !!data.ok);
          loadUsers();
        });
      }
    });

  bindSort("usersTable", () => usersData, (sorted) => {
    usersData = sorted;
    renderUsers(sorted);
  });
  bindSort("betsTable", () => betsData, (sorted) => {
    betsData = sorted;
    renderBets(sorted);
  });

  $("settleForm") &&
    $("settleForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const fd = new FormData(e.target);
      const payload = {
        event_url: fd.get("event_url"),
        fight_index: parseInt(fd.get("fight_index"), 10),
        winner_side: fd.get("winner_side"),
      };
      showModal("Liquidar todas as apostas abertas desta luta com o vencedor indicado?", async () => {
        const msg = $("settleMsg");
        if (msg) msg.textContent = "Processando…";
        const data = await apiPost("/api/admin/settle", payload);
        if (!data.ok) {
          if (msg) msg.textContent = data.message || data.error || "Falha.";
          return;
        }
        if (msg) msg.textContent = "Liquidadas: " + data.settled + " aposta(s).";
        loadBets();
        loadStats();
        loadRanking();
        loadUsers();
      });
    });

  $("btnSaveIptv") && $("btnSaveIptv").addEventListener("click", saveIptvAdmin);
  $("btnLoadIptvPlaylist") && $("btnLoadIptvPlaylist").addEventListener("click", loadIptvPlaylist);
  $("iptvChannelSearch") && $("iptvChannelSearch").addEventListener("input", debounce(applyIptvSearch, 180));
  $("iptvChannelSelect") &&
    $("iptvChannelSelect").addEventListener("change", () => {
      const sel = $("iptvChannelSelect");
      const opt = sel && sel.options && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex] : null;
      setPickedPreview(opt ? opt.getAttribute("data-name") || "" : "", opt ? opt.getAttribute("data-url") || "" : "");
    });
});

function debounce(fn, ms) {
  let t;
  return function () {
    clearTimeout(t);
    t = setTimeout(fn, ms);
  };
}
