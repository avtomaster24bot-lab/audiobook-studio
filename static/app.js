const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const API = window.API_BASE || '';
let BOOK = null, VOICES = [], EDITS = {}, POLL = null;

const fmtTime = s => s == null ? '—' : (s >= 60 ? `${Math.floor(s / 60)} мин ${Math.round(s % 60)} с` : `${Math.round(s)} с`);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

async function api(path, opts = {}) {
  const r = await fetch(API + path, opts);
  const ct = r.headers.get('content-type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw (data && data.error) ? data.error : {message: (data && data.detail) || `HTTP ${r.status}`};
  return data;
}

// вкладки источника
let TAB = 'query';
$$('.tab').forEach(b => b.onclick = () => {
  TAB = b.dataset.tab;
  $$('.tab').forEach(x => x.classList.toggle('active', x === b));
  $$('.pane').forEach(p => p.classList.toggle('active', p.dataset.pane === TAB));
});

$('#srcForm').onsubmit = async e => {
  e.preventDefault();
  const f = e.target, fd = new FormData();
  if (TAB === 'query') { fd.append('title', f.title.value); fd.append('author', f.author.value); }
  if (TAB === 'url') fd.append('url', f.url.value);
  if (TAB === 'isbn') fd.append('isbn', f.isbn.value);
  if (TAB === 'file') { if (!f.file.files[0]) return alert('Выберите файл'); fd.append('file', f.file.files[0]); }
  if ($('#useLlm').checked) fd.append('use_llm', 'true');
  $('#resolveBtn').disabled = true;
  $('#resolveStatus').textContent = 'Ищу текст, чищу и размечаю… (до минуты)';
  try {
    BOOK = await api('/api/books/resolve', {method: 'POST', body: fd});
    EDITS = {};
    await renderPreview();
    $('#resolveStatus').textContent = '';
  } catch (err) {
    $('#resolveStatus').innerHTML = `<div class="err">${esc(err.message)}${(err.suggestions || []).length ? '<br>Похожие: ' + err.suggestions.map(esc).join('; ') : ''}</div>`;
  } finally { $('#resolveBtn').disabled = false; }
};

async function renderPreview() {
  if (!VOICES.length) VOICES = await api('/api/voices');
  const b = BOOK;
  $('#preview').classList.remove('hidden');
  $('#bookMeta').innerHTML = `<span><b>${esc(b.title)}</b></span><span>${esc(b.author)}</span>
    <span>${b.words.toLocaleString('ru')} слов · ${b.chapters.length} гл. · ≈ ${b.estimated_minutes} мин аудио</span>
    <span>разметка: ${b.analysis_method === 'llm' ? 'LLM' : 'правила'}</span>
    ${b.source_url ? `<a href="${esc(b.source_url)}" target="_blank">источник</a>` : ''}<span>${esc(b.license_note)}</span>`;
  $('#firstPar').textContent = b.first_paragraph;
  $('#warnings').innerHTML = (b.warnings || []).map(w => `<div class="warn">${esc(w)}</div>`).join('');
  $('#chapters').innerHTML = b.chapters.map(c => `<label><input type="checkbox" class="chap" value="${c.index}" checked> ${esc(c.title)} <span class="hint">${c.words} сл.</span></label>`).join('');
  const roles = [{name: 'narrator', lines: '—', title: 'Рассказчик'}, ...b.characters.map(c => ({...c, title: c.name}))];
  $('#voiceRows').innerHTML = roles.map(r => `<tr><td>${esc(r.title)}${r.gender ? ` <span class="hint">${r.gender === 'female' ? 'ж' : 'м'}</span>` : ''}</td>
    <td>${r.lines}</td><td><select class="voiceSel" data-role="${esc(r.name)}">${VOICES.map(v =>
      `<option value="${v.id}" ${b.voices[r.name] === v.id ? 'selected' : ''}>${esc(v.title)} — ${esc(v.description)}</option>`).join('')}</select></td>
    <td><button class="play" type="button" title="Прослушать">▶</button></td></tr>`).join('');
  $$('#voiceRows tr').forEach(tr => {
    tr.querySelector('.play').onclick = () => {
      const vid = tr.querySelector('select').value;
      new Audio(`${API}/api/voices/${vid}/preview`).play();
    };
  });
  loadSegments();
  $('#preview').scrollIntoView({behavior: 'smooth'});
}

async function loadSegments() {
  const data = await api(`/api/books/${BOOK.book_id}/segments?chapter=0`);
  const names = ['narrator', ...BOOK.characters.map(c => c.name)];
  $('#segments').innerHTML = data.segments.map(s => s.kind === 'dialogue'
    ? `<div class="seg"><select data-key="0:${s.i}">${[...new Set([...names, s.speaker])].map(n => `<option ${n === s.speaker ? 'selected' : ''}>${esc(n)}</option>`).join('')}</select>
       <div>— ${esc(s.text)} <span class="hint">[${esc(s.emotion)} · ${esc(s.attributed_by)}]</span></div></div>`
    : `<div class="seg n">${esc(s.text.slice(0, 220))}${s.text.length > 220 ? '…' : ''}</div>`).join('');
  $$('#segments select').forEach(sel => sel.onchange = () => { EDITS[sel.dataset.key] = sel.value; });
}

$('#saveEdits').onclick = async () => {
  BOOK = await api(`/api/books/${BOOK.book_id}/analysis`, {method: 'PATCH', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({speakers: EDITS, voices: selectedVoices()})});
  EDITS = {};
  renderPreview();
};

const selectedVoices = () => Object.fromEntries($$('.voiceSel').map(s => [s.dataset.role, s.value]));
['rate', 'pitch', 'musicGain'].forEach(id => $('#' + id).oninput = e => {
  const v = e.target.value;
  $({rate: '#rateOut', pitch: '#pitchOut', musicGain: '#musicOut'}[id]).textContent =
    id === 'musicGain' ? `${v} dB` : id === 'rate' ? Number(v).toFixed(2) : v;
});

async function startJob(preview) {
  const settings = {
    preview, voices: selectedVoices(),
    chapters: $$('.chap').filter(c => c.checked).map(c => +c.value),
    rate: +$('#rate').value, pitch: +$('#pitch').value,
    music: $('#musicOn').checked, music_gain_db: +$('#musicGain').value,
    formats: $$('input[name=fmt]').filter(c => c.checked).map(c => c.value),
  };
  const body = {book_id: BOOK.book_id, settings, webhook_url: $('#webhook').value || null};
  const job = await api('/api/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  showJob(job);
}
$('#previewBtn').onclick = () => startJob(true).catch(e => alert(e.message));
$('#generateBtn').onclick = () => startJob(false).catch(e => alert(e.message));

function showJob(job) {
  $('#jobCard').classList.remove('hidden');
  $('#jobCard').scrollIntoView({behavior: 'smooth'});
  clearInterval(POLL);
  const tick = async () => {
    const j = await api(`/api/jobs/${job.id}`);
    $('#bar').style.width = `${Math.round(j.progress * 100)}%`;
    $('#jobStage').textContent = `${j.stage || ''} · ${Math.round(j.progress * 100)}%` + (j.eta_sec ? ` · осталось ≈ ${fmtTime(j.eta_sec)}` : '');
    if (j.status === 'done' || j.status === 'failed') { clearInterval(POLL); renderResult(j); loadLibrary(); }
  };
  tick(); POLL = setInterval(tick, 1500);
}

function renderResult(j) {
  if (j.status === 'failed') { $('#jobResult').innerHTML = `<div class="err">${esc(j.error?.message)}</div>`; return; }
  const r = j.result, main = r.files.mp3 || r.files.m4b;
  const d = r.chapters[0]?.ducking;
  $('#jobResult').innerHTML = `${main ? `<audio controls src="${API + main}"></audio>` : ''}
    <div class="downloads">${Object.entries(r.files).map(([k, u]) => `<a href="${API + u}" download>⬇ ${k.toUpperCase()}</a>`).join('')}
      ${r.chapters.length > 1 ? r.chapters.map((c, i) => c.url ? `<a href="${API + c.url}" download>Глава ${i + 1}</a>` : '').join('') : ''}</div>
    <p class="metrics">Длительность ${fmtTime(r.duration_sec)} · обработка ${fmtTime(r.elapsed_sec)} · громкость ${r.loudness?.integrated_lufs} LUFS
      ${d ? ` · голос громче музыки под речью на ${d.voice_minus_music_db} dB (приглушение ${d.ducking_depth_db} dB)` : ''}</p>
    ${(r.warnings || []).map(w => `<div class="warn">${esc(w)}</div>`).join('')}`;
}

async function loadLibrary() {
  const jobs = await api('/api/jobs');
  if (!jobs.length) return;
  $('#jobs').innerHTML = jobs.map(j => `<div class="job"><div><b>${esc(j.book?.title || 'Книга')}</b> <span class="hint">${esc(j.book?.author || '')}</span>
      ${j.preview ? '<span class="badge">превью</span>' : ''}<br><span class="hint">${new Date(j.created_at * 1000).toLocaleString('ru')}</span></div>
      <div><span class="badge ${j.status}">${({queued: 'в очереди', running: 'идёт', done: 'готово', failed: 'ошибка'})[j.status]}</span>
      ${j.result?.files?.mp3 ? ` <a href="${API + j.result.files.mp3}" target="_blank">MP3</a>` : ''}
      ${j.result?.files?.m4b ? ` <a href="${API + j.result.files.m4b}">M4B</a>` : ''}</div></div>`).join('');
}
loadLibrary().catch(() => {});
