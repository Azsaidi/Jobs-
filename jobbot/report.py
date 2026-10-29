"""Static HTML dashboard of every tracked application."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .db import STATUSES, Tracker
from .util import now_iso

_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Job Applications</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e4e4e0;--accent:#1f5f8b}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1d1d20;--fg:#ececee;--muted:#9a9aa2;--line:#2c2c31;--accent:#6fb3e0}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted);margin-bottom:20px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:10px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;cursor:pointer}
.card b{display:block;font-size:22px}.card span{color:var(--muted);font-size:12px;text-transform:capitalize}
.card.active{outline:2px solid var(--accent)}
input{width:100%;padding:9px 12px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);margin-bottom:12px}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:760px}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600}td.num{font-variant-numeric:tabular-nums}
a{color:var(--accent)}.pill{display:inline-block;padding:1px 8px;border-radius:99px;border:1px solid var(--line);font-size:12px;white-space:nowrap}
.note{color:var(--muted);font-size:12px;max-width:340px}
</style></head><body><main>
<h1>Job applications</h1><div class="sub">Generated __GENERATED__ &middot; __SEEN__ postings checked</div>
<div class="cards" id="cards"></div>
<input id="q" placeholder="Filter by company, title, status...">
<div class="wrap"><table><thead><tr><th>#</th><th>Role</th><th>Company</th><th>Status</th><th>Fit</th><th>ATS before &rarr; after</th><th>Applied</th><th>Notes</th></tr></thead>
<tbody id="rows"></tbody></table></div>
</main>
<script>
const DATA = __DATA__; const STATUSES = __STATUSES__;
let active = null;
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function render(){
  const q = document.getElementById('q').value.toLowerCase();
  const counts = {}; DATA.forEach(a => counts[a.status] = (counts[a.status]||0)+1);
  document.getElementById('cards').innerHTML = [['all', DATA.length], ...STATUSES.filter(s => counts[s]).map(s => [s, counts[s]])]
    .map(([s,n]) => `<div class="card ${active===s||(!active&&s==='all')?'active':''}" data-s="${s}"><b>${n}</b><span>${s.replace('_',' ')}</span></div>`).join('');
  document.querySelectorAll('.card').forEach(c => c.onclick = () => { active = c.dataset.s==='all'?null:c.dataset.s; render(); });
  document.getElementById('rows').innerHTML = DATA.filter(a => (!active || a.status===active) &&
      (`${a.title} ${a.company} ${a.status} ${a.location}`.toLowerCase().includes(q)))
    .map(a => `<tr><td class="num">${a.id}</td><td><a href="${esc(a.url)}" target="_blank" rel="noopener">${esc(a.title)}</a><div class="note">${esc(a.location)}</div></td>
      <td>${esc(a.company)}</td><td><span class="pill">${esc(a.status.replace('_',' '))}</span></td>
      <td class="num">${a.llm_score ?? (a.match_score != null ? Math.round(a.match_score) : '')}</td>
      <td class="num">${a.ats_before ?? ''} &rarr; ${a.ats_after ?? ''}</td><td>${esc((a.applied_at||'').slice(0,10))}</td>
      <td class="note">${esc(a.notes||'')}</td></tr>`).join('');
}
document.getElementById('q').oninput = render; render();
</script></body></html>
"""


def build_dashboard(tracker: Tracker, path: Path) -> Path:
    apps = tracker.applications()
    keep = ("id", "title", "company", "location", "status", "llm_score", "match_score", "ats_before", "ats_after",
            "applied_at", "notes", "url")
    data = [{k: a.get(k) for k in keep} for a in apps]
    page = (_TEMPLATE.replace("__DATA__", json.dumps(data).replace("</", "<\\/"))
            .replace("__STATUSES__", json.dumps(STATUSES))
            .replace("__GENERATED__", html.escape(now_iso()))
            .replace("__SEEN__", str(tracker.stats().get("jobs_seen", 0))))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
