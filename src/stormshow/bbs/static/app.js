(function () {
  var statusEl = document.getElementById('status');
  var whoEl = document.getElementById('who');
  var buttons = Array.prototype.slice.call(document.querySelectorAll('button[data-cmd]'));
  var timer = null;

  function setStatus(kind, text) { statusEl.className = kind || ''; statusEl.textContent = text; }
  function lock(on) { buttons.forEach(function (b) { b.disabled = on; }); }

  function countdown(secs, text) {
    clearInterval(timer);
    var left = secs;
    lock(true);
    setStatus('cooldown', text);
    timer = setInterval(function () {
      left -= 1;
      if (left <= 0) { clearInterval(timer); lock(false); setStatus('', 'Ready. Choose your curse.'); }
      else { setStatus('cooldown', 'The clouds are recharging. Try again in ' + left + ' s.'); }
    }, 1000);
  }

  function refreshWho() {
    fetch('/api/status', { cache: 'no-store' }).then(function (r) { return r.json(); })
      .then(function (d) { whoEl.textContent = d.who || ''; }).catch(function () {});
  }

  function summon(cmd) {
    lock(true);
    setStatus('', 'Calling the storm...');
    fetch('/api/summon', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd }) })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.status === 'ok') {
          document.body.classList.remove('flash'); void document.body.offsetWidth;
          document.body.classList.add('flash');
          if (navigator.vibrate) { navigator.vibrate([40, 60, 120]); }
        }
        if (d.retry_after) { countdown(d.retry_after, d.message); }
        else { setStatus(d.status, d.message); lock(false); }
        refreshWho();
      })
      .catch(function () { setStatus('error', "The spirits didn't answer. Try again."); lock(false); });
  }

  buttons.forEach(function (b) { b.addEventListener('click', function () { summon(b.getAttribute('data-cmd')); }); });
  refreshWho();
})();
