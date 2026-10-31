'use strict';
// ---------------------------------------------------------------------------
// Studio Montage - logique de la page
// ---------------------------------------------------------------------------
let STATE = null;
const $ = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));

const COLOR_LABELS = {
  main: 'Principale', pop: 'Accent (pop)', light: 'Fond clair', soft: 'Secondaire',
  caption: 'Sous-titres', caption_accent: 'Mot accentué',
};
const FONT_LABELS = { head: 'Titres', body: 'Texte', accent: 'Accent' };
const IDEA_STAGES = [
  { key: 'idee', label: 'Idée', color: '#9FB4E8' },
  { key: 'tournee', label: 'Tournée', color: '#e8b64a' },
  { key: 'montage', label: 'En montage', color: '#4F7DF0' },
  { key: 'postee', label: 'Postée', color: '#43c08a' },
];

// --- utilitaires -----------------------------------------------------------
function toast(msg) {
  const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden');
  clearTimeout(t._tm); t._tm = setTimeout(() => t.classList.add('hidden'), 2200);
}
async function copyText(txt) {
  try { await navigator.clipboard.writeText(txt); toast('Copié ! Colle-le dans ta conversation avec Claude.'); }
  catch (_) {
    const ta = document.createElement('textarea'); ta.value = txt; document.body.appendChild(ta);
    ta.select(); try { document.execCommand('copy'); toast('Copié !'); } catch (e) { alert(txt); }
    ta.remove();
  }
}
function fmtSize(b) { if (!b) return ''; const mo = b / 1048576; return mo >= 1 ? mo.toFixed(0) + ' Mo' : (b / 1024).toFixed(0) + ' Ko'; }
function esc(s) { return (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }

// --- chargement ------------------------------------------------------------
async function load() {
  const r = await fetch('/api/state'); STATE = await r.json();
  $('#reelsHome').textContent = STATE.reelsHome;
  const t = STATE.tools || {};
  $('#toolHint').textContent = t.ffmpeg ? 'ffmpeg OK' : 'ffmpeg absent (pas de vignettes)';
  renderBanner(); renderCurrent(); renderVideos(); renderDelivered();
  renderBrand(); renderInspiration(); renderBoard();
}
function renderBanner() {
  const b = $('#banner');
  if (STATE.brandMissing) {
    b.classList.remove('hidden');
    b.innerHTML = 'Aucun fichier de marque trouvé dans <b>' + esc(STATE.reelsHome) + '/brand.json</b>. ' +
      'Ouvre Claude Code et dis « configure ma marque » pour lancer le petit entretien. ' +
      'Tu peux déjà régler couleurs et typos ici, elles seront enregistrées.';
  } else b.classList.add('hidden');
}

// --- 1. EN COURS -----------------------------------------------------------
function renderCurrent() {
  const wrap = $('#currentWrap');
  const reel = STATE.reels.find(r => r.name === STATE.current);
  if (!reel) {
    wrap.innerHTML = '<div class="current-card"><div class="current-info">' +
      '<h2>Aucune vidéo pour l\'instant</h2>' +
      '<p class="lead">Dès que tu confies une vidéo à Claude Code (« edit this into a reel »), elle apparaît ici avec son avancement.</p>' +
      '</div></div>';
    return;
  }
  const videoHtml = reel.bestVideo
    ? `<video src="/media?path=${encodeURIComponent(STATE.reelsHome + '/' + reel.name + '/' + reel.bestVideo)}" controls playsinline poster="/thumb?reel=${encodeURIComponent(reel.name)}"></video>`
    : '<div class="ph">🎬</div>';

  const coupes = reel.fillers != null ? `${reel.fillers} hésitation(s) repérée(s)` : (reel.stage.step >= 2 ? 'coupes en préparation' : 'pas encore');
  const subs = reel.words != null ? `${reel.words} mots transcrits · style « ${reel.captionMode || 'word'} »` : 'pas encore';
  const finale = reel.hasFinal
    ? (reel.versions.find(v => v.key === 'finale') ? 'version finale rendue' : 'aperçu rendu')
    : 'pas encore';

  const steps = [
    { b: 'Coupes', s: coupes, done: reel.stage.step >= 2 },
    { b: 'Sous-titres', s: subs, done: reel.stage.step >= 2 && reel.words != null },
    { b: 'Version finale', s: finale, done: reel.hasFinal },
  ];
  const pct = Math.round((reel.stage.step / 6) * 100);

  wrap.innerHTML = `<div class="current-card">
    <div class="current-video">${videoHtml}</div>
    <div class="current-info">
      <h2>${esc(reel.name)}</h2>
      <div class="badges"><span class="badge step">${esc(reel.stage.label)}</span></div>
      <div class="progress"><div style="width:${pct}%"></div></div>
      <div class="steps">
        ${steps.map(st => `<div class="step-line">
          <div class="step-dot ${st.done ? 'done' : ''}">${st.done ? '✓' : '•'}</div>
          <div class="step-txt"><b>${st.b}</b><span>${esc(st.s)}</span></div>
        </div>`).join('')}
      </div>
      <button class="primary-btn" data-req="${esc(reel.name)}">📋 Demander une modif à Claude</button>
    </div>
  </div>`;
  wrap.querySelector('[data-req]').addEventListener('click', () => copyModifRequest(reel));
}

// --- 2. MES VIDEOS ---------------------------------------------------------
function copyModifRequest(reel) {
  const txt =
`Claude, je veux modifier mon reel « ${reel.name} » (dossier ${STATE.reelsHome}/${reel.name}).

Ce que je veux changer :
- (écris ici : coupes, sous-titres, graphismes, couleurs, musique, version finale…)

État actuel : ${reel.stage.label}.`;
  copyText(txt);
}
function renderVideos() {
  const grid = $('#videosGrid');
  if (!STATE.reels.length) { grid.innerHTML = '<p class="lead">Aucune vidéo pour l\'instant.</p>'; return; }
  grid.innerHTML = STATE.reels.map(reel => {
    const versions = reel.versions.length
      ? reel.versions.map(v => `<span class="badge">${esc(v.label)}${v.duration ? ' · ' + v.duration + 's' : ''}</span>`).join('')
      : '<span class="badge">brouillon</span>';
    return `<div class="card">
      <div class="thumb" style="background-image:url('/thumb?reel=${encodeURIComponent(reel.name)}')">${reel.bestVideo ? '' : '🎬'}</div>
      <div class="body">
        <div class="title">${esc(reel.name)}</div>
        <div class="badges"><span class="badge step">${esc(reel.stage.label)}</span></div>
        <div class="badges">${versions}</div>
        <button class="mini-btn" data-req="${esc(reel.name)}">📋 Demander une modif</button>
      </div>
    </div>`;
  }).join('');
  grid.querySelectorAll('[data-req]').forEach(btn =>
    btn.addEventListener('click', () => copyModifRequest(STATE.reels.find(r => r.name === btn.dataset.req))));
}
function renderDelivered() {
  const d = STATE.delivered || { dir: '', files: [] };
  $('#deliveredPath').textContent = d.files.length ? 'Dossier : ' + d.dir : 'Aucune vidéo livrée pour l\'instant (' + d.dir + ').';
  $('#deliveredGrid').innerHTML = d.files.map(f => `<div class="card">
    <div class="thumb">🎬</div>
    <div class="body"><div class="title">${esc(f.name)}</div><div class="hint">${fmtSize(f.size)}</div></div>
  </div>`).join('');
}

// --- 3. MARQUE -------------------------------------------------------------
function renderBrand() {
  const b = STATE.brand;
  const colors = b.colors || {};
  $('#colorRows').innerHTML = Object.keys(COLOR_LABELS).map(k => {
    const val = colors[k] || '#ffffff';
    return `<div class="color-row">
      <input type="color" value="${esc(val)}" data-color="${k}" />
      <label>${COLOR_LABELS[k]}</label>
      <span class="hex" data-hex="${k}">${esc(val)}</span>
    </div>`;
  }).join('');
  const fonts = b.fonts || {};
  $('#fontRows').innerHTML = Object.keys(FONT_LABELS).map(k => {
    const fam = (fonts[k] && fonts[k].family) || '';
    return `<div class="font-row"><label>${FONT_LABELS[k]}</label>
      <input type="text" value="${esc(fam)}" data-font="${k}" placeholder="ex : Montserrat" /></div>`;
  }).join('');
  $$('#colorRows input[type=color]').forEach(inp => inp.addEventListener('input', () => {
    $(`[data-hex="${inp.dataset.color}"]`).textContent = inp.value; updatePreview();
  }));
  $$('#fontRows input').forEach(inp => inp.addEventListener('input', updatePreview));
  updatePreview();
}
function readBrandForm() {
  const colors = {}; $$('#colorRows input[type=color]').forEach(i => colors[i.dataset.color] = i.value);
  const fonts = {}; $$('#fontRows input').forEach(i => { if (i.value.trim()) fonts[i.dataset.font] = { family: i.value.trim() }; });
  return { colors, fonts };
}
function updatePreview() {
  const f = readBrandForm(); const c = f.colors; const ph = $('#phonePreview');
  ph.style.setProperty('--light', c.light); ph.style.setProperty('--main', c.main);
  ph.style.setProperty('--pop', c.pop); ph.style.setProperty('--cap', c.caption);
  ph.style.setProperty('--capacc', c.caption_accent);
  const famHead = (f.fonts.head && f.fonts.head.family) || 'sans-serif';
  const famBody = (f.fonts.body && f.fonts.body.family) || 'sans-serif';
  const famAcc = (f.fonts.accent && f.fonts.accent.family) || 'serif';
  ph.style.setProperty('--font-head', `'${famHead}', sans-serif`);
  ph.style.setProperty('--font-body', `'${famBody}', sans-serif`);
  ph.style.setProperty('--font-accent', `'${famAcc}', serif`);
}
async function saveBrand() {
  const r = await fetch('/api/brand', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(readBrandForm()) });
  if (r.ok) { $('#brandSaved').textContent = '✓ Marque enregistrée dans brand.json'; STATE.brandMissing = false; renderBanner(); setTimeout(() => $('#brandSaved').textContent = '', 3000); }
  else $('#brandSaved').textContent = 'Erreur à l\'enregistrement';
}
function copyBrandSyncRequest() {
  copyText(`Claude, j'ai mis à jour mes couleurs et mes typos dans ${STATE.reelsHome}/brand.json via le Studio Montage. Mets à jour ${STATE.reelsHome}/brand.md pour que le texte corresponde (couleurs et polices), sans rien casser d'autre.`);
}

// --- 4. INSPIRATION --------------------------------------------------------
function renderInspiration() {
  const grid = $('#inspoGrid');
  const items = STATE.inspiration || [];
  if (!items.length) { grid.innerHTML = '<p class="lead">Rien pour l\'instant. Glisse une vidéo ou colle un lien ci-dessus.</p>'; return; }
  grid.innerHTML = items.map(it => {
    let media;
    if (it.type === 'file') media = `<video src="/inspo?file=${encodeURIComponent(it.file)}" controls playsinline class="thumb" style="aspect-ratio:9/16;object-fit:cover"></video>`;
    else if (it.thumbnail) media = `<a href="${esc(it.link)}" target="_blank"><div class="thumb" style="background-image:url('${esc(it.thumbnail)}')"></div></a>`;
    else media = `<a href="${esc(it.link)}" target="_blank"><div class="thumb">${it.platform === 'TikTok' ? '🎵' : it.platform === 'Instagram' ? '📷' : '▶️'}</div></a>`;
    return `<div class="card">
      ${media}
      <div class="body">
        <div class="badges"><span class="badge">${esc(it.platform || 'Vidéo')}</span></div>
        <div class="title" style="font-size:13px">${esc(it.title || it.link || it.file || '')}</div>
        <button class="mini-btn" data-del="${it.id}">Retirer</button>
      </div>
    </div>`;
  }).join('');
  grid.querySelectorAll('[data-del]').forEach(btn => btn.addEventListener('click', () => delInspo(btn.dataset.del)));
}
async function addLink() {
  const inp = $('#inspoLink'); const link = inp.value.trim(); if (!link) return;
  const r = await fetch('/api/inspiration/link', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ link }) });
  const j = await r.json(); if (r.ok) { STATE.inspiration = j.inspiration; inp.value = ''; renderInspiration(); toast('Inspiration ajoutée'); }
  else toast(j.error || 'Lien invalide');
}
async function delInspo(id) {
  const r = await fetch('/api/inspiration/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id }) });
  const j = await r.json(); if (r.ok) { STATE.inspiration = j.inspiration; renderInspiration(); }
}
async function uploadInspo(file) {
  toast('Import de la vidéo…');
  const r = await fetch('/api/inspiration/upload?name=' + encodeURIComponent(file.name), { method: 'POST', body: file });
  const j = await r.json(); if (r.ok) { STATE.inspiration = j.inspiration; renderInspiration(); toast('Vidéo ajoutée'); }
  else toast(j.error || 'Import impossible');
}
function wireDropzone() {
  const dz = $('#dropzone');
  ['dragenter', 'dragover'].forEach(e => dz.addEventListener(e, ev => { ev.preventDefault(); dz.classList.add('drag'); }));
  ['dragleave', 'drop'].forEach(e => dz.addEventListener(e, ev => { ev.preventDefault(); dz.classList.remove('drag'); }));
  dz.addEventListener('drop', ev => {
    const f = ev.dataTransfer.files[0];
    if (f) uploadInspo(f);
    else { const txt = ev.dataTransfer.getData('text'); if (/^https?:/.test(txt)) { $('#inspoLink').value = txt; addLink(); } }
  });
}

// --- 5. IDEES (kanban) -----------------------------------------------------
function renderBoard() {
  const ideas = STATE.ideas || [];
  const board = $('#board');
  board.innerHTML = IDEA_STAGES.map(st => {
    const items = ideas.filter(i => i.stage === st.key);
    return `<div class="col" data-stage="${st.key}">
      <div class="col-head"><div><span class="dot" style="background:${st.color}"></span><span class="col-title">${st.label}</span></div><span class="col-count">${items.length}</span></div>
      <div class="col-body" data-stage="${st.key}">
        ${items.map(i => `<div class="idea" draggable="true" data-id="${i.id}">${esc(i.title)}<span class="del" data-del="${i.id}">✕</span></div>`).join('')}
      </div>
    </div>`;
  }).join('');
  // drag
  let dragId = null;
  board.querySelectorAll('.idea').forEach(el => {
    el.addEventListener('dragstart', () => { dragId = el.dataset.id; el.classList.add('dragging'); });
    el.addEventListener('dragend', () => el.classList.remove('dragging'));
  });
  board.querySelectorAll('.col').forEach(col => {
    col.addEventListener('dragover', ev => { ev.preventDefault(); col.classList.add('drag'); });
    col.addEventListener('dragleave', () => col.classList.remove('drag'));
    col.addEventListener('drop', ev => {
      ev.preventDefault(); col.classList.remove('drag');
      const it = STATE.ideas.find(x => x.id === dragId);
      if (it) { it.stage = col.dataset.stage; saveIdeas(); renderBoard(); }
    });
  });
  board.querySelectorAll('[data-del]').forEach(btn => btn.addEventListener('click', ev => {
    ev.stopPropagation(); STATE.ideas = STATE.ideas.filter(x => x.id !== btn.dataset.del); saveIdeas(); renderBoard();
  }));
}
function addIdea() {
  const inp = $('#newIdea'); const t = inp.value.trim(); if (!t) return;
  STATE.ideas.unshift({ id: 'd' + Date.now().toString(36), title: t, stage: 'idee', created: Date.now() });
  inp.value = ''; saveIdeas(); renderBoard();
}
async function saveIdeas() {
  await fetch('/api/ideas', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ideas: STATE.ideas }) });
}

// --- navigation + events ---------------------------------------------------
function wireNav() {
  $$('.nav-btn').forEach(btn => btn.addEventListener('click', () => {
    $$('.nav-btn').forEach(b => b.classList.remove('active'));
    $$('.tab').forEach(t => t.classList.remove('active'));
    btn.classList.add('active'); $('#tab-' + btn.dataset.tab).classList.add('active');
  }));
}
document.addEventListener('DOMContentLoaded', () => {
  wireNav(); wireDropzone();
  $('#refreshBtn').addEventListener('click', load);
  $('#saveBrand').addEventListener('click', saveBrand);
  $('#copyBrandReq').addEventListener('click', copyBrandSyncRequest);
  $('#addLink').addEventListener('click', addLink);
  $('#inspoLink').addEventListener('keydown', e => { if (e.key === 'Enter') addLink(); });
  $('#addIdea').addEventListener('click', addIdea);
  $('#newIdea').addEventListener('keydown', e => { if (e.key === 'Enter') addIdea(); });
  load();
});
