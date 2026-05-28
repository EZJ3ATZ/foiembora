const $ = id => document.getElementById(id);

async function getInstagramTab() {
  const tabs = await chrome.tabs.query({ url: 'https://www.instagram.com/*' });
  return tabs.length > 0 ? tabs[0] : null;
}

async function init() {
  const igTab = await getInstagramTab();

  if (!igTab) {
    $('screen-wrong').style.display = 'block';
    $('btn-open-ig').addEventListener('click', () => {
      chrome.tabs.create({ url: 'https://www.instagram.com/' });
    });
    return;
  }

  $('screen-main').style.display = 'block';

  $('btn-analyze').addEventListener('click', async () => {
    $('btn-analyze').disabled = true;
    $('btn-analyze').textContent = 'Analisando...';
    $('status-box').classList.add('visible');
    $('error-box').classList.remove('visible');
    $('results-header').classList.remove('visible');
    $('results-list').classList.remove('visible');
    $('results-list').innerHTML = '';
    $('stats-row').style.display = 'none';

    // Inject content script if needed then send message
    try {
      await chrome.scripting.executeScript({
        target: { tabId: igTab.id },
        files: ['content.js']
      });
    } catch (_) {}

    chrome.tabs.sendMessage(igTab.id, { action: 'analyze' });
  });
}

// Listen for messages from content script
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action === 'status') {
    $('status-text').textContent = msg.text;
  }

  if (msg.action === 'error') {
    $('status-box').classList.remove('visible');
    $('error-box').classList.add('visible');
    $('error-box').textContent = '⚠️ ' + msg.text;
    $('btn-analyze').disabled = false;
    $('btn-analyze').textContent = '🔍 Tentar novamente';
  }

  if (msg.action === 'result') {
    $('status-box').classList.remove('visible');

    // Stats
    $('stat-following').textContent = msg.totalFollowing;
    $('stat-followers').textContent = msg.totalFollowers;
    $('stat-notback').textContent = msg.notFollowingBack.length;
    $('stats-row').style.display = 'flex';

    // Results list
    const list = msg.notFollowingBack;
    $('results-count').textContent = list.length;
    $('results-header').classList.add('visible');
    $('results-list').classList.add('visible');

    if (list.length === 0) {
      $('results-list').innerHTML = '<div style="text-align:center;color:#71717a;font-size:12px;padding:16px;">🎉 Todo mundo que você segue te segue de volta!</div>';
    } else {
      list.forEach(user => {
        const a = document.createElement('a');
        a.className = 'user-item';
        a.href = `https://www.instagram.com/${user.username}/`;
        a.target = '_blank';

        const img = document.createElement('img');
        img.className = 'user-avatar';
        img.referrerPolicy = 'no-referrer';
        img.crossOrigin = 'anonymous';
        // Gera avatar de letra como fallback imediato
        const letter = (user.full_name || user.username || '?')[0].toUpperCase();
        const colors = ['#833ab4','#fd1d1d','#fcb045','#a855f7','#ec4899','#f97316'];
        const color = colors[letter.charCodeAt(0) % colors.length];
        const fallbackSvg = `data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 36 36"><circle cx="18" cy="18" r="18" fill="${encodeURIComponent(color)}"/><text x="18" y="23" text-anchor="middle" font-size="15" font-weight="bold" fill="white">${letter}</text></svg>`;
        img.src = user.profile_pic_url || fallbackSvg;
        img.onerror = () => { img.src = fallbackSvg; };

        // Nome: esconde full_name se for igual ao username
        const displayName = user.full_name && user.full_name.toLowerCase() !== user.username.toLowerCase()
          ? user.full_name : null;

        const info = document.createElement('div');
        info.className = 'user-info';
        info.innerHTML = `
          <div class="user-username">@${user.username}${user.is_verified ? ' <span style="color:#a855f7">✓</span>' : ''}</div>
          ${displayName ? `<div class="user-name">${displayName}</div>` : ''}
        `;

        const badges = document.createElement('div');
        badges.className = 'user-badges';
        if (user.is_private) {
          const b = document.createElement('span');
          b.className = 'badge-private';
          b.textContent = 'Privado';
          badges.appendChild(b);
        }

        a.appendChild(img);
        a.appendChild(info);
        a.appendChild(badges);
        $('results-list').appendChild(a);
      });
    }

    $('btn-analyze').disabled = false;
    $('btn-analyze').textContent = '🔄 Verificar novamente';
  }
});

init();
