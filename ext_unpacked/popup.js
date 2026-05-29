const $ = id => document.getElementById(id);
const API_BASE = 'https://foiembora.up.railway.app';

// ── Utilitários ──────────────────────────────────────────────────────────────

function showScreen(id) {
  ['screen-token', 'screen-wrong', 'screen-main'].forEach(s => {
    const el = document.getElementById(s);
    if (el) el.style.display = 'none';
  });
  const target = document.getElementById(id);
  if (target) target.style.display = 'block';
}

async function validateToken(token) {
  try {
    const res = await fetch(`${API_BASE}/api/token/validate?token=${encodeURIComponent(token)}`);
    if (res.status === 401) return false;
    const data = await res.json();
    return data.valid === true;
  } catch {
    return true; // sem conexao = nao invalida token salvo
  }
}

// Encontra aba do Instagram — 3 métodos em cascata para máxima compatibilidade
async function getInstagramTab() {
  // Método 1: query por URL (Chrome desktop)
  try {
    const tabs = await chrome.tabs.query({ url: '*://www.instagram.com/*' });
    if (tabs && tabs.length > 0) return tabs[0];
  } catch (_) {}

  // Método 2: aba ativa (funciona no Orion iOS)
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tabs && tabs[0] && tabs[0].url && tabs[0].url.includes('instagram.com')) return tabs[0];
  } catch (_) {}

  // Método 3: varrer todas as abas
  try {
    const tabs = await chrome.tabs.query({});
    if (tabs) {
      const ig = tabs.find(t => t.url && t.url.includes('instagram.com'));
      if (ig) return ig;
    }
  } catch (_) {}

  return null;
}

// Ping com timeout — verifica se o content script está vivo
function pingTab(tabId, timeoutMs = 2000) {
  return new Promise(resolve => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    try {
      chrome.tabs.sendMessage(tabId, { action: 'ping' }, (resp) => {
        clearTimeout(timer);
        if (chrome.runtime.lastError) { resolve(false); return; }
        resolve(resp && resp.alive === true);
      });
    } catch (_) {
      clearTimeout(timer);
      resolve(false);
    }
  });
}

// Envia mensagem para o content script — sem executeScript (não existe no iOS)
async function sendToIg(tab, message) {
  // Verifica se o content script está vivo
  const alive = await pingTab(tab.id);
  if (!alive) {
    // No iOS/Orion o content script pode não ter sido injetado ainda — orienta o usuário
    return 'not_alive';
  }
  try {
    chrome.tabs.sendMessage(tab.id, message);
    return true;
  } catch (_) {
    return false;
  }
}

// ── Init ─────────────────────────────────────────────────────────────────────

async function init() {
  try {
    const stored = await chrome.storage.local.get('access_token');
    const savedToken = stored.access_token;

    if (savedToken) {
      const valid = await validateToken(savedToken);
      if (valid) {
        await setupMainScreen();
        return;
      }
      await chrome.storage.local.remove('access_token');
    }
  } catch (_) {}

  showScreen('screen-token');
  setupTokenScreen();
}

// ── Tela de login (email) ─────────────────────────────────────────────────────

function setupTokenScreen() {
  const btn   = $('btn-validate');
  const input = $('email-input');
  const err   = $('token-error');

  const btnSite = $('btn-open-site');
  if (btnSite) {
    btnSite.addEventListener('click', () => {
      try { chrome.tabs.create({ url: `${API_BASE}/entrar` }); } catch (_) {}
    });
  }

  async function doLogin() {
    const email = input.value.trim().toLowerCase();
    if (!email) { input.focus(); return; }
    btn.disabled = true;
    btn.textContent = 'Entrando...';
    err.style.display = 'none';

    try {
      const res = await fetch(`${API_BASE}/api/auth/email`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email })
      });
      const data = await res.json();
      if (data.ok && data.token) {
        await chrome.storage.local.set({ access_token: data.token });
        await setupMainScreen();
      } else {
        err.textContent = data.error || 'Erro ao entrar. Tente novamente.';
        err.style.display = 'block';
        btn.disabled = false;
        btn.textContent = 'Entrar →';
      }
    } catch (_) {
      err.textContent = 'Sem conexao com o servidor. Verifique sua internet.';
      err.style.display = 'block';
      btn.disabled = false;
      btn.textContent = 'Entrar →';
    }
  }

  btn.addEventListener('click', doLogin);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') doLogin(); });
}

// ── Tela principal ────────────────────────────────────────────────────────────

async function setupMainScreen() {
  const igTab = await getInstagramTab();

  if (!igTab) {
    showScreen('screen-wrong');
    const btnIg = $('btn-open-ig');
    if (btnIg) {
      btnIg.addEventListener('click', () => {
        try { chrome.tabs.create({ url: 'https://www.instagram.com/' }); } catch (_) {}
      });
    }
    return;
  }

  showScreen('screen-main');

  $('btn-analyze').addEventListener('click', async () => {
    $('btn-analyze').disabled = true;
    $('btn-analyze').textContent = 'Analisando...';
    $('status-box').classList.add('visible');
    $('error-box').classList.remove('visible');
    $('results-notback').style.display = 'none';
    $('results-unfollowers').style.display = 'none';
    $('results-new').style.display = 'none';
    $('stats-row').style.display = 'none';

    const ok = await sendToIg(igTab, { action: 'analyze' });
    if (!ok) {
      $('status-box').classList.remove('visible');
      $('error-box').classList.add('visible');
      $('error-box').textContent = ok === 'not_alive'
        ? 'Recarregue a pagina do Instagram (puxe para baixo) e tente novamente.'
        : 'Nao foi possivel conectar. Reabra a extensao com o Instagram aberto.';
      $('btn-analyze').disabled = false;
      $('btn-analyze').textContent = 'Tentar novamente';
    }
  });
}

// ── Listas de usuários ────────────────────────────────────────────────────────

function buildUserList(users, containerId, isUnfollower) {
  const container = $(containerId);
  if (!container) return;
  container.innerHTML = '';
  if (!users || users.length === 0) return;

  users.forEach(user => {
    const username        = typeof user === 'string' ? user : user.username;
    const full_name       = user.full_name || null;
    const profile_pic_url = user.profile_pic_url || null;
    const is_verified     = user.is_verified || false;
    const is_private      = user.is_private || false;

    const a = document.createElement('a');
    a.className = 'user-item';
    a.href = `https://www.instagram.com/${username}/`;
    a.target = '_blank';

    const letter = (full_name || username || '?')[0].toUpperCase();
    const colors = ['#833ab4','#fd1d1d','#fcb045','#a855f7','#ec4899','#f97316'];
    const color = colors[letter.charCodeAt(0) % colors.length];
    const fallbackSvg = `data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36"><circle cx="18" cy="18" r="18" fill="${encodeURIComponent(color)}"/><text x="18" y="23" text-anchor="middle" font-size="15" font-weight="bold" fill="white">${letter}</text></svg>`;

    const img = document.createElement('img');
    img.className = 'user-avatar';
    img.referrerPolicy = 'no-referrer';
    img.src = profile_pic_url || fallbackSvg;
    img.onerror = () => { img.src = fallbackSvg; };

    const info = document.createElement('div');
    info.className = 'user-info';
    const displayName = full_name && full_name.toLowerCase() !== username.toLowerCase() ? full_name : null;
    info.innerHTML = `
      <div class="user-username">@${username}${is_verified ? ' <span style="color:#a855f7">&#10003;</span>' : ''}</div>
      ${displayName ? `<div class="user-name">${displayName}</div>` : ''}
    `;

    const badges = document.createElement('div');
    badges.className = 'user-badges';
    if (is_private) {
      const b = document.createElement('span');
      b.className = 'badge-private';
      b.textContent = 'Privado';
      badges.appendChild(b);
    }
    if (isUnfollower) {
      const b = document.createElement('span');
      b.className = 'badge-unfollower';
      b.textContent = 'Saiu';
      badges.appendChild(b);
    }

    a.appendChild(img);
    a.appendChild(info);
    a.appendChild(badges);
    container.appendChild(a);
  });
}

// ── Mensagens do content script (analyze) ────────────────────────────────────

chrome.runtime.onMessage.addListener((msg) => {
  try {
    if (msg.action === 'status') {
      const el = $('status-text');
      if (el) el.textContent = msg.text;
    }

    if (msg.action === 'error') {
      $('status-box').classList.remove('visible');
      $('error-box').classList.add('visible');
      $('error-box').textContent = 'Erro: ' + msg.text;
      const btn = $('btn-analyze');
      if (btn) { btn.disabled = false; btn.textContent = 'Tentar novamente'; }
    }

    if (msg.action === 'result') {
      (async () => {
        $('status-box').classList.remove('visible');

        $('stat-following').textContent = msg.totalFollowing;
        $('stat-followers').textContent = msg.totalFollowers;
        $('stat-notback').textContent   = msg.notFollowingBack.length;
        $('stats-row').style.display    = 'flex';

        const notback = msg.notFollowingBack;
        $('count-notback').textContent = notback.length;
        $('results-notback').style.display = 'block';
        buildUserList(notback, 'list-notback', false);

        const btn = $('btn-analyze');
        if (btn) { btn.disabled = false; btn.textContent = 'Verificar novamente'; }

        // Sync snapshot a partir do POPUP (origem da extensão — funciona no Orion,
        // diferente do content script). Calcula quem saiu / novos seguidores.
        let snap = null;
        try {
          const stored = await chrome.storage.local.get('access_token');
          const token  = stored.access_token;
          if (token && msg.followers && msg.following) {
            const r = await fetch(`${API_BASE}/api/snapshot`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ token, followers: msg.followers, following: msg.following })
            });
            if (r.ok) snap = await r.json();
          }
        } catch (_) {}

        if (snap) {
          const unfollowers = snap.unfollowers || [];
          $('count-unfollowers').textContent = unfollowers.length;
          $('results-unfollowers').style.display = 'block';
          if (unfollowers.length === 0) {
            $('list-unfollowers').innerHTML = '<div class="empty-msg">Ninguem parou de te seguir desde a ultima analise</div>';
          } else {
            buildUserList(unfollowers, 'list-unfollowers', true);
          }

          const newFollowers = snap.new_followers || [];
          $('count-new').textContent = newFollowers.length;
          $('results-new').style.display = 'block';
          if (newFollowers.length === 0) {
            $('list-new').innerHTML = '<div class="empty-msg">Nenhum novo seguidor desde a ultima analise</div>';
          } else {
            buildUserList(newFollowers, 'list-new', false);
          }
        } else {
          $('results-unfollowers').style.display = 'block';
          $('count-unfollowers').textContent = '?';
          $('list-unfollowers').innerHTML = '<div class="empty-msg">Primeira analise salva! Faca a proxima para ver quem saiu.</div>';
        }
      })();
    }
  } catch (_) {}
});

// ── Tabs ──────────────────────────────────────────────────────────────────────

function switchTab(tab) {
  $('panel-meu').style.display  = tab === 'meu' ? 'block' : 'none';
  $('panel-spy').style.display  = tab === 'spy' ? 'block' : 'none';
  $('tab-meu').style.background = tab === 'meu' ? 'linear-gradient(135deg,#833ab4,#fd1d1d,#fcb045)' : 'transparent';
  $('tab-meu').style.color      = tab === 'meu' ? '#fff' : '#71717a';
  $('tab-spy').style.background = tab === 'spy' ? 'linear-gradient(135deg,#833ab4,#fd1d1d,#fcb045)' : 'transparent';
  $('tab-spy').style.color      = tab === 'spy' ? '#fff' : '#71717a';
}

$('tab-meu')?.addEventListener('click', () => switchTab('meu'));
$('tab-spy')?.addEventListener('click', () => switchTab('spy'));

// ── Spy — contagem ────────────────────────────────────────────────────────────

async function runSpy() {
  const igTab = await getInstagramTab();
  if (!igTab) {
    $('spy-error').style.display = 'block';
    $('spy-error').textContent = 'Abra o Instagram no navegador e faca login primeiro.';
    return;
  }

  const input    = $('spy-username');
  const username = input.value.trim().replace(/^@/, '');
  if (!username) { input.focus(); return; }

  $('spy-status').style.display = 'block';
  $('spy-result').style.display = 'none';
  $('spy-error').style.display  = 'none';
  $('spy-status-text').textContent = `Buscando @${username}...`;
  $('btn-spy').disabled = true;

  const ok = await sendToIg(igTab, { action: 'spy', username });
  if (!ok) {
    $('spy-status').style.display = 'none';
    $('btn-spy').disabled = false;
    $('spy-error').style.display = 'block';
    $('spy-error').textContent = ok === 'not_alive'
      ? 'Recarregue a pagina do Instagram e tente novamente.'
      : 'Nao foi possivel conectar. Abra o Instagram e tente novamente.';
  }
}

$('btn-spy')?.addEventListener('click', runSpy);

chrome.runtime.onMessage.addListener((msg) => {
  try {
    if (msg.action === 'status' && $('spy-status')?.style.display !== 'none') {
      $('spy-status-text').textContent = msg.text;
    }

    if (msg.action === 'spy_result') {
      (async () => {
        $('spy-status').style.display = 'none';
        $('btn-spy').disabled = false;

        const info = msg.info;
        $('spy-name').textContent          = info.full_name || info.username;
        $('spy-handle').textContent        = '@' + info.username;
        $('spy-photo').src                 = info.profile_pic_url || '';
        $('spy-followers-num').textContent = Number(info.followers).toLocaleString('pt-BR');
        $('spy-following-num').textContent = Number(info.following).toLocaleString('pt-BR');
        $('spy-result').style.display      = 'block';

        // Salva no servidor a partir do POPUP (origem da extensão — funciona no Orion,
        // diferente do content script em instagram.com). Retorna o diff.
        let backend = null;
        try {
          const stored = await chrome.storage.local.get('access_token');
          const token  = stored.access_token;
          if (token) {
            const r = await fetch(`${API_BASE}/api/spy/update`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ token, ...info })
            });
            if (r.ok) backend = await r.json();
          }
        } catch (_) {}

        const diffEl = $('spy-diff-num');
        const diff = backend?.diff;
        if (diff !== null && diff !== undefined) {
          diffEl.textContent = (diff >= 0 ? '+' : '') + diff.toLocaleString('pt-BR');
          diffEl.style.background = diff > 0
            ? 'linear-gradient(135deg,#22c55e,#4ade80)'
            : diff < 0 ? 'linear-gradient(135deg,#ef4444,#f87171)'
            : 'linear-gradient(135deg,#a855f7,#ec4899)';
          diffEl.style.webkitBackgroundClip = 'text';
          diffEl.style.webkitTextFillColor  = 'transparent';
          diffEl.style.backgroundClip       = 'text';
        } else {
          diffEl.textContent = '—';
        }

        $('spy-history-msg').textContent = !backend
          ? 'Nao foi possivel salvar no servidor. Verifique sua conexao/login.'
          : backend.is_new
            ? 'Primeira verificacao salva! Volte amanha para ver mudancas.'
            : `Comparado com ${backend.prev_followers?.toLocaleString('pt-BR')} seguidores anteriores`;
      })();
    }

    if (msg.action === 'error' && $('spy-status')?.style.display !== 'none') {
      $('spy-status').style.display = 'none';
      $('btn-spy').disabled = false;
      $('spy-error').style.display = 'block';
      $('spy-error').textContent = msg.text;
    }
  } catch (_) {}
});

$('spy-username')?.addEventListener('keydown', e => { if (e.key === 'Enter') runSpy(); });
$('btn-spy-refresh')?.addEventListener('click', runSpy);

// ── Spy — auditoria de seguidores ─────────────────────────────────────────────

async function runSpyFollowers() {
  const igTab = await getInstagramTab();
  if (!igTab) {
    $('spy-fl-error').style.display = 'block';
    $('spy-fl-error').textContent = 'Abra o Instagram no navegador e faca login primeiro.';
    return;
  }

  const input    = $('spy-username');
  const username = input.value.trim().replace(/^@/, '');
  if (!username) { input.focus(); return; }

  $('spy-fl-status').style.display      = 'block';
  $('spy-fl-result').style.display      = 'none';
  $('spy-fl-error').style.display       = 'none';
  $('spy-fl-left-section').style.display   = 'none';
  $('spy-fl-joined-section').style.display = 'none';
  $('spy-fl-status-text').textContent   = `Carregando seguidores de @${username}...`;
  $('btn-spy-followers').disabled       = true;

  const ok = await sendToIg(igTab, { action: 'spy_followers', username });
  if (!ok) {
    $('spy-fl-status').style.display = 'none';
    $('btn-spy-followers').disabled = false;
    $('spy-fl-error').style.display = 'block';
    $('spy-fl-error').textContent = ok === 'not_alive'
      ? 'Recarregue a pagina do Instagram e tente novamente.'
      : 'Nao foi possivel conectar. Abra o Instagram e tente novamente.';
  }
}

$('btn-spy-followers')?.addEventListener('click', runSpyFollowers);

chrome.runtime.onMessage.addListener((msg) => {
  try {
    if (msg.action === 'spy_fl_status') {
      const el = $('spy-fl-status-text');
      if (el) el.textContent = msg.text;
    }

    if (msg.action === 'spy_followers_result') {
      (async () => {
        $('btn-spy-followers').disabled = false;

        // Salva o snapshot da lista pelo POPUP (origem da extensão — funciona no Orion).
        let result = null;
        try {
          const stored = await chrome.storage.local.get('access_token');
          const token  = stored.access_token;
          if (token) {
            $('spy-fl-status-text').textContent = 'Salvando snapshot no servidor...';
            const r = await fetch(`${API_BASE}/api/spy/follower_snapshot`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ token, username: msg.username, followers: msg.followers || [] })
            });
            if (r.ok) result = await r.json();
          }
        } catch (_) {}

        $('spy-fl-status').style.display = 'none';

        const total      = result?.total ?? (msg.followers?.length || 0);
        const prev_total = result?.prev_total ?? 0;
        const is_new     = result?.is_new ?? true;
        const joined     = result?.joined ?? [];
        const left       = result?.left ?? [];

        $('spy-fl-left-count').textContent   = left.length;
        $('spy-fl-joined-count').textContent = joined.length;
        $('spy-fl-msg').textContent = !result
          ? 'Nao foi possivel salvar no servidor. Verifique sua conexao/login.'
          : is_new
            ? `Primeiro snapshot salvo com ${total.toLocaleString('pt-BR')} seguidores. Audite novamente para ver mudancas.`
            : `Comparado com snapshot anterior (${prev_total.toLocaleString('pt-BR')} seguidores)`;

        $('spy-fl-result').style.display = 'block';

        if (left.length > 0) {
          $('spy-fl-left-section').style.display = 'block';
          const leftList = $('spy-fl-left-list');
          leftList.innerHTML = '';
          left.slice(0, 50).forEach(u => {
            const a = document.createElement('a');
            a.className = 'user-item';
            a.href = `https://www.instagram.com/${u}/`;
            a.target = '_blank';
            a.innerHTML = `<div class="user-info"><div class="user-username">@${u}</div></div><div class="user-badges"><span class="badge-unfollower">Saiu</span></div>`;
            leftList.appendChild(a);
          });
        }

        if (joined.length > 0) {
          $('spy-fl-joined-section').style.display = 'block';
          const joinedList = $('spy-fl-joined-list');
          joinedList.innerHTML = '';
          joined.slice(0, 50).forEach(u => {
            const a = document.createElement('a');
            a.className = 'user-item';
            a.href = `https://www.instagram.com/${u}/`;
            a.target = '_blank';
            a.innerHTML = `<div class="user-info"><div class="user-username">@${u}</div></div><div class="user-badges"><span class="badge-new">Novo</span></div>`;
            joinedList.appendChild(a);
          });
        }
      })();
    }

    if (msg.action === 'spy_fl_error') {
      $('spy-fl-status').style.display = 'none';
      $('btn-spy-followers').disabled  = false;
      $('spy-fl-error').style.display  = 'block';
      $('spy-fl-error').textContent    = msg.text;
    }
  } catch (_) {}
});

init();
