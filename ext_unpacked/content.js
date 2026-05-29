function getCookie(name) {
  const cookies = document.cookie.split(';');
  for (const c of cookies) {
    const [n, v] = c.trim().split('=');
    if (n === name) return decodeURIComponent(v || '');
  }
  return null;
}

function getHeaders() {
  return {
    'x-ig-app-id': '936619743392459',
    'x-csrftoken': getCookie('csrftoken') || '',
    'x-asbd-id': '129477',
    'accept': '*/*',
    'x-requested-with': 'XMLHttpRequest'
  };
}

async function getCurrentUser() {
  const dsUserId = getCookie('ds_user_id');
  if (dsUserId) {
    let username = null;
    try {
      const resp = await fetch(`/api/v1/users/${dsUserId}/info/`, {
        headers: getHeaders(), credentials: 'include'
      });
      if (resp.ok) {
        const data = await resp.json();
        username = data.user?.username;
      }
    } catch (_) {}
    return { id: dsUserId, username };
  }

  const resp = await fetch('/api/v1/accounts/current_user/?edit=true', {
    headers: getHeaders(),
    credentials: 'include'
  });
  if (!resp.ok) throw new Error('Voce precisa estar logado no Instagram nesta aba.');
  const data = await resp.json();
  return { id: data.user?.pk, username: data.user?.username };
}

async function paginate(url) {
  const list = [];
  let nextMaxId = null;
  do {
    const fullUrl = `${url}?count=200${nextMaxId ? '&max_id=' + nextMaxId : ''}`;
    const resp = await fetch(fullUrl, { headers: getHeaders(), credentials: 'include' });
    if (!resp.ok) throw new Error('Instagram bloqueou a requisicao. Tente novamente em alguns minutos.');
    const data = await resp.json();
    list.push(...(data.users || []));
    nextMaxId = data.next_max_id || null;
    if (nextMaxId) await new Promise(r => setTimeout(r, 600));
  } while (nextMaxId);
  return list;
}

async function resolveUserId(username) {
  // Resolve @username -> user id usando a busca da propria sessao (mesma origem).
  const resp = await fetch(
    `/web/search/topsearch/?context=blended&query=${encodeURIComponent(username)}`,
    { headers: getHeaders(), credentials: 'include' }
  );
  if (!resp.ok) throw new Error(`busca topsearch falhou (${resp.status})`);
  const data = await resp.json();
  const alvo = (data.users || []).find(
    x => (x.user?.username || '').toLowerCase() === username.toLowerCase()
  );
  return alvo?.user?.pk || alvo?.user?.id || null;
}

async function infoById(userId) {
  // Endpoint que o modo "minha conta" ja usa com sucesso -> confiavel no WebKit/Orion.
  const resp = await fetch(`/api/v1/users/${userId}/info/`, {
    headers: getHeaders(), credentials: 'include'
  });
  if (!resp.ok) throw new Error(`users/${userId}/info falhou (${resp.status})`);
  const u = (await resp.json()).user;
  if (!u) throw new Error('info vazio');
  return {
    username:        u.username,
    full_name:       u.full_name || '',
    followers:       u.follower_count ?? 0,
    following:       u.following_count ?? 0,
    is_private:      u.is_private || false,
    profile_pic_url: u.profile_pic_url || '',
    user_id:         String(userId),
  };
}

async function getProfileInfo(username) {
  /**
   * Busca info publica de um perfil usando a sessao autenticada do usuario.
   * 1) tenta web_profile_info (mais rico)
   * 2) fallback: resolve id (topsearch) + users/{id}/info  -> mesmo caminho do "minha conta"
   */
  username = (username || '').trim().replace(/^@/, '');
  const erros = [];

  // 1) web_profile_info (relativo, mesma origem)
  try {
    const resp = await fetch(
      `/api/v1/users/web_profile_info/?username=${encodeURIComponent(username)}`,
      { headers: getHeaders(), credentials: 'include' }
    );
    if (resp.ok) {
      const u = (await resp.json()).data?.user;
      if (u) return {
        username:        u.username,
        full_name:       u.full_name || '',
        followers:       u.edge_followed_by?.count ?? 0,
        following:       u.edge_follow?.count ?? 0,
        is_private:      u.is_private || false,
        profile_pic_url: u.profile_pic_url || '',
        user_id:         u.id,
      };
      erros.push('web_profile_info sem user');
    } else {
      erros.push(`web_profile_info ${resp.status}`);
    }
  } catch (e) {
    erros.push(`web_profile_info: ${e.message}`);
  }

  // 2) fallback robusto: topsearch -> users/{id}/info
  try {
    const id = await resolveUserId(username);
    if (!id) throw new Error('@ nao encontrado na busca');
    return await infoById(id);
  } catch (e) {
    erros.push(`fallback: ${e.message}`);
  }

  throw new Error(`Nao consegui ler @${username}. (${erros.join(' | ')})`);
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  // ── Ping — confirma que o content script está vivo (usado pelo popup no iOS) ──
  if (msg.action === 'ping') {
    sendResponse({ alive: true });
    return true;
  }

  // ── Modo SPY: busca info de um perfil externo ──
  if (msg.action === 'spy') {
    (async () => {
      try {
        const targetUsername = msg.username?.trim().replace(/^@/, '');
        if (!targetUsername) throw new Error('Username invalido');

        chrome.runtime.sendMessage({ action: 'status', text: `Buscando @${targetUsername}...` });
        const info = await getProfileInfo(targetUsername);

        // NÃO fazemos o POST aqui: o content script roda na origem instagram.com e
        // no Orion/iOS o fetch cross-origin p/ foiembora é bloqueado. Mandamos os dados
        // crus para o POPUP, que fala com o foiembora pela origem da extensão (funciona).
        chrome.runtime.sendMessage({ action: 'spy_result', info });
      } catch (err) {
        chrome.runtime.sendMessage({ action: 'error', text: err.message });
      }
    })();
    sendResponse({ ok: true });
    return true;
  }

  // ── Modo SPY FOLLOWERS: busca lista completa de seguidores para diff ──
  if (msg.action === 'spy_followers') {
    (async () => {
      try {
        const targetUsername = msg.username?.trim().replace(/^@/, '');
        if (!targetUsername) throw new Error('Username invalido');

        chrome.runtime.sendMessage({ action: 'spy_fl_status', text: `Buscando info de @${targetUsername}...` });
        const info = await getProfileInfo(targetUsername);

        if (info.is_private) throw new Error('Perfil privado — nao e possivel auditar seguidores.');

        const MAX_FOLLOWERS = 50000;
        if (info.followers > MAX_FOLLOWERS) {
          throw new Error(`Perfil tem mais de ${MAX_FOLLOWERS.toLocaleString('pt-BR')} seguidores. Use o modo contagem.`);
        }

        chrome.runtime.sendMessage({ action: 'spy_fl_status', text: `Carregando seguidores de @${targetUsername}... (${info.followers.toLocaleString('pt-BR')})` });
        const followerList = await paginate(`/api/v1/friendships/${info.user_id}/followers/`);
        const followerUsernames = followerList.map(u => u.username);

        // POST de snapshot é feito pelo POPUP (origem da extensão fala com foiembora;
        // o content script em instagram.com é bloqueado no Orion). Mandamos a lista crua.
        chrome.runtime.sendMessage({
          action: 'spy_followers_result',
          username: targetUsername,
          info,
          followers: followerUsernames,
        });
      } catch (err) {
        chrome.runtime.sendMessage({ action: 'spy_fl_error', text: err.message });
      }
    })();
    sendResponse({ ok: true });
    return true;
  }

  if (msg.action !== 'analyze') return;

  (async () => {
    try {
      chrome.runtime.sendMessage({ action: 'status', text: 'Identificando sua conta...' });
      const user = await getCurrentUser();

      chrome.runtime.sendMessage({ action: 'status', text: 'Carregando quem voce segue...' });
      const following = await paginate(`/api/v1/friendships/${user.id}/following/`);

      chrome.runtime.sendMessage({
        action: 'status',
        text: `Carregando seus seguidores... (voce segue ${following.length})`
      });
      const followers = await paginate(`/api/v1/friendships/${user.id}/followers/`);

      const followerIds = new Set(followers.map(u => u.pk));
      const notFollowingBack = following
        .filter(u => !followerIds.has(u.pk))
        .map(u => ({
          username: u.username,
          full_name: u.full_name,
          profile_pic_url: u.profile_pic_url,
          is_verified: u.is_verified,
          is_private: u.is_private
        }));

      // O POST de snapshot é feito pelo POPUP (origem da extensão fala com foiembora;
      // o content script em instagram.com é bloqueado no Orion/iOS). Mandamos as listas cruas.
      chrome.runtime.sendMessage({
        action: 'result',
        notFollowingBack,
        totalFollowing: following.length,
        totalFollowers: followers.length,
        username: user.username,
        followers: followers.map(u => u.username),
        following: following.map(u => u.username),
      });
    } catch (err) {
      chrome.runtime.sendMessage({ action: 'error', text: err.message });
    }
  })();

  sendResponse({ ok: true });
  return true;
});
