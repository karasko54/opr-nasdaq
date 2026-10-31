#!/usr/bin/env node
/*
 * Studio Montage - serveur local (zero dependance, Node pur).
 * Lit le dossier ~/reels de l'editeur de reels et sert une page web en francais.
 * Lie seulement a 127.0.0.1 : rien n'est expose sur le reseau.
 */
'use strict';
const http = require('http');
const fs = require('fs');
const os = require('os');
const path = require('path');
const url = require('url');
const https = require('https');
const { spawnSync } = require('child_process');

// ---------------------------------------------------------------------------
// Chemins
// ---------------------------------------------------------------------------
function expandHome(p) {
  if (!p) return p;
  if (p === '~') return os.homedir();
  if (p.startsWith('~/')) return path.join(os.homedir(), p.slice(2));
  return p;
}
const REELS_HOME = expandHome(process.env.REELS_HOME || path.join(os.homedir(), 'reels'));
const STUDIO_DIR = path.join(REELS_HOME, 'studio');
const THUMBS_DIR = path.join(STUDIO_DIR, 'thumbs');
const INSPO_DIR = path.join(STUDIO_DIR, 'inspiration');
const IDEAS_FILE = path.join(STUDIO_DIR, 'ideas.json');
const INSPO_FILE = path.join(STUDIO_DIR, 'inspiration.json');
const BRAND_JSON = path.join(REELS_HOME, 'brand.json');
const BRAND_MD = path.join(REELS_HOME, 'brand.md');
const APP_DIR = __dirname;
const PUBLIC_DIR = path.join(APP_DIR, 'public');
const SEEDS_DIR = path.join(APP_DIR, '..', 'seeds');
const HOME_MARK = path.join(APP_DIR, '..'); // install root; .port file goes next to app/

function ensureDirs() {
  for (const d of [REELS_HOME, STUDIO_DIR, THUMBS_DIR, INSPO_DIR]) {
    try { fs.mkdirSync(d, { recursive: true }); } catch (_) {}
  }
}

// ---------------------------------------------------------------------------
// Outils fichiers
// ---------------------------------------------------------------------------
function readJSON(p, fallback) {
  try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch (_) { return fallback; }
}
function writeJSON(p, obj) {
  fs.mkdirSync(path.dirname(p), { recursive: true });
  fs.writeFileSync(p, JSON.stringify(obj, null, 2));
}
function readText(p, fallback) {
  try { return fs.readFileSync(p, 'utf8'); } catch (_) { return fallback; }
}
function exists(p) { try { fs.accessSync(p); return true; } catch (_) { return false; } }
function statSafe(p) { try { return fs.statSync(p); } catch (_) { return null; } }
function sizeOf(p) { const s = statSafe(p); return s ? s.size : 0; }
function mtimeOf(p) { const s = statSafe(p); return s ? s.mtimeMs : 0; }

// ffprobe (fourni avec ffmpeg) - cache memoire par chemin+mtime
let HAS_FFPROBE = null;
function hasFfprobe() {
  if (HAS_FFPROBE === null) {
    const r = spawnSync('ffprobe', ['-version'], { encoding: 'utf8' });
    HAS_FFPROBE = !r.error;
  }
  return HAS_FFPROBE;
}
let HAS_FFMPEG = null;
function hasFfmpeg() {
  if (HAS_FFMPEG === null) {
    const r = spawnSync('ffmpeg', ['-version'], { encoding: 'utf8' });
    HAS_FFMPEG = !r.error;
  }
  return HAS_FFMPEG;
}
const probeCache = new Map();
function probe(p) {
  if (!exists(p) || !hasFfprobe()) return null;
  const key = p + ':' + mtimeOf(p);
  if (probeCache.has(key)) return probeCache.get(key);
  const r = spawnSync('ffprobe', [
    '-v', 'error', '-select_streams', 'v:0',
    '-show_entries', 'stream=width,height:format=duration',
    '-of', 'json', p
  ], { encoding: 'utf8', timeout: 15000 });
  let out = null;
  if (!r.error && r.stdout) {
    try {
      const j = JSON.parse(r.stdout);
      const st = (j.streams && j.streams[0]) || {};
      out = {
        width: st.width || null,
        height: st.height || null,
        duration: j.format && j.format.duration ? Math.round(parseFloat(j.format.duration)) : null,
      };
    } catch (_) {}
  }
  probeCache.set(key, out);
  return out;
}

// ---------------------------------------------------------------------------
// Marque
// ---------------------------------------------------------------------------
const DEFAULT_BRAND = {
  name: '', resolution: [1080, 1920],
  colors: { main: '#4F7DF0', pop: '#FF5A8C', light: '#F3F6FF', soft: '#9FB4E8', caption: '#FFFFFF', caption_accent: '#FF5A8C' },
  fonts: {
    head: { family: 'Montserrat', file: '', weight: 700 },
    body: { family: 'Montserrat', file: '', weight: 500 },
    accent: { family: 'Playfair Display', file: '', weight: 400, style: 'italic' },
  },
  captions: { mode: 'word', case: 'lower', size: 104, style: 'editorial' },
};
function getBrand() {
  const b = readJSON(BRAND_JSON, null);
  if (!b) return { ...DEFAULT_BRAND, _missing: true };
  // garantir les clefs attendues par l'UI sans ecraser l'existant
  b.colors = Object.assign({}, DEFAULT_BRAND.colors, b.colors || {});
  b.fonts = Object.assign({}, DEFAULT_BRAND.fonts, b.fonts || {});
  b.captions = Object.assign({}, DEFAULT_BRAND.captions, b.captions || {});
  return b;
}
function saveBrand(patch) {
  const current = readJSON(BRAND_JSON, {}) || {};
  if (patch.colors) current.colors = Object.assign({}, current.colors || {}, patch.colors);
  if (patch.fonts) {
    current.fonts = current.fonts || {};
    for (const role of Object.keys(patch.fonts)) {
      current.fonts[role] = Object.assign({}, current.fonts[role] || {}, patch.fonts[role]);
    }
  }
  if (patch.captions) current.captions = Object.assign({}, current.captions || {}, patch.captions);
  if (typeof patch.name === 'string') current.name = patch.name;
  writeJSON(BRAND_JSON, current);
  return current;
}

// ---------------------------------------------------------------------------
// Scan des reels
// ---------------------------------------------------------------------------
const RESERVED = new Set(['fonts', 'assets', 'studio', 'node_modules']);
const REEL_MARKERS = ['raw.mov', 'transcript.json', 'edit.json', 'edl.json',
  'proposal.json', 'master.mp4', 'final.mp4', 'review_reel.json', 'reel.json',
  'fillers.json', 'master_transcript.json'];

function isReelDir(full, name) {
  if (name.startsWith('.') || RESERVED.has(name)) return false;
  const st = statSafe(full);
  if (!st || !st.isDirectory()) return false;
  return REEL_MARKERS.some(m => exists(path.join(full, m)));
}

function stageOf(dir, brandHeight) {
  const has = f => exists(path.join(dir, f));
  const finalP = path.join(dir, 'final.mp4');
  if (has('final.mp4')) {
    const pr = probe(finalP);
    const h = pr && pr.height ? pr.height : null;
    if (h && brandHeight && h >= brandHeight) return { key: 'finale', label: 'Finale rendue', step: 6 };
    if (h && h <= 1100) return { key: 'apercu', label: 'Aperçu rendu', step: 5 };
    return { key: 'finale', label: 'Vidéo rendue', step: 6 };
  }
  if (has('master.mp4')) return { key: 'master', label: 'Montage coupé', step: 4 };
  if (has('edl.json') || has('edit.json')) return { key: 'edit', label: 'Montage préparé', step: 4 };
  if (has('proposal.json') || has('review_reel.json') || has('reel.json')) return { key: 'revue', label: 'En revue', step: 3 };
  if (has('fillers.json')) return { key: 'coupes', label: 'Coupes détectées', step: 2 };
  if (has('transcript.json') || has('master_transcript.json')) return { key: 'transcrite', label: 'Transcrite', step: 2 };
  if (has('raw.mov')) return { key: 'importee', label: 'Importée', step: 1 };
  return { key: 'vide', label: 'Vide', step: 0 };
}

function reelMtime(dir) {
  let m = mtimeOf(dir);
  try {
    for (const f of fs.readdirSync(dir)) {
      m = Math.max(m, mtimeOf(path.join(dir, f)));
    }
  } catch (_) {}
  return m;
}

function scanReels() {
  const brand = getBrand();
  const brandH = (brand.resolution && brand.resolution[1]) || 1920;
  const out = [];
  let entries = [];
  try { entries = fs.readdirSync(REELS_HOME); } catch (_) { return out; }
  for (const name of entries) {
    const full = path.join(REELS_HOME, name);
    if (!isReelDir(full, name)) continue;
    const has = f => exists(path.join(full, f));
    const stage = stageOf(full, brandH);

    // sous-titres
    const tr = readJSON(path.join(full, 'master_transcript.json'), null) || readJSON(path.join(full, 'transcript.json'), null);
    let words = null;
    if (Array.isArray(tr)) words = tr.filter(w => (w.text || '').trim()).length;
    // hesitations
    const fj = readJSON(path.join(full, 'fillers.json'), null);
    let fillers = null;
    if (fj && Array.isArray(fj.transcription)) fillers = fj.transcription.filter(t => (t.text || '').trim()).length;

    // versions disponibles
    const versions = [];
    if (has('master.mp4')) versions.push({ key: 'master', label: 'Montage (master)', file: 'master.mp4', size: sizeOf(path.join(full, 'master.mp4')) });
    if (has('final.mp4')) {
      const pr = probe(path.join(full, 'final.mp4'));
      const isPreview = pr && pr.height && pr.height <= 1100 && brandH > 1100;
      versions.push({ key: isPreview ? 'apercu' : 'finale', label: isPreview ? 'Aperçu' : 'Finale', file: 'final.mp4', size: sizeOf(path.join(full, 'final.mp4')), width: pr && pr.width, height: pr && pr.height, duration: pr && pr.duration });
    }
    const bestVideo = has('final.mp4') ? 'final.mp4' : has('master.mp4') ? 'master.mp4' : has('raw.mov') ? 'raw.mov' : null;

    out.push({
      name,
      stage,
      words,
      fillers,
      captionMode: (brand.captions && brand.captions.mode) || null,
      versions,
      bestVideo,
      hasRaw: has('raw.mov'),
      hasFinal: has('final.mp4'),
      mtime: reelMtime(full),
      delivered: false,
    });
  }
  out.sort((a, b) => b.mtime - a.mtime);
  return out;
}

function deliverDir() {
  const b = getBrand();
  return expandHome((b && b.deliver_dir) || '~/Movies/Reels');
}
function scanDelivered() {
  const d = deliverDir();
  const out = [];
  try {
    for (const f of fs.readdirSync(d)) {
      if (/\.(mp4|mov|m4v)$/i.test(f)) out.push({ name: f, size: sizeOf(path.join(d, f)), mtime: mtimeOf(path.join(d, f)) });
    }
  } catch (_) {}
  out.sort((a, b) => b.mtime - a.mtime);
  return { dir: d, files: out };
}

// ---------------------------------------------------------------------------
// Idees / Inspiration
// ---------------------------------------------------------------------------
function seedIdeasIfNeeded() {
  if (exists(IDEAS_FILE)) return;
  const seed = readJSON(path.join(SEEDS_DIR, 'ideas.json'), null);
  if (seed) writeJSON(IDEAS_FILE, seed);
}
function getIdeas() { return readJSON(IDEAS_FILE, []) || []; }
function saveIdeas(list) { writeJSON(IDEAS_FILE, Array.isArray(list) ? list : []); return getIdeas(); }

function getInspiration() { return readJSON(INSPO_FILE, []) || []; }
function saveInspiration(list) { writeJSON(INSPO_FILE, list); return list; }
function platformOf(link) {
  if (/tiktok\.com/i.test(link)) return 'TikTok';
  if (/instagram\.com/i.test(link)) return 'Instagram';
  if (/youtube\.com|youtu\.be/i.test(link)) return 'YouTube';
  return 'Lien';
}
function fetchOembed(link) {
  // TikTok a un oEmbed ouvert ; on tente juste une vignette, sans bloquer.
  return new Promise(resolve => {
    if (!/tiktok\.com/i.test(link)) return resolve(null);
    const api = 'https://www.tiktok.com/oembed?url=' + encodeURIComponent(link);
    const req = https.get(api, { timeout: 6000 }, res => {
      let data = '';
      res.on('data', c => (data += c));
      res.on('end', () => { try { const j = JSON.parse(data); resolve({ thumbnail: j.thumbnail_url || null, title: j.title || null, author: j.author_name || null }); } catch (_) { resolve(null); } });
    });
    req.on('error', () => resolve(null));
    req.on('timeout', () => { req.destroy(); resolve(null); });
  });
}

// ---------------------------------------------------------------------------
// Securite chemins (pour /media et /thumb)
// ---------------------------------------------------------------------------
function resolveSafe(rel, bases) {
  const target = path.resolve(expandHome(rel));
  for (const base of bases) {
    const b = path.resolve(base);
    if (target === b || target.startsWith(b + path.sep)) return target;
  }
  return null;
}

// ---------------------------------------------------------------------------
// HTTP helpers
// ---------------------------------------------------------------------------
const MIME = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'application/javascript; charset=utf-8', '.json': 'application/json; charset=utf-8', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.svg': 'image/svg+xml', '.mp4': 'video/mp4', '.mov': 'video/quicktime', '.m4v': 'video/x-m4v' };
function sendJSON(res, code, obj) { res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' }); res.end(JSON.stringify(obj)); }
function sendText(res, code, t) { res.writeHead(code, { 'Content-Type': 'text/plain; charset=utf-8' }); res.end(t); }
function readBody(req, limitBytes) {
  return new Promise((resolve, reject) => {
    const chunks = []; let n = 0;
    req.on('data', c => { n += c.length; if (limitBytes && n > limitBytes) { reject(new Error('too big')); req.destroy(); } else chunks.push(c); });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}

function serveStatic(res, file) {
  if (!exists(file)) return sendText(res, 404, 'introuvable');
  const ext = path.extname(file).toLowerCase();
  res.writeHead(200, { 'Content-Type': MIME[ext] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(res);
}

function streamMedia(req, res, filePath) {
  const st = statSafe(filePath);
  if (!st) return sendText(res, 404, 'introuvable');
  const ext = path.extname(filePath).toLowerCase();
  const type = MIME[ext] || 'application/octet-stream';
  const range = req.headers.range;
  if (range) {
    const m = /bytes=(\d*)-(\d*)/.exec(range);
    let start = m && m[1] ? parseInt(m[1], 10) : 0;
    let end = m && m[2] ? parseInt(m[2], 10) : st.size - 1;
    if (isNaN(start)) start = 0; if (isNaN(end) || end >= st.size) end = st.size - 1;
    if (start > end) start = 0;
    res.writeHead(206, {
      'Content-Range': `bytes ${start}-${end}/${st.size}`,
      'Accept-Ranges': 'bytes', 'Content-Length': end - start + 1, 'Content-Type': type,
    });
    fs.createReadStream(filePath, { start, end }).pipe(res);
  } else {
    res.writeHead(200, { 'Content-Length': st.size, 'Content-Type': type, 'Accept-Ranges': 'bytes' });
    fs.createReadStream(filePath).pipe(res);
  }
}

function makeThumb(reelName) {
  const reelDir = path.join(REELS_HOME, reelName);
  if (!statSafe(reelDir)) return null;
  const src = ['final.mp4', 'master.mp4', 'raw.mov'].map(f => path.join(reelDir, f)).find(exists);
  if (!src) return null;
  const outP = path.join(THUMBS_DIR, reelName.replace(/[^\w.-]/g, '_') + '.jpg');
  if (exists(outP) && mtimeOf(outP) >= mtimeOf(src)) return outP;
  if (!hasFfmpeg()) return null;
  try { fs.mkdirSync(THUMBS_DIR, { recursive: true }); } catch (_) {}
  const r = spawnSync('ffmpeg', ['-v', 'error', '-y', '-ss', '0.5', '-i', src, '-frames:v', '1', '-vf', 'scale=360:-2', outP], { timeout: 20000 });
  if (r.error || !exists(outP)) return null;
  return outP;
}

// ---------------------------------------------------------------------------
// Routage
// ---------------------------------------------------------------------------
async function handle(req, res) {
  const u = url.parse(req.url, true);
  const p = decodeURIComponent(u.pathname);

  // --- API ---
  if (p === '/api/ping') return sendJSON(res, 200, { ok: true });

  if (p === '/api/state') {
    seedIdeasIfNeeded();
    const reels = scanReels();
    const brand = getBrand();
    const current = reels.find(r => r.stage.step > 0 && r.stage.step < 6) || reels[0] || null;
    return sendJSON(res, 200, {
      reelsHome: REELS_HOME,
      brandMissing: !!brand._missing,
      brand,
      brandMd: readText(BRAND_MD, ''),
      reels,
      current: current ? current.name : null,
      delivered: scanDelivered(),
      ideas: getIdeas(),
      inspiration: getInspiration(),
      tools: { ffmpeg: hasFfmpeg(), ffprobe: hasFfprobe() },
    });
  }

  if (p === '/api/brand' && req.method === 'POST') {
    try { const body = JSON.parse((await readBody(req, 1e6)).toString('utf8')); return sendJSON(res, 200, { brand: saveBrand(body) }); }
    catch (e) { return sendJSON(res, 400, { error: String(e.message || e) }); }
  }

  if (p === '/api/ideas' && req.method === 'POST') {
    try { const body = JSON.parse((await readBody(req, 2e6)).toString('utf8')); return sendJSON(res, 200, { ideas: saveIdeas(body.ideas) }); }
    catch (e) { return sendJSON(res, 400, { error: String(e.message || e) }); }
  }

  if (p === '/api/inspiration/link' && req.method === 'POST') {
    try {
      const body = JSON.parse((await readBody(req, 1e5)).toString('utf8'));
      const link = (body.link || '').trim();
      if (!/^https?:\/\//i.test(link)) return sendJSON(res, 400, { error: 'lien invalide' });
      const list = getInspiration();
      const oe = await fetchOembed(link);
      const item = { id: 'i' + Date.now().toString(36), type: 'link', platform: platformOf(link), link, title: body.title || (oe && oe.title) || '', thumbnail: oe && oe.thumbnail || null, author: oe && oe.author || null, note: body.note || '', created: Date.now() };
      list.unshift(item); saveInspiration(list);
      return sendJSON(res, 200, { inspiration: list });
    } catch (e) { return sendJSON(res, 400, { error: String(e.message || e) }); }
  }

  if (p === '/api/inspiration/upload' && req.method === 'POST') {
    try {
      const name = (u.query.name || 'clip.mp4').toString().replace(/[^\w.-]/g, '_');
      const buf = await readBody(req, 500 * 1024 * 1024); // 500 Mo max
      fs.mkdirSync(INSPO_DIR, { recursive: true });
      const fname = Date.now().toString(36) + '_' + name;
      fs.writeFileSync(path.join(INSPO_DIR, fname), buf);
      const list = getInspiration();
      const item = { id: 'i' + Date.now().toString(36), type: 'file', file: fname, title: name, note: '', created: Date.now() };
      list.unshift(item); saveInspiration(list);
      return sendJSON(res, 200, { inspiration: list });
    } catch (e) { return sendJSON(res, 400, { error: String(e.message || e) }); }
  }

  if (p === '/api/inspiration/delete' && req.method === 'POST') {
    try {
      const body = JSON.parse((await readBody(req, 1e5)).toString('utf8'));
      let list = getInspiration();
      const item = list.find(x => x.id === body.id);
      if (item && item.type === 'file' && item.file) { try { fs.unlinkSync(path.join(INSPO_DIR, item.file)); } catch (_) {} }
      list = list.filter(x => x.id !== body.id); saveInspiration(list);
      return sendJSON(res, 200, { inspiration: list });
    } catch (e) { return sendJSON(res, 400, { error: String(e.message || e) }); }
  }

  if (p === '/thumb') {
    const reel = (u.query.reel || '').toString();
    const t = makeThumb(reel);
    if (!t) { res.writeHead(404); return res.end(); }
    return serveStatic(res, t);
  }

  if (p === '/media') {
    const rel = (u.query.path || '').toString();
    const safe = resolveSafe(rel, [REELS_HOME, deliverDir()]);
    if (!safe || !exists(safe)) { res.writeHead(404); return res.end('introuvable'); }
    return streamMedia(req, res, safe);
  }

  if (p === '/inspo') {
    const f = (u.query.file || '').toString().replace(/[^\w.-]/g, '_');
    const full = path.join(INSPO_DIR, f);
    if (!exists(full)) { res.writeHead(404); return res.end(); }
    return streamMedia(req, res, full);
  }

  // --- Statique ---
  let file = p === '/' ? path.join(PUBLIC_DIR, 'index.html') : path.join(PUBLIC_DIR, p.replace(/^\/+/, ''));
  if (!path.resolve(file).startsWith(path.resolve(PUBLIC_DIR))) { res.writeHead(403); return res.end(); }
  return serveStatic(res, file);
}

// ---------------------------------------------------------------------------
// Demarrage : choisir un port libre, ecrire .port
// ---------------------------------------------------------------------------
ensureDirs();
const PORTS = [4178, 4179, 4180, 4181, 4182, 4183];
const PORT_FILE = path.join(HOME_MARK, '.port');

function listen(i) {
  if (i >= PORTS.length) { console.error('Aucun port libre.'); process.exit(1); }
  const port = PORTS[i];
  const server = http.createServer((req, res) => {
    handle(req, res).catch(e => { try { sendJSON(res, 500, { error: String(e.message || e) }); } catch (_) {} });
  });
  server.on('error', err => { if (err.code === 'EADDRINUSE') listen(i + 1); else { console.error(err); process.exit(1); } });
  server.listen(port, '127.0.0.1', () => {
    try { fs.writeFileSync(PORT_FILE, String(port)); } catch (_) {}
    const link = `http://127.0.0.1:${port}`;
    console.log('Studio Montage demarre sur ' + link);
    console.log('Dossier reels : ' + REELS_HOME);
  });
  const cleanup = () => { try { fs.unlinkSync(PORT_FILE); } catch (_) {} process.exit(0); };
  process.on('SIGINT', cleanup);
  process.on('SIGTERM', cleanup);
}
listen(0);
