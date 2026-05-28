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
  // Lê o ID do usuário direto do cookie — sem chamada à API
  const dsUserId = getCookie('ds_user_id');
  if (dsUserId) {
    // Tenta pegar o username via GraphQL
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

  // Fallback: tenta via endpoint de conta
  const resp = await fetch('/api/v1/accounts/current_user/?edit=true', {
    headers: getHeaders(),
    credentials: 'include'
  });
  if (!resp.ok) throw new Error('Você precisa estar logado no Instagram nesta aba.');
  const data = await resp.json();
  return { id: data.user?.pk, username: data.user?.username };
}

async function paginate(url) {
  const list = [];
  let nextMaxId = null;
  do {
    const fullUrl = `${url}?count=200${nextMaxId ? '&max_id=' + nextMaxId : ''}`;
    const resp = await fetch(fullUrl, { headers: getHeaders(), credentials: 'include' });
    if (!resp.ok) throw new Error('Instagram bloqueou a requisição. Tente novamente em alguns minutos.');
    const data = await resp.json();
    list.push(...(data.users || []));
    nextMaxId = data.next_max_id || null;
    if (nextMaxId) await new Promise(r => setTimeout(r, 600));
  } while (nextMaxId);
  return list;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action !== 'analyze') return;

  (async () => {
    try {
      chrome.runtime.sendMessage({ action: 'status', text: 'Identificando sua conta...' });
      const user = await getCurrentUser();

      chrome.runtime.sendMessage({ action: 'status', text: 'Carregando quem você segue...' });
      const following = await paginate(`/api/v1/friendships/${user.id}/following/`);

      chrome.runtime.sendMessage({
        action: 'status',
        text: `Carregando seus seguidores... (você segue ${following.length})`
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

      chrome.runtime.sendMessage({
        action: 'result',
        notFollowingBack,
        totalFollowing: following.length,
        totalFollowers: followers.length,
        username: user.username
      });
    } catch (err) {
      chrome.runtime.sendMessage({ action: 'error', text: err.message });
    }
  })();

  sendResponse({ ok: true });
  return true;
});
