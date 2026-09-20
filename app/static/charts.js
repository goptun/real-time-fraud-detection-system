/* Gráficos em SVG puro. Sem bibliotecas, sem CDN, sem estilo inline (CSP do domínio).
   Todo texto entra por textContent (os rótulos vêm da API: tratados como dados, não HTML).
   Cores/estilos vêm de classes CSS (.s1 .s2 .deemph .pos .neg .alert), então claro/escuro é só CSS. */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var nf = new Intl.NumberFormat('pt-BR');

  function el(name, attrs, text) {
    var n = document.createElementNS(NS, name);
    for (var k in attrs || {}) n.setAttribute(k, attrs[k]);
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function compact(v) {
    if (v >= 1e6) return (v / 1e6).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + ' mi';
    if (v >= 1e3) return (v / 1e3).toLocaleString('pt-BR', { maximumFractionDigits: 1 }) + ' mil';
    return nf.format(v);
  }

  function niceMax(v) {
    if (v <= 1) return 1;
    var exp = Math.pow(10, Math.floor(Math.log10(v)));
    var f = v / exp;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp;
  }

  /* ------------------------------------------------------------- tooltip único */
  var tip = null;
  function tooltipEl() { return tip || (tip = document.getElementById('tooltip')); }

  /** rows: [{key:'s1'|'s2'|'', name, value}] ; title: string */
  function showTip(title, rows, anchor) {
    var t = tooltipEl();
    if (!t) return;
    t.replaceChildren();
    if (title) t.appendChild(elHtml('div', 't-title', title));
    rows.forEach(function (r) {
      var row = elHtml('div', 't-row');
      row.appendChild(elHtml('span', 't-key ' + (r.key || '')));
      row.appendChild(elHtml('span', 't-name', r.name));
      row.appendChild(elHtml('span', 't-val', r.value));
      t.appendChild(row);
    });
    t.hidden = false;
    var w = t.offsetWidth, h = t.offsetHeight;
    var x = Math.min(Math.max(8, anchor.x + 14), window.innerWidth - w - 8);
    var y = anchor.y - h - 12;
    if (y < 8) y = anchor.y + 18;
    t.style.left = x + 'px'; // CSSOM: permitido pela CSP (bloqueado é só o atributo style="")
    t.style.top = y + 'px';
  }
  function hideTip() { var t = tooltipEl(); if (t) t.hidden = true; }
  function elHtml(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  /** Liga hover/foco a um retângulo de captura (maior que a marca). */
  function bindHit(rect, title, rows, label) {
    rect.setAttribute('class', 'hit');
    rect.setAttribute('tabindex', '0');
    rect.setAttribute('aria-label', label || (title + ': ' + rows.map(function (r) { return r.name + ' ' + r.value; }).join(', ')));
    rect.addEventListener('pointermove', function (e) { showTip(title, rows, { x: e.clientX, y: e.clientY }); });
    rect.addEventListener('pointerleave', hideTip);
    rect.addEventListener('focus', function () {
      var b = rect.getBoundingClientRect();
      showTip(title, rows, { x: b.left + b.width / 2, y: b.top });
    });
    rect.addEventListener('blur', hideTip);
  }

  function roundedTop(x, y, w, h, r) { // 4px arredondado no topo, reto na base
    r = Math.max(0, Math.min(r, w / 2, h));
    return 'M' + x + ',' + (y + h) + 'V' + (y + r) + 'Q' + x + ',' + y + ' ' + (x + r) + ',' + y +
      'H' + (x + w - r) + 'Q' + (x + w) + ',' + y + ' ' + (x + w) + ',' + (y + r) + 'V' + (y + h) + 'Z';
  }
  function roundedRight(x, y, w, h, r) { // arredondado na ponta de dados (direita), reto na base (esquerda)
    r = Math.max(0, Math.min(r, h / 2, w));
    return 'M' + x + ',' + y + 'H' + (x + w - r) + 'Q' + (x + w) + ',' + y + ' ' + (x + w) + ',' + (y + r) +
      'V' + (y + h - r) + 'Q' + (x + w) + ',' + (y + h) + ' ' + (x + w - r) + ',' + (y + h) + 'H' + x + 'Z';
  }

  function baseSvg(host, W, H, label) {
    host.replaceChildren();
    var svg = el('svg', { width: W, height: H, viewBox: '0 0 ' + W + ' ' + H, 'aria-hidden': 'true', focusable: 'false' });
    host.appendChild(svg);
    return svg;
  }
  function widthOf(host) { return Math.max(Math.floor(host.clientWidth), 240); }

  /* ------------------------------------------------------------- colunas empilhadas */
  /** data: [{label, title, values:[n, n]}] ; series: [{name, cls}] (base primeiro) */
  function stackedColumns(host, data, series, opts) {
    opts = opts || {};
    var W = widthOf(host), H = opts.height || 210, m = { l: 42, r: 6, t: 8, b: 26 };
    var pw = W - m.l - m.r, ph = H - m.t - m.b;
    var svg = baseSvg(host, W, H);
    var totals = data.map(function (d) { return d.values.reduce(function (a, b) { return a + b; }, 0); });
    var max = niceMax(Math.max(1, Math.max.apply(null, totals)));
    var ticks = max >= 2 && max % 2 === 0 ? [0, max / 2, max] : [0, max];
    var base = m.t + ph;
    ticks.forEach(function (tv) {
      var y = base - (tv / max) * ph;
      svg.appendChild(el('line', { x1: m.l, x2: W - m.r, y1: y, y2: y, class: tv === 0 ? 'axis' : 'grid' }));
      svg.appendChild(el('text', { x: m.l - 6, y: y + 4, 'text-anchor': 'end', class: 'tick' }, compact(tv)));
    });
    var n = data.length, band = pw / Math.max(n, 1), bw = Math.min(24, Math.max(band - 4, 2));
    var every = Math.max(1, Math.ceil(52 / band));
    data.forEach(function (d, i) {
      var x0 = m.l + i * band + (band - bw) / 2, cum = 0, lastVisible = -1;
      d.values.forEach(function (v, k) { if (v > 0) lastVisible = k; });
      d.values.forEach(function (v, k) {
        if (v <= 0) return;
        var h = (v / max) * ph, y = base - cum - h;
        var drawH = k > 0 ? Math.max(h - 2, 1) : h; // 2px de "surface gap" entre segmentos
        var top = k === lastVisible;
        var cls = series[k].cls;
        svg.appendChild(top ? el('path', { d: roundedTop(x0, y, bw, drawH, 4), class: cls }) : el('rect', { x: x0, y: y, width: bw, height: drawH, class: cls }));
        cum += h;
      });
      if (i % every === 0) svg.appendChild(el('text', { x: x0 + bw / 2, y: H - 8, 'text-anchor': 'middle', class: 'tick' }, d.label));
      var hit = el('rect', { x: m.l + i * band, y: m.t, width: band, height: ph });
      var rows = d.values.map(function (v, k) { return { key: series[k].cls, name: series[k].name, value: nf.format(v) }; });
      rows.push({ key: '', name: 'Total', value: nf.format(totals[i]) });
      bindHit(hit, d.title || d.label, rows);
      svg.appendChild(hit);
    });
    return svg;
  }

  /* ------------------------------------------------------------- histograma (escala log) */
  /** counts: 10 bins em [0,1]; threshold: linha de decisão */
  function histogramLog(host, counts, threshold, opts) {
    opts = opts || {};
    var W = widthOf(host), H = opts.height || 210, m = { l: 42, r: 8, t: 22, b: 30 };
    var pw = W - m.l - m.r, ph = H - m.t - m.b, base = m.t + ph;
    var svg = baseSvg(host, W, H);
    var ly = function (c) { return Math.log10(1 + c); };
    var maxc = Math.max(1, Math.max.apply(null, counts));
    var decades = Math.max(1, Math.ceil(ly(maxc)));
    var ticks = [0]; for (var d = 1; d <= decades; d++) ticks.push(Math.pow(10, d)); // sem o tick '1': ficaria colado no 0 (log10(1+1) = 0,3)
    ticks.forEach(function (tv) {
      if (ly(tv) > decades + 1e-9) return;
      var y = base - (ly(tv) / decades) * ph;
      svg.appendChild(el('line', { x1: m.l, x2: W - m.r, y1: y, y2: y, class: tv === 0 ? 'axis' : 'grid' }));
      svg.appendChild(el('text', { x: m.l - 6, y: y + 4, 'text-anchor': 'end', class: 'tick' }, compact(tv)));
    });
    var band = pw / counts.length, bw = Math.min(24, band - 4);
    counts.forEach(function (c, i) {
      var x0 = m.l + i * band + (band - bw) / 2, h = (ly(c) / decades) * ph;
      if (h > 0) svg.appendChild(el('path', { d: roundedTop(x0, base - h, bw, h, 4), class: 's1' }));
      var hit = el('rect', { x: m.l + i * band, y: m.t, width: band, height: ph });
      var lo = (i / counts.length).toLocaleString('pt-BR', { minimumFractionDigits: 1 }), hi = ((i + 1) / counts.length).toLocaleString('pt-BR', { minimumFractionDigits: 1 });
      bindHit(hit, 'Score ' + lo + ' – ' + hi, [{ key: 's1', name: 'Transações', value: nf.format(c) }]);
      svg.appendChild(hit);
    });
    [0, 0.2, 0.4, 0.6, 0.8, 1].forEach(function (v) {
      svg.appendChild(el('text', { x: m.l + v * pw, y: H - 10, 'text-anchor': v === 0 ? 'start' : v === 1 ? 'end' : 'middle', class: 'tick' }, v.toLocaleString('pt-BR')));
    });
    if (typeof threshold === 'number') {
      var tx = m.l + threshold * pw;
      svg.appendChild(el('line', { x1: tx, x2: tx, y1: m.t - 4, y2: base, class: 'ref' }));
      var right = tx > W * 0.6;
      svg.appendChild(el('text', { x: right ? tx - 5 : tx + 5, y: m.t + 6, 'text-anchor': right ? 'end' : 'start', class: 'note' },
        'limite de decisão ' + threshold.toLocaleString('pt-BR', { maximumFractionDigits: 2 })));
    }
    return svg;
  }

  /* ------------------------------------------------------------- barras horizontais (rótulo acima da barra) */
  /** rows: [{label, value, kind:'s1'|'deemph'|'alert', display, detail}] ; ref: {value, label} */
  function hbars(host, rows, opts) {
    opts = opts || {};
    var W = widthOf(host), rowH = 40, m = { l: 0, r: 64, t: opts.ref ? 20 : 4, b: 4 };
    var H = m.t + rows.length * rowH + m.b, pw = W - m.l - m.r;
    var svg = baseSvg(host, W, H);
    var dataMax = Math.max.apply(null, rows.map(function (r) { return r.value; }).concat([0]));
    var max = opts.max || Math.max(dataMax * 1.1, opts.ref ? opts.ref.value * 1.6 : 0, 0.0001);
    if (opts.ref) {
      var rx = m.l + (opts.ref.value / max) * pw;
      svg.appendChild(el('line', { x1: rx, x2: rx, y1: m.t - 4, y2: H - m.b, class: 'ref' }));
      var right = rx > W * 0.7;
      svg.appendChild(el('text', { x: right ? rx - 5 : rx + 5, y: 10, 'text-anchor': right ? 'end' : 'start', class: 'note' }, opts.ref.label));
    }
    svg.appendChild(el('line', { x1: m.l, x2: m.l, y1: m.t, y2: H - m.b, class: 'axis' }));
    rows.forEach(function (r, i) {
      var y = m.t + i * rowH;
      svg.appendChild(el('text', { x: m.l, y: y + 13, class: 'lbl' }, r.label));
      var w = Math.max(Math.min(r.value / max, 1) * pw, r.value > 0 ? 2 : 0), by = y + 20, bh = 10;
      if (w > 0) svg.appendChild(el('path', { d: roundedRight(m.l, by, w, bh, 4), class: r.kind || 's1' }));
      svg.appendChild(el('text', { x: m.l + w + 6, y: by + 9, class: 'val' }, (r.display || String(r.value)) + (r.kind === 'alert' ? ' ▲' : '')));
      var hit = el('rect', { x: m.l, y: y, width: W - m.l, height: rowH });
      bindHit(hit, r.label, [{ key: r.kind === 'alert' ? 's2' : r.kind === 'deemph' ? '' : 's1', name: r.detailName || 'Valor', value: r.display || String(r.value) }].concat(r.extra || []));
      svg.appendChild(hit);
    });
    return svg;
  }

  /* ------------------------------------------------------------- barras divergentes (contribuições) */
  /** rows: [{label, value(contribution), note}] ; positivo → aumenta o risco */
  function divergingBars(host, rows) {
    var W = widthOf(host), rowH = 44, m = { l: 8, r: 8, t: 4, b: 22 };
    var H = m.t + rows.length * rowH + m.b, pw = W - m.l - m.r, mid = m.l + pw / 2;
    var svg = baseSvg(host, W, H);
    var maxAbs = Math.max(0.5, Math.max.apply(null, rows.map(function (r) { return Math.abs(r.value); })));
    var half = pw / 2 - 30;
    svg.appendChild(el('line', { x1: mid, x2: mid, y1: m.t, y2: H - m.b, class: 'axis' }));
    rows.forEach(function (r, i) {
      var y = m.t + i * rowH, pos = r.value >= 0, w = Math.max((Math.abs(r.value) / maxAbs) * half, 2);
      svg.appendChild(el('text', { x: pos ? m.l : W - m.r, y: y + 13, 'text-anchor': pos ? 'start' : 'end', class: 'lbl' }, r.label));
      var by = y + 21, bh = 10;
      var d = pos ? roundedRight(mid, by, w, bh, 4) : 'M' + mid + ',' + by + 'H' + (mid - w + 4) + 'Q' + (mid - w) + ',' + by + ' ' + (mid - w) + ',' + (by + 4) + 'V' + (by + bh - 4) + 'Q' + (mid - w) + ',' + (by + bh) + ' ' + (mid - w + 4) + ',' + (by + bh) + 'H' + mid + 'Z';
      svg.appendChild(el('path', { d: d, class: pos ? 'pos' : 'neg' }));
      var txt = (pos ? '+' : '−') + Math.abs(r.value).toLocaleString('pt-BR', { maximumFractionDigits: 2 });
      svg.appendChild(el('text', { x: pos ? mid + w + 6 : mid - w - 6, y: by + 9, 'text-anchor': pos ? 'start' : 'end', class: 'val' }, txt));
      var hit = el('rect', { x: m.l, y: y, width: pw, height: rowH });
      bindHit(hit, r.label, [{ key: pos ? 's2' : 's1', name: pos ? 'Aumenta o risco' : 'Reduz o risco', value: txt }, { key: '', name: 'Valor', value: r.note || '' }]);
      svg.appendChild(hit);
    });
    svg.appendChild(el('text', { x: m.l, y: H - 6, class: 'note' }, '← reduz o risco'));
    svg.appendChild(el('text', { x: W - m.r, y: H - 6, 'text-anchor': 'end', class: 'note' }, 'aumenta o risco →'));
    return svg;
  }

  window.Viz = { stackedColumns: stackedColumns, histogramLog: histogramLog, hbars: hbars, divergingBars: divergingBars, hideTip: hideTip, compact: compact };
})();
