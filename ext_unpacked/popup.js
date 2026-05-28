const $ = id => document.getElementById(id);

const API_BASE = 'https://foiembora.up.railway.app';

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
    return true;
  }
}

async function getInstagramTab() {
  const tabs = await chrome.tabs.query({ url: 'https://www.instagram.com/*' });
  return tabs.length > 0 ? tabs[0] : null;
}

async function init() {
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

  showScreen('screen-token');
  setupTokenScreen();
}

function setupTokenScreen() {
  const btn = $('btn-validate');
  const input = $('token-input');
  const err = $('token-error');

  // Botao para abrir o site (auto-login)
  const btnSite = $('btn-open-site');
  if (btnSite) {
    btnSite.addEventListener('click', () => {
      chrome.tabs.create({ url: `${API_BASE}/entrar` });
    });
  }

  btn.addEventListener('click', async () => {
    const token = input.value.trim();
    if (!token) return;
    btn.disabled = true;
    btn.textContent = 'Validando...';
    err.style.display = 'none';

    const valid = await validateToken(token);
    if (valid) {
      await chrome.storage.local.set({ access_token: token });
      await setupMainScreen();
    } else {
      err.textContent = 'Codigo invalido ou expirado. Verifique e tente novamente.';
      err.style.display = 'block';
      btn.disabled = false;
      btn.textContent = 'Ativar acesso';
    }
  });

  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') btn.click();
  });
}

async function setupMainScreen() {
  const igTab = await getInstagramTab();

  if (!igTab) {
    showScreen('screen-wrong');
    $('btn-open-ig').addEventListener('click', () => {
      chrome.tabs.create({ url: 'https://www.instagram.com/' });
    });
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

    try {
      await chrome.scripting.executeScript({
        target: { tabId: igTab.id },
        files: ['content.js']
      });
    } catch (_) {}

    chrome.tabs.sendMessage(igTab.id, { action: 'analyze' });
  });
}

function buildUserList(users, containerId, isUnfollower) {
  const container = $(containerId);
  container.innerHTML = '';
  if (users.length === 0) return;

  users.forEach(user => {
    const username = typeof user === 'string' ? user : user.username;
    const full_name = user.full_name || null;
    const profile_pic_url = user.profile_pic_url || null;
    const is_verified = user.is_verified || false;
    const is_private = user.is_private || false;

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
    img.crossOrigin = 'anonymous';
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

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action === 'status') {
    $('status-text').textContent = msg.text;
  }

  if (msg.action === 'error') {
    $('status-box').classList.remove('visible');
    $('error-box').classList.add('visible');
    $('error-box').textContent = 'Erro: ' + msg.text;
    $('btn-analyze').disabled = false;
    $('btn-analyze').textContent = 'Tentar novamente';
  }

  if (msg.action === 'result') {
    $('status-box').classList.remove('visible');

    // Stats
    $('stat-following').textContent = msg.totalFollowing;
    $('stat-followers').textContent = msg.totalFollowers;
    $('stat-notback').textContent = msg.notFollowingBack.length;
    $('stats-row').style.display = 'flex';

    // Secao 1: Nao seguem de volta (calculado localmente, sempre disponivel)
    const notback = msg.notFollowingBack;
    $('count-notback').textContent = notback.length;
    $('results-notback').style.display = 'block';
    buildUserList(notback, 'list-notback', false);

    // Secao 2: Pararam de te seguir (do backend, historico)
    const unfollowers = msg.unfollowers || [];
    if (msg.snapshotOk && unfollowers.length >= 0) {
      $('count-unfollowers').textContent = unfollowers.length;
      $('results-unfollowers').style.display = 'block';
      if (unfollowers.length === 0) {
        $('list-unfollowers').innerHTML = '<div class="empty-msg">Ninguem parou de te seguir desde a ultima analise</div>';
      } else {
        buildUserList(unfollowers, 'list-unfollowers', true);
      }
    } else if (!msg.snapshotOk) {
      $('results-unfollowers').style.display = 'block';
      $('count-unfollowers').textContent = '?';
      $('list-unfollowers').innerHTML = '<div class="empty-msg">Primeira analise salva! Fa a proxima para ver quem saiu.</div>';
    }

    // Secao 3: Novos seguidores (do backend)
    const newFollowers = msg.new_followers || [];
    if (msg.snapshotOk && newFollowers.length >= 0) {
      $('count-new').textContent = newFollowers.length;
      $('results-new').style.display = 'block';
      if (newFollowers.length === 0) {
        $('list-new').innerHTML = '<div class="empty-msg">Nenhum novo seguidor desde a ultima analise</div>';
      } else {
        buildUserList(newFollowers, 'list-new', false);
      }
    }

    $('btn-analyze').disabled = false;
    $('btn-analyze').textContent = 'Verificar novamente';
  }
});

// ── Tab switching ──
function switchTab(tab) {
  $('panel-meu').style.display  = tab === 'meu'  ? 'block' : 'none';
  $('panel-spy').style.display  = tab === 'spy'  ? 'block' : 'none';
  $('tab-meu').style.background = tab === 'meu'
    ? 'linear-gradient(135deg,#833ab4,#fd1d1d,#fcb045)' : 'transparent';
  $('tab-meu').style.color = tab === 'meu' ? '#fff' : '#71717a';
  $('tab-spy').style.background = tab === 'spy'
    ? 'linear-gradient(135deg,#833ab4,#fd1d1d,#fcb045)' : 'transparent';
  $('tab-spy').style.color = tab === 'spy' ? '#fff' : '#71717a';
}

$('tab-meu')?.addEventListener('click', () => switchTab('meu'));
$('tab-spy')?.addEventListener('click', () => switchTab('spy'));

// ── Spy mode ──
async function runSpy() {
  const igTab = await getInstagramTab();
  if (!igTab) {
    $('spy-error').style.display = 'block';
    $('spy-error').textContent = 'Abra o Instagram no navegador e faca login primeiro.';
    return;
  }

  const input = $('spy-username');
  const username = input.value.trim().replace(/^@/, '');
  if (!username) { input.focus(); return; }

  $('spy-status').style.display = 'block';
  $('spy-result').style.display = 'none';
  $('spy-error').style.display  = 'none';
  $('spy-status-text').textContent = `Buscando @${username}...`;
  $('btn-spy').disabled = true;

  try {
    await chrome.scripting.executeScript({ target: { tabId: igTab.id }, files: ['content.js'] });
  } catch (_) {}

  chrome.tabs.sendMessage(igTab.id, { action: 'spy', username });
}

$('btn-spy')?.addEventListener('click', runSpy);

// Listener para spy_result
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action === 'status' && $('spy-status')?.style.display !== 'none') {
    $('spy-status-text').textContent = msg.text;
  }

  if (msg.action === 'spy_result') {
    $('spy-status').style.display = 'none';
    $('btn-spy').disabled = false;

    const info = msg.info;
    $('spy-name').textContent    = info.full_name || info.username;
    $('spy-handle').textContent  = '@' + info.username;
    $('spy-photo').src           = info.profile_pic_url || '';
    $('spy-followers-num').textContent = Number(info.followers).toLocaleString('pt-BR');
    $('spy-following-num').textContent = Number(info.following).toLocaleString('pt-BR');

    const diffEl = $('spy-diff-num');
    if (msg.diff !== null && msg.diff !== undefined) {
      const d = msg.diff;
      diffEl.textContent = (d >= 0 ? '+' : '') + d.toLocaleString('pt-BR');
      diffEl.style.background = d > 0
        ? 'linear-gradient(135deg,#22c55e,#4ade80)'
        : d < 0 ? 'linear-gradient(135deg,#ef4444,#f87171)'
        : 'linear-gradient(135deg,#a855f7,#ec4899)';
      diffEl.style.webkitBackgroundClip = 'text';
      diffEl.style.webkitTextFillColor  = 'transparent';
      diffEl.style.backgroundClip = 'text';
    } else {
      diffEl.textContent = '—';
    }

    $('spy-history-msg').textContent = msg.is_new
      ? 'Primeira verificacao salva! Volte amanha para ver mudancas.'
      : `Comparado com ${msg.prev_followers?.toLocaleString('pt-BR')} seguidores anteriores`;

    $('spy-result').style.display = 'block';
  }

  if (msg.action === 'error' && $('spy-status')?.style.display !== 'none') {
    $('spy-status').style.display = 'none';
    $('btn-spy').disabled = false;
    $('spy-error').style.display = 'block';
    $('spy-error').textContent = msg.text;
  }
});

$('spy-username')?.addEventListener('keydown', e => { if (e.key === 'Enter') runSpy(); });
$('btn-spy-refresh')?.addEventListener('click', runSpy);

// ── Spy Followers Audit ──
async function runSpyFollowers() {
  const igTab = await getInstagramTab();
  if (!igTab) {
    $('spy-fl-error').style.display = 'block';
    $('spy-fl-error').textContent = 'Abra o Instagram no navegador e faca login primeiro.';
    return;
  }

  const input = $('spy-username');
  const username = input.value.trim().replace(/^@/, '');
  if (!username) { input.focus(); return; }

  $('spy-fl-status').style.display = 'block';
  $('spy-fl-result').style.display = 'none';
  $('spy-fl-error').style.display  = 'none';
  $('spy-fl-left-section').style.display = 'none';
  $('spy-fl-joined-section').style.display = 'none';
  $('spy-fl-status-text').textContent = `Carregando seguidores de @${username}...`;
  $('btn-spy-followers').disabled = true;

  try {
    await chrome.scripting.executeScript({ target: { tabId: igTab.id }, files: ['content.js'] });
  } catch (_) {}

  chrome.tabs.sendMessage(igTab.id, { action: 'spy_followers', username });
}

$('btn-spy-followers')?.addEventListener('click', runSpyFollowers);

// Listener spy_followers_result
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action === 'spy_fl_status') {
    $('spy-fl-status-text').textContent = msg.text;
  }

  if (msg.action === 'spy_followers_result') {
    $('spy-fl-status').style.display = 'none';
    $('btn-spy-followers').disabled = false;

    $('spy-fl-left-count').textContent  = msg.left?.length || 0;
    $('spy-fl-joined-count').textContent = msg.joined?.length || 0;
    $('spy-fl-msg').textContent = msg.is_new
      ? `Primeiro snapshot salvo com ${(msg.total || 0).toLocaleString('pt-BR')} seguidores. Audite novamente para ver mudancas.`
      : `Comparado com snapshot anterior (${(msg.prev_total || 0).toLocaleString('pt-BR')} seguidores)`;

    $('spy-fl-result').style.display = 'block';

    if (msg.left?.length > 0) {
      $('spy-fl-left-section').style.display = 'block';
      const leftList = $('spy-fl-left-list');
      leftList.innerHTML = '';
      msg.left.slice(0, 50).forEach(u => {
        const a = document.createElement('a');
        a.className = 'user-item';
        a.href = `https://www.instagram.com/${u}/`;
        a.target = '_blank';
        a.innerHTML = `<div class="user-info"><div class="user-username">@${u}</div></div><div class="user-badges"><span class="badge-unfollower">Saiu</span></div>`;
        leftList.appendChild(a);
      });
    }

    if (msg.joined?.length > 0) {
      $('spy-fl-joined-section').style.display = 'block';
      const joinedList = $('spy-fl-joined-list');
      joinedList.innerHTML = '';
      msg.joined.slice(0, 50).forEach(u => {
        const a = document.createElement('a');
        a.className = 'user-item';
        a.href = `https://www.instagram.com/${u}/`;
        a.target = '_blank';
        a.innerHTML = `<div class="user-info"><div class="user-username">@${u}</div></div><div class="user-badges"><span class="badge-new">Novo</span></div>`;
        joinedList.appendChild(a);
      });
    }
  }

  if (msg.action === 'spy_fl_error') {
    $('spy-fl-status').style.display = 'none';
    $('btn-spy-followers').disabled = false;
    $('spy-fl-error').style.display = 'block';
    $('spy-fl-error').textContent = msg.text;
  }
});

init();
