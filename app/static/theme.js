/* Tema claro/escuro. Mesma chave/atributo do site principal (localStorage 'theme' + data-theme),
   então a escolha do visitante vale nos dois. Carregado no <head> para evitar flash de tema. */
(function () {
  var root = document.documentElement;
  try {
    var stored = localStorage.getItem('theme');
    if (stored === 'light' || stored === 'dark') root.setAttribute('data-theme', stored);
  } catch (e) {}

  function current() {
    var explicit = root.getAttribute('data-theme');
    if (explicit === 'light' || explicit === 'dark') return explicit;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }

  document.addEventListener('DOMContentLoaded', function () {
    var button = document.getElementById('theme-toggle');
    if (!button) return;
    var transitionTimer;
    button.addEventListener('click', function () {
      var next = current() === 'dark' ? 'light' : 'dark';
      // Cross-fade curto das cores (ver .theme-transition em style.css).
      root.classList.add('theme-transition');
      clearTimeout(transitionTimer);
      transitionTimer = setTimeout(function () { root.classList.remove('theme-transition'); }, 200);
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('theme', next); } catch (e) {}
      document.dispatchEvent(new CustomEvent('themechange'));
    });
  });
})();
