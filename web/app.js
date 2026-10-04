const csrf = document.querySelector('meta[name="repopulated-token"]').content;
const $ = id => document.getElementById(id);
let initialized = false;
async function api(route, body) {
  const response = await fetch('/api/' + route, {
    method: body === undefined ? 'GET' : 'POST',
    headers: { 'X-Repopulated-Token': csrf, 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.message || value.error);
  return value;
}
function element(tag, text, className) {
  const e = document.createElement(tag); e.textContent = text;
  if (className) e.className = className; return e;
}
async function action(route, body) {
  $('error').textContent = '';
  try { await api(route, body); await refresh(); }
  catch (e) { $('error').textContent = e.message; }
}
for (const [id, route, body] of [
  ['discover', 'discover', {}], ['ready', 'ready', { ready: true }],
  ['unready', 'ready', { ready: false }], ['disconnect', 'disconnect', {}],
  ['start', 'start', {}], ['save', 'save', {}]
]) $(id).onclick = () => action(route, body);
for (const id of ['probe', 'join', 'chat']) $(id).onsubmit = e => {
  e.preventDefault(); const body = Object.fromEntries(new FormData(e.target));
  if (body.port) body.port = Number(body.port);
  if (body.faction) body.faction = Number(body.faction);
  action(id, body);
  if (id === 'chat') e.target.reset();
};
$('settings').onsubmit = e => {
  e.preventDefault(); const form = e.target;
  const body = Object.fromEntries(new FormData(form));
  for (const key of ['maxPlayers', 'aiFactions', 'seed', 'tickRate']) body[key] = Number(body[key]);
  body.factions = body.factions.split(',').map(s => Number(s.trim()));
  body.friendlyFire = form.elements.friendlyFire.checked;
  body.pauseWhenEmpty = form.elements.pauseWhenEmpty.checked;
  body.sharedExploration = form.elements.sharedExploration.checked;
  action('settings', body);
};
let refreshing = false;
$('forget').onclick = () => action('forget', { host: $('join').elements.host.value, port: Number($('join').elements.port.value) });
async function refresh() {
  if (refreshing) return; refreshing = true;
  try {
    const state = await api('state');
    $('servers').replaceChildren();
    if (!state.servers.length) $('servers').append(element('p', 'Scan your LAN or add a server by address.'));
    for (const server of state.servers) {
      const card = element('div', '', 'card');
      card.append(element('h3', server.name), element('p', `${server.host}:${server.port}`),
        element('p', server.online === false ? 'Unavailable · last known listing' :
          `${server.connectedPlayers}/${server.settings.maxPlayers} online · ${server.phase}`, 'pill'));
      const button = element('button', 'Select galaxy');
      button.onclick = () => {
        $('join').elements.host.value = server.host; $('join').elements.port.value = server.port;
        $('join').elements.faction.value = server.settings.factions[0]; $('join').scrollIntoView({ behavior: 'smooth' });
      };
      card.append(button); $('servers').append(card);
    }
    $('connection').textContent = state.client.connected ? `Connected · ${state.client.lobby?.phase ?? 'joining'}` :
      `Disconnected${state.client.error ? ' · ' + state.client.error : ''}${!state.manifestLoaded ? ' · Manifest required' : ''}`;
    for (const id of ['ready', 'unready', 'disconnect']) $(id).disabled = !state.client.connected;
    $('forget').disabled = state.client.connected;
    $('join').querySelector('button').disabled = state.client.connected || !state.manifestLoaded;
    const lobby = state.client.lobby ?? state.host;
    $('roster').replaceChildren();
    for (const player of lobby?.players ?? []) {
      const row = element('div', '', 'player');
      row.append(element('span', `${player.name} · base faction ${player.faction} · empire ${player.empire}`),
        element('small', player.connected ? (player.ready ? 'Ready' : 'Preparing') : 'Reserved / offline'));
      if (state.host?.phase === 'lobby') {
        const kick = element('button', 'Remove', 'secondary'); kick.onclick = () => action('kick', { id: player.id }); row.append(kick);
      }
      $('roster').append(row);
    }
    $('messages').replaceChildren(...state.chats.map(m => element('p', `${m.name}: ${m.text}`)));
    $('hosting').hidden = !state.host;
    if (state.host) {
      $('engine').textContent = state.host.engineConnected ? 'Experimental adapter connection present.' : 'Native game adapter unavailable. Gameplay cannot start.';
      $('start').disabled = !state.host.engineConnected || !['lobby', 'suspended'].includes(state.host.phase);
      for (const input of $('settings').elements) input.disabled = state.host.phase !== 'lobby';
      if (!initialized) {
        for (const [key, value] of Object.entries(state.host.settings)) {
          const input = $('settings').elements[key];
          if (typeof value === 'boolean') input.checked = value;
          else input.value = Array.isArray(value) ? value.join(', ') : value;
        }
        initialized = true;
      }
    }
  } catch (e) { $('error').textContent = e.message; }
  finally { refreshing = false; }
}
refresh(); setInterval(refresh, 1500);
