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

        // Envia para o backend
        const stored = await chrome.storage.local.get('access_token');
        const token  = stored.access_token;
        let backendResult = null;
        if (token) {
          chrome.runtime.sendMessage({ action: 'status', text: 'Salvando no servidor...' });
          const r = await fetch('https://foiembora.up.railway.app/api/spy/update', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token, ...info })
          });
          if (r.ok) backendResult = await r.json();
        }

        chrome.runtime.sendMessage({
          action: 'spy_result',
          info,
          diff:         backendResult?.diff ?? null,
          prev_followers: backendResult?.prev_followers ?? null,
          is_new:       backendResult?.is_new ?? true,
        });
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

        const stored = await chrome.storage.local.get('access_token');
        const token  = stored.access_token;
        if (!token) throw new Error('Extensao nao conectada. Faca login no FoiEmbora.');

        chrome.runtime.sendMessage({ action: 'spy_fl_status', text: 'Salvando snapshot no servidor...' });
        const r = await fetch('https://foiembora.up.railway.app/api/spy/follower_snapshot', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token, username: targetUsername, followers: followerUsernames })
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          throw new Error(err.error || 'Erro ao salvar no servidor');
        }
        const result = await r.json();

        chrome.runtime.sendMessage({
          action: 'spy_followers_result',
          username: targetUsername,
          info,
          total: result.total,
          prev_total: result.prev_total,
          is_new: result.is_new,
          joined: result.joined,
          left: result.left,
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

      // ---- Sync snapshot com o backend ----
      let unfollowers = [];
      let new_followers = [];
      let snapshotOk = false;

      try {
        const stored = await chrome.storage.local.get('access_token');
        const token = stored.access_token;
        if (token) {
          chrome.runtime.sendMessage({ action: 'status', text: 'Sincronizando com o servidor...' });
          const snapshotResp = await fetch(
            'https://foiembora.up.railway.app/api/snapshot',
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                token: token,
                followers: followers.map(u => u.username),
                following: following.map(u => u.username)
              })
            }
          );
          if (snapshotResp.ok) {
            const snapshotData = await snapshotResp.json();
            unfollowers = snapshotData.unfollowers || [];
            new_followers = snapshotData.new_followers || [];
            snapshotOk = true;
          }
        }
      } catch (_) {
        // Falha silenciosa — ainda retorna dados locais
      }

      chrome.runtime.sendMessage({
        action: 'result',
        notFollowingBack,
        totalFollowing: following.length,
        totalFollowers: followers.length,
        username: user.username,
        unfollowers,
        new_followers,
        snapshotOk
      });
    } catch (err) {
      chrome.runtime.sendMessage({ action: 'error', text: err.message });
    }
  })();

  sendResponse({ ok: true });
  return true;
});
