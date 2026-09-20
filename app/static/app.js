/* Dashboard da demo. Vanilla JS, sem build. TODAS as URLs são relativas ('stats', 'stream', 'score'…)
   para funcionar atrás de qualquer prefixo (ex.: /projects/fraud/). Todo texto vindo da API entra
   por textContent. Sem script/estilo inline (CSP do domínio). */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var nf0 = new Intl.NumberFormat('pt-BR');
  var brl = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
  var pct = function (v, d) { return (v * 100).toLocaleString('pt-BR', { maximumFractionDigits: d === undefined ? 1 : d }) + '%'; };
  var dec = function (v, d) { return v.toLocaleString('pt-BR', { minimumFractionDigits: d, maximumFractionDigits: d }); };

  var FEATURE_LABELS = {
    amount: 'Valor da compra', amount_zscore_user: 'Valor vs. padrão do usuário (z)', amount_ratio_to_user_max: 'Valor ÷ maior compra recente',
    txn_count_1m: 'Transações no último minuto', txn_count_10m: 'Transações nos últimos 10 min', txn_count_1h: 'Transações na última hora',
    txn_count_24h: 'Transações nas últimas 24 h', txn_count_30d: 'Transações nos últimos 30 dias', seconds_since_last: 'Tempo desde a última transação',
    is_new_device: 'Dispositivo novo', is_new_city: 'Cidade nova', is_new_country: 'País novo', is_new_merchant: 'Merchant novo',
    km_from_last: 'Distância da última transação', implied_speed_kmh: 'Velocidade implícita', merchant_category_code: 'Categoria do merchant',
    is_risky_category: 'Categoria de risco', hour_of_day: 'Hora do dia'
  };
  var CATEGORY_LABELS = {
    groceries: 'Supermercado', restaurants: 'Restaurantes', fuel: 'Combustível', pharmacy: 'Farmácia', transport: 'Transporte',
    entertainment: 'Entretenimento', utilities: 'Contas e serviços', online_retail: 'Varejo online', electronics: 'Eletrônicos',
    travel: 'Viagens', jewelry: 'Joalheria', gift_cards: 'Gift cards', crypto_exchange: 'Corretora de cripto'
  };
  var MODEL_LABELS = {
    baseline_regra_valor: 'Regra: “valor alto = suspeito”', logistic_regression: 'Regressão logística',
    xgboost_plain: 'XGBoost', xgboost_weighted: 'XGBoost (peso de classe)', lightgbm_plain: 'LightGBM', lightgbm_weighted: 'LightGBM (peso de classe)'
  };
  var PRESETS = {
    normal: { amount: 45, city: 'São Paulo', category: 'restaurants', n1: 0, n10: 1, sec: 7200, km: 0, newdev: false, newmerch: false },
    suspicious: { amount: 4200, city: 'Tokyo', category: 'crypto_exchange', n1: 5, n10: 7, sec: 20, km: 18500, newdev: true, newmerch: true }
  };

  var state = { model: null, stats: null, feed: [], flaggedOnly: false, lastSeq: 0, conn: { api: null, stream: 'connecting' }, catList: [] };
  var renderers = {}; // gráficos redesenhados no resize

  /* ----------------------------------------------------------------- utilidades */
  function node(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = text;
    return n;
  }
  function svgIcon(kind) {
    var NS = 'http://www.w3.org/2000/svg';
    var s = document.createElementNS(NS, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('fill', 'none'); s.setAttribute('stroke', 'currentColor');
    s.setAttribute('stroke-width', '2.4'); s.setAttribute('stroke-linecap', 'round'); s.setAttribute('stroke-linejoin', 'round'); s.setAttribute('aria-hidden', 'true');
    var p = document.createElementNS(NS, 'path');
    p.setAttribute('d', { flag: 'M5 21V4h12l-2.5 4.5L17 13H5', check: 'M20 6 9 17l-5-5', x: 'M18 6 6 18M6 6l12 12' }[kind]);
    s.appendChild(p);
    return s;
  }
  function tag(cls, icon, text) {
    var t = node('span', 'tag ' + cls);
    if (icon) t.appendChild(svgIcon(icon));
    t.appendChild(document.createTextNode(text));
    return t;
  }
  function setText(id, text) { var e = $(id); if (e) e.textContent = text; }
  function setValue(id, value, unit) {
    var e = $(id); e.replaceChildren(document.createTextNode(value));
    if (unit) e.appendChild(node('span', 'unit', unit));
  }
  function fmtDuration(s) {
    if (s >= 604800) return '≥ 7 dias';
    if (s >= 3600) return dec(s / 3600, 1) + ' h';
    if (s >= 60) return dec(s / 60, 1) + ' min';
    return nf0.format(Math.round(s)) + ' s';
  }
  function fmtLatency(ms) { // [valor, unidade]; acima de 1 s mostra em segundos (ex.: backlog durante o aquecimento)
    return ms >= 1000 ? [dec(ms / 1000, 1), 's'] : [nf0.format(Math.round(ms)), 'ms'];
  }
  function fmtFeature(name, v) {
    if (name.indexOf('is_') === 0) return v ? 'sim' : 'não';
    switch (name) {
      case 'amount': return brl.format(v);
      case 'seconds_since_last': return fmtDuration(v);
      case 'km_from_last': return nf0.format(Math.round(v)) + ' km';
      case 'implied_speed_kmh': return nf0.format(Math.round(v)) + ' km/h';
      case 'hour_of_day': return nf0.format(v) + ' h';
      case 'amount_zscore_user': return dec(v, 1);
      case 'amount_ratio_to_user_max': return dec(v, 1) + '×';
      case 'merchant_category_code': return CATEGORY_LABELS[state.catList[v]] || String(v);
      default: return nf0.format(v);
    }
  }
  function categoryLabel(c) { return CATEGORY_LABELS[c] || c; }
  function scoreText(s) { return s < 0.001 ? '< 0,001' : dec(s, 3); }

  function api(path, opts) {
    opts = opts || {};
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, 10000);
    return fetch(path, Object.assign({ signal: ctrl.signal, headers: { Accept: 'application/json' } }, opts)).then(function (res) {
      clearTimeout(timer);
      if (res.ok) return res.json();
      return res.json().catch(function () { return {}; }).then(function (body) {
        var err = new Error((body && typeof body.detail === 'string' && body.detail) || ('HTTP ' + res.status));
        err.status = res.status; err.body = body; err.retryAfter = parseInt(res.headers.get('Retry-After') || '0', 10);
        throw err;
      });
    }, function (e) { clearTimeout(timer); throw e; });
  }

  /* ----------------------------------------------------------------- conexão / estado ao vivo */
  function updateConn() {
    var c = state.conn, pill = $('conn'), text = 'Conectando…', st = 'connecting';
    if (c.api === false) { st = 'down'; text = 'Sem conexão com a API — tentando reconectar…'; }
    else if (c.stream === 'open' && c.api) { st = 'live'; text = 'Ao vivo'; }
    else if (c.stream === 'error') { st = 'wait'; text = 'Reconectando o stream…'; }
    pill.setAttribute('data-state', st);
    setText('conn-text', text);
    $('live-area').classList.toggle('stale', c.api === false); // nunca mostra dado velho como se fosse atual
    $('drift-card').classList.toggle('stale', c.api === false);
  }

  function poll(fn, intervalMs) {
    var failures = 0;
    (function tick() {
      var wait = intervalMs;
      if (document.hidden) { setTimeout(tick, intervalMs); return; }
      fn().then(function () { failures = 0; state.conn.api = true; updateConn(); }, function () {
        failures++; if (failures >= 2) { state.conn.api = false; updateConn(); }
        wait = Math.min(30000, intervalMs * Math.pow(2, failures));
      }).then(function () { setTimeout(tick, wait); });
    })();
  }

  function connectStream() {
    if (!window.EventSource) return;
    var es = new EventSource('stream'); // reconecta sozinho (retry: 3000 no stream)
    es.onopen = function () { state.conn.stream = 'open'; updateConn(); };
    es.onerror = function () { state.conn.stream = 'error'; updateConn(); };
    es.addEventListener('transaction', function (e) {
      try { pushRows([JSON.parse(e.data)]); } catch (err) { /* evento malformado: ignora */ }
    });
  }

  /* ----------------------------------------------------------------- feed */
  var renderQueued = false;
  function pushRows(rows) {
    rows.forEach(function (r) { state.feed.unshift(r); });
    if (state.feed.length > 300) state.feed.length = 300;
    if (!renderQueued) { renderQueued = true; setTimeout(function () { renderQueued = false; renderFeed(); }, 300); } // no aquecimento chegam centenas/s
  }

  function renderFeed() {
    var body = $('feed-body');
    var rows = state.feed.filter(function (r) { return !state.flaggedOnly || r.flagged; }).slice(0, 14);
    var prevSeq = state.lastSeq;
    var fresh = rows.filter(function (r) { return r.seq > prevSeq; }).length;
    if (state.feed.length) state.lastSeq = Math.max(state.lastSeq, state.feed[0].seq);
    body.replaceChildren();
    if (!rows.length) {
      var tr = node('tr'); var td = node('td', 'empty', state.flaggedOnly ? 'Nenhuma transação sinalizada recente.' : 'Aguardando transações…'); td.colSpan = 8; tr.appendChild(td); body.appendChild(tr); return;
    }
    rows.forEach(function (r) {
      var tr = node('tr', (r.flagged ? 'flagged' : '') + (prevSeq && r.seq > prevSeq && fresh <= 4 ? ' flash' : ''));
      tr.appendChild(node('td', 'num', new Date(r.scored_at).toLocaleTimeString('pt-BR')));
      tr.appendChild(node('td', 'c-user mono', r.user_id));
      tr.appendChild(node('td', 'c-cat', categoryLabel(r.merchant_category)));
      tr.appendChild(node('td', 'c-city', r.city));
      tr.appendChild(node('td', 'num', brl.format(r.amount)));
      var sc = node('td', 'num'); var mini = node('span', 'mini'); var bar = node('i'); bar.style.width = Math.max(2, Math.round(r.score * 100)) + '%'; mini.appendChild(bar);
      sc.appendChild(mini); sc.appendChild(document.createTextNode(scoreText(r.score))); tr.appendChild(sc);
      var dc = node('td'); dc.appendChild(r.flagged ? tag('flag', 'flag', 'Sinalizada') : tag('ok', 'check', 'Aprovada')); tr.appendChild(dc);
      var lc = node('td', 'c-label');
      if (r.is_fraud === true) lc.appendChild(r.flagged ? tag('hit', 'check', 'Fraude detectada') : tag('miss', 'x', 'Fraude perdida'));
      else if (r.is_fraud === false) lc.appendChild(r.flagged ? tag('miss', 'x', 'Falso alarme') : tag('ok', null, 'Legítima'));
      else lc.textContent = '—';
      tr.appendChild(lc);
      body.appendChild(tr);
    });
  }

  /* ----------------------------------------------------------------- KPIs e gráficos operacionais */
  function renderStats(s) {
    state.stats = s;
    setValue('kpi-tpm', nf0.format(s.last_minute));
    setText('kpi-tpm-sub', 'média de ' + nf0.format(Math.round(s.total / s.window_minutes)) + '/min em ' + s.window_minutes + ' min');
    setValue('kpi-flag', s.total ? pct(s.flag_rate) : '—');
    setText('kpi-flag-sub', nf0.format(s.flagged) + ' sinalizadas de ' + nf0.format(s.total));
    if (s.latency_ms && s.latency_ms.p95 !== null) {
      var p95 = fmtLatency(s.latency_ms.p95);
      setValue('kpi-lat', p95[0], p95[1]);
      setText('kpi-lat-sub', 'p50 ' + fmtLatency(s.latency_ms.p50).join(' ') + ' · publicação → banco');
    }
    else { setValue('kpi-lat', '—'); setText('kpi-lat-sub', ' '); }
    var l = s.labeled;
    if (l && l.recall !== null) { setValue('kpi-recall', pct(l.recall)); setText('kpi-recall-sub', 'precision ' + pct(l.precision || 0) + ' · ' + nf0.format(l.n) + ' rotuladas'); }
    else { setValue('kpi-recall', '—'); setText('kpi-recall-sub', 'aguardando fraudes no feed'); }
    renderTimeline(); renderHist(); renderConfusion();
  }

  function minuteLabel(d) { return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }); }

  function renderTimeline() {
    var s = state.stats; if (!s) return;
    var byMin = {}, latest = 0;
    s.timeline.forEach(function (p) { var t = Math.floor(Date.parse(p.t) / 60000); byMin[t] = p; if (t > latest) latest = t; });
    var end = Math.floor(Date.now() / 60000);
    if (latest && Math.abs(latest - end) > 5) end = latest; // relógio do cliente fora do lugar: segue o servidor
    var n = Math.min(s.window_minutes, 15), data = [], rowsHtml = [];
    for (var i = end - n + 1; i <= end; i++) {
      var p = byMin[i] || { count: 0, flagged: 0 }, d = new Date(i * 60000);
      data.push({ label: minuteLabel(d), title: minuteLabel(d) + (i === end ? ' (minuto em andamento)' : ''), values: [p.count - p.flagged, p.flagged] });
    }
    var series = [{ name: 'Aprovadas', cls: 's1' }, { name: 'Sinalizadas', cls: 's2' }];
    var lg = $('legend-timeline');
    if (!lg.childElementCount) series.forEach(function (x) { var i = node('span'); i.appendChild(node('span', 'key ' + x.cls)); i.appendChild(document.createTextNode(x.name)); lg.appendChild(i); });
    renderers.timeline = function () { Viz.stackedColumns($('chart-timeline'), data, series); };
    renderers.timeline();
    fillTable('tbl-timeline', ['Minuto', 'Aprovadas', 'Sinalizadas', 'Total'], data.map(function (d) { return [d.label, nf0.format(d.values[0]), nf0.format(d.values[1]), nf0.format(d.values[0] + d.values[1])]; }), [1, 2, 3]);
  }

  function renderHist() {
    var s = state.stats; if (!s || !state.model) return;
    var thr = state.model.threshold, counts = s.score_histogram;
    renderers.hist = function () { Viz.histogramLog($('chart-hist'), counts, thr); };
    renderers.hist();
    setText('hist-note', s.total ? pct(s.flag_rate) + ' das ' + nf0.format(s.total) + ' transações da janela ficaram acima do limite e foram sinalizadas. A escala é logarítmica: a cauda à direita é pequena, mas é ela que importa.' : '');
    fillTable('tbl-hist', ['Faixa de score', 'Transações'], counts.map(function (c, i) { return [dec(i / 10, 1) + ' – ' + dec((i + 1) / 10, 1), nf0.format(c)]; }), [1]);
  }

  function fillTable(id, head, rows, numCols) {
    var t = $(id); t.replaceChildren();
    var tr = node('tr'); head.forEach(function (h, i) { var th = node('th', numCols && numCols.indexOf(i) >= 0 ? 'num' : '', h); th.scope = 'col'; tr.appendChild(th); });
    var thead = node('thead'); thead.appendChild(tr); t.appendChild(thead);
    var tb = node('tbody');
    rows.forEach(function (r) { var row = node('tr'); r.forEach(function (c, i) { row.appendChild(node('td', numCols && numCols.indexOf(i) >= 0 ? 'num' : '', c)); }); tb.appendChild(row); });
    t.appendChild(tb);
  }

  function renderConfusion() {
    var s = state.stats, t = $('tbl-confusion'), l = s && s.labeled;
    t.replaceChildren();
    if (!l) { setText('confusion-note', 'Ainda sem transações rotuladas na janela.'); return; }
    var head = node('tr'); ['', 'Modelo sinalizou', 'Modelo aprovou'].forEach(function (h) { var th = node('th', '', h); th.scope = 'col'; head.appendChild(th); });
    var thead = node('thead'); thead.appendChild(head); t.appendChild(thead);
    var tb = node('tbody');
    [['Era fraude', l.tp, 'good', l.fn, 'bad'], ['Era legítima', l.fp, 'bad', l.tn, 'good']].forEach(function (r) {
      var tr = node('tr'); var th = node('th', 'row', r[0]); th.scope = 'row'; tr.appendChild(th);
      tr.appendChild(node('td', 'big ' + r[2], nf0.format(r[1]))); tr.appendChild(node('td', 'big ' + r[4], nf0.format(r[3]))); tb.appendChild(tr);
    });
    t.appendChild(tb);
    setText('confusion-note', 'Precision ' + pct(l.precision || 0) + ' · recall ' + pct(l.recall || 0) + ' em ' + nf0.format(l.n) + ' transações. Verde = acerto; laranja/vermelho = erro (falso alarme ou fraude perdida).');
  }

  /* ----------------------------------------------------------------- modelo */
  function renderModel(m) {
    state.model = m; state.catList = m.options.categories;
    var t = m.metrics.test;
    setText('model-line', 'Modelo ' + m.version + ' · limite de decisão ' + dec(m.threshold, 3));
    var dl = $('model-metrics'); dl.replaceChildren();
    [['PR-AUC', dec(m.metrics.test_pr_auc, 3)], ['Precision', pct(t.precision)], ['Recall', pct(t.recall)], ['F1', dec(t.f1, 3)], ['Falso positivo (FPR)', pct(t.fpr, 2)]].forEach(function (kv) {
      var d = node('div'); d.appendChild(node('dt', '', kv[0])); d.appendChild(node('dd', '', kv[1])); dl.appendChild(d);
    });
    var data = m.data || {};
    setText('model-criterion', 'Limite: ' + (m.threshold_criterion || '') + '. Treinado em ' + (m.trained_at || '').slice(0, 10) + ' com dados sintéticos (' + nf0.format(data.users || 0) + ' usuários × ' + (data.days || 0) + ' dias); avaliado em ' + nf0.format((m.split_sizes || {}).test || 0) + ' transações futuras (split temporal), com ' + pct(m.metrics.test_fraud_rate) + ' de fraude.');
    var rows = Object.keys(m.comparison || {}).map(function (k) { return { key: k, c: m.comparison[k] }; }).sort(function (a, b) { return b.c.test_pr_auc - a.c.test_pr_auc; });
    var served = function (k) { return m.version.indexOf(k) >= 0; };
    renderers.models = function () {
      Viz.hbars($('chart-models'), rows.map(function (r) {
        return { label: (MODEL_LABELS[r.key] || r.key) + (served(r.key) ? ' — servido' : ''), value: r.c.test_pr_auc, kind: served(r.key) ? 's1' : 'deemph', display: dec(r.c.test_pr_auc, 3), detailName: 'PR-AUC (teste)' };
      }), { max: 1 });
    };
    renderers.models();
    fillTable('tbl-models', ['Modelo', 'PR-AUC', 'Precision', 'Recall', 'F1', 'FPR'], rows.map(function (r) {
      var x = r.c.test; return [(MODEL_LABELS[r.key] || r.key) + (served(r.key) ? ' (servido)' : ''), dec(r.c.test_pr_auc, 3), pct(x.precision), pct(x.recall), dec(x.f1, 3), pct(x.fpr, 2)];
    }), [1, 2, 3, 4, 5]);
    Array.prototype.forEach.call($('tbl-models').querySelectorAll('tbody tr'), function (tr, i) { if (served(rows[i].key)) tr.className = 'served'; });
    buildForm(m.options);
    renderHist();
  }

  /* ----------------------------------------------------------------- drift */
  function renderDrift(d) {
    var pill = $('drift-pill'), chart = $('chart-drift');
    if (d.status === 'insufficient_data') {
      pill.setAttribute('data-state', 'wait'); setText('drift-pill-text', 'Coletando dados (' + nf0.format(d.n) + '/' + nf0.format(d.min_samples) + ')');
      chart.replaceChildren(node('p', 'muted small', 'O indicador aparece quando a janela tem transações suficientes para uma comparação estável.')); renderers.drift = null; $('tbl-drift').replaceChildren(); return;
    }
    pill.setAttribute('data-state', d.status === 'alert' ? 'alert' : 'ok');
    setText('drift-pill-text', d.status === 'alert' ? 'Alerta: ' + d.alerts.length + (d.alerts.length === 1 ? ' deslocamento' : ' deslocamentos') : 'Estável');
    var all = Object.keys(d.features).map(function (k) { return { key: k, label: FEATURE_LABELS[k] || k, v: d.features[k] }; });
    all.push({ key: 'score', label: 'Distribuição do score', v: d.score });
    all.sort(function (a, b) { return b.v - a.v; });
    var top = all.slice(0, 8), maxV = Math.max.apply(null, top.map(function (r) { return r.v; }));
    renderers.drift = function () {
      Viz.hbars(chart, top.map(function (r) {
        return { label: r.label, value: r.v, kind: r.v > d.threshold ? 'alert' : 's1', display: dec(r.v, 2), detailName: 'PSI' };
      }), { ref: { value: d.threshold, label: 'limite ' + dec(d.threshold, 1) }, max: Math.max(d.threshold * 2, Math.min(maxV * 1.1, 1)) });
    };
    renderers.drift();
    fillTable('tbl-drift', ['Feature', 'PSI', 'Situação'], all.map(function (r) { return [r.label, dec(r.v, 3), r.v > d.threshold ? '▲ acima do limite' : 'ok']; }), [1]);
  }

  /* ----------------------------------------------------------------- formulário "Experimente" */
  var formBuilt = false;
  function buildForm(options) {
    if (formBuilt) return; formBuilt = true;
    var city = $('f-city'), br = node('optgroup'), ex = node('optgroup');
    br.label = 'Brasil'; ex.label = 'Exterior';
    options.cities.forEach(function (c) { var o = node('option', '', c.name); o.value = c.name; (c.country === 'BR' ? br : ex).appendChild(o); });
    city.appendChild(br); city.appendChild(ex);
    options.categories.forEach(function (c) { var o = node('option', '', categoryLabel(c)); o.value = c; $('f-category').appendChild(o); });
    applyPreset('normal');
  }
  function applyPreset(name) {
    var p = PRESETS[name]; if (!p) return;
    $('f-amount').value = p.amount; $('f-city').value = p.city; $('f-category').value = p.category; $('f-n1').value = p.n1; $('f-n10').value = p.n10;
    $('f-sec').value = p.sec; $('f-km').value = p.km; $('f-newdev').checked = p.newdev; $('f-newmerch').checked = p.newmerch;
  }
  function num(id, dflt) { var v = parseFloat($(id).value); return isFinite(v) ? v : dflt; }

  function buildRequest() {
    var amount = num('f-amount', NaN);
    if (!(amount > 0)) throw new Error('Informe um valor maior que zero.');
    var city = $('f-city').value, country = (state.model.options.cities.filter(function (c) { return c.name === city; })[0] || {}).country;
    var n1 = Math.max(0, num('f-n1', 0)), n10 = Math.max(n1, num('f-n10', 0)), sec = Math.max(0, num('f-sec', 3600)), km = Math.max(0, num('f-km', 0));
    var speed = Math.min(km / Math.max(sec / 3600, 1 / 60), 5000);
    var z = Math.max(-10, Math.min(10, (Math.log(amount) - Math.log(70)) / 0.6));
    var newdev = $('f-newdev').checked;
    return {
      amount: amount, city: city, merchant_category: $('f-category').value, user_id: 'demo-user',
      device_id: newdev ? 'dev_nunca_visto' : 'dev_habitual', merchant_id: 'm_demo',
      context: {
        is_new_device: newdev ? 1 : 0, is_new_city: city === 'São Paulo' ? 0 : 1, is_new_country: country === 'BR' ? 0 : 1, is_new_merchant: $('f-newmerch').checked ? 1 : 0,
        txn_count_1m: n1, txn_count_10m: n10, txn_count_1h: n10, txn_count_24h: Math.max(n10, 3), txn_count_30d: 120,
        seconds_since_last: sec, km_from_last: km, implied_speed_kmh: speed, amount_zscore_user: z, amount_ratio_to_user_max: Math.min(amount / 300, 50)
      }
    };
  }

  function setStatus(msg, isError) { var s = $('try-status'); s.textContent = msg || ''; s.className = 'status' + (isError ? ' error' : ''); }
  var cooldown = null;
  function startCooldown(seconds) {
    var btn = $('f-submit'), left = seconds; clearInterval(cooldown);
    btn.disabled = true;
    cooldown = setInterval(function () {
      left--; setStatus('Limite de requisições atingido. Tente de novo em ' + left + ' s.', true);
      if (left <= 0) { clearInterval(cooldown); btn.disabled = false; setStatus(''); }
    }, 1000);
    setStatus('Limite de requisições atingido. Tente de novo em ' + left + ' s.', true);
  }

  function submitScore(e) {
    e.preventDefault();
    var body; try { body = buildRequest(); } catch (err) { setStatus(err.message, true); return; }
    var btn = $('f-submit'); btn.disabled = true; setStatus('Calculando…');
    api('score', { method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, body: JSON.stringify(body) }).then(function (r) {
      setStatus(''); btn.disabled = false; renderResult(r);
    }, function (err) {
      if (err.status === 429) { startCooldown(Math.max(err.retryAfter || 30, 1)); return; }
      btn.disabled = false;
      if (err.status === 422) { var d = err.body && err.body.detail; setStatus('Dados inválidos' + (Array.isArray(d) && d[0] ? ': ' + d[0].msg : '.'), true); }
      else setStatus('Não foi possível calcular agora. Tente de novo em instantes.', true);
    });
  }

  function renderResult(r) {
    $('try-result').hidden = false;
    setText('r-score', scoreText(r.score));
    $('r-decision').setAttribute('data-state', r.flagged ? 'alert' : 'ok');
    setText('r-decision-text', r.flagged ? 'Sinalizada como suspeita' : 'Aprovada');
    $('r-fill').style.width = Math.max(1, r.score * 100) + '%';
    $('r-tick').style.left = 'calc(' + (r.threshold * 100) + '% - 1px)';
    $('r-meter').setAttribute('data-flagged', String(r.flagged));
    setText('r-threshold', 'A barra vai de 0 a 1; a marca preta é o limite de decisão (' + dec(r.threshold, 3) + '): score igual ou acima dele é sinalizado. Modelo ' + r.model_version + '.');
    var rows = r.top_features.map(function (f) { return { label: (FEATURE_LABELS[f.feature] || f.feature) + ': ' + fmtFeature(f.feature, f.value), value: f.contribution, note: fmtFeature(f.feature, f.value) }; });
    renderers.contrib = function () { Viz.divergingBars($('chart-contrib'), rows); };
    renderers.contrib();
    var names = Object.keys(r.features);
    fillTable('tbl-features', ['Feature', 'Valor enviado'], names.map(function (n) { return [FEATURE_LABELS[n] || n, fmtFeature(n, r.features[n])]; }));
  }

  /* ----------------------------------------------------------------- inicialização */
  function refreshStats() { return api('stats?window_minutes=15').then(renderStats); }
  function refreshDrift() { return api('drift').then(renderDrift); }

  function init() {
    $('flagged-only').addEventListener('change', function (e) { state.flaggedOnly = e.target.checked; renderFeed(); });
    $('score-form').addEventListener('submit', submitScore);
    $('presets').addEventListener('click', function (e) { var b = e.target.closest('[data-preset]'); if (b) applyPreset(b.getAttribute('data-preset')); });
    document.addEventListener('visibilitychange', function () { if (!document.hidden) { refreshStats().catch(function () {}); } });

    var ro = window.ResizeObserver ? new ResizeObserver(function () {
      requestAnimationFrame(function () { Object.keys(renderers).forEach(function (k) { if (renderers[k]) renderers[k](); }); });
    }) : null;
    if (ro) ['chart-timeline', 'chart-hist', 'chart-models', 'chart-drift', 'chart-contrib'].forEach(function (id) { ro.observe($(id)); });

    var loadModel = function () {
      api('model').then(function (m) {
        renderModel(m);
        return api('transactions?limit=200');
      }).then(function (r) {
        state.feed = r.items; renderFeed();
        connectStream();
        poll(refreshStats, 3000);
        poll(refreshDrift, 15000);
      }).catch(function () { state.conn.api = false; updateConn(); setTimeout(loadModel, 4000); });
    };
    loadModel();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
