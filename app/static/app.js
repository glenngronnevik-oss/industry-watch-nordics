"use strict";
const DATA = window.IW_DATA || {updated:new Date().toISOString(),updated_label:"no data",briefing:["The data file could not be loaded from the portal. Check /api/status."],items:[],matrices:[],watchlist:[]};
const fmt = d => new Date(d+"T12:00:00").toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric"});
const esc = s => String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));
const safeUrl = u => /^https?:\/\//i.test(u||"") ? u : "#";
const impactRank = {High:0,Medium:1,Low:2};
const state = {cat:"", sig:"", q:"", prod:"", sort:"impact"};
const RECENT_DAYS = DATA.recent_days || 7;
// Age since the item went into the dashboard ("added"); items without "added" fall back to the news date.
const ageDays = i => (new Date(DATA.updated) - new Date((i.added||i.date)+"T12:00:00"))/864e5;
const isRecent = i => ageDays(i) <= RECENT_DAYS;

(function stamp(){
  const upd = new Date(DATA.updated);
  const ageH = (Date.now()-upd.getTime())/36e5;
  const el = document.getElementById("stamp");
  el.innerHTML = `Last updated <b>${esc(DATA.updated_label)}</b><br>Refreshes weekdays at 06:45 (Oslo)` +
    (ageH>80 ? `<br><span class="stale">Data is ${Math.round(ageH/24)} days old. The scheduled refresh may have failed.</span>`:"") +
    `<br><button class="themebtn" id="tb">Toggle theme</button> <span id="adminlink"></span> <button class="themebtn" id="lo">Sign out</button>`;
  document.getElementById("lo").onclick = async () => {
    await fetch("/api/logout", {method:"POST", credentials:"same-origin"});
    location.replace("/login");
  };
  fetch("/api/me", {credentials:"same-origin"}).then(r=>r.ok?r.json():{}).then(m=>{
    if (m.role === "admin") document.getElementById("adminlink").innerHTML = '<a class="themebtn" href="/admin">Admin</a>';
  }).catch(()=>{});
  document.getElementById("tb").onclick = () => {
    const r = document.documentElement, cur = r.getAttribute("data-theme");
    const dark = cur ? cur==="dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    r.setAttribute("data-theme", dark ? "light" : "dark");
  };
})();

document.getElementById("brief").innerHTML = (DATA.briefing||[]).map(b=>`<li>${esc(b)}</li>`).join("");

const cats = ["PDF editors","Print management","Capture & workflow","Standards & format","Tungsten"];
function renderKpis(){
  const all = {label:"All items", key:"", n:DATA.items.length};
  const tiles = [all, ...cats.map(c=>({label:c,key:c,n:DATA.items.filter(i=>i.category===c).length}))];
  document.getElementById("kpis").innerHTML = tiles.map(t=>
    `<div class="kpi ${state.cat===t.key?"active":""}" data-cat="${esc(t.key)}"><div class="n">${t.n}</div><div class="l">${esc(t.label)}</div></div>`).join("");
  document.querySelectorAll(".kpi").forEach(k=>k.onclick=()=>{state.cat=k.dataset.cat;renderKpis();renderFeed();});
}
function renderChips(){
  const sigs = ["","Threat","Opportunity","Parity","Watch"];
  document.getElementById("sigchips").innerHTML = sigs.map(s=>{
    const n = s? DATA.items.filter(i=>i.signal===s).length : DATA.items.length;
    return `<button class="chip ${state.sig===s?"on":""}" data-s="${s}">${s||"All signals"} (${n})</button>`;}).join("");
  document.querySelectorAll(".chip").forEach(c=>c.onclick=()=>{state.sig=c.dataset.s;renderChips();renderFeed();});
}
const prods = [...new Set(DATA.items.flatMap(i=>i.tungsten_product.split(" / ").map(s=>s.trim())))].sort();
document.getElementById("prod").innerHTML += prods.map(p=>`<option>${esc(p)}</option>`).join("");

function card(i, now){
  const isNew = i.added && (now - new Date(i.added+"T12:00:00"))/864e5 < 3;
  return `<article class="card imp-${esc(i.impact)}">
      <div class="meta">${isNew?'<span class="tag new">New</span>':""}<span class="tag sig-${esc(i.signal)}">${esc(i.signal)}</span>
      <span class="tag">${esc(i.impact)} impact</span><span>${esc(i.category)}</span><span>·</span><span>${esc(i.date_note||fmt(i.date))}</span></div>
      <h3>${esc(i.title)}</h3>
      <p>${esc(i.summary)}</p>
      <div class="angle"><b>Tungsten angle · ${esc(i.tungsten_product)}</b>${esc(i.tungsten_angle)}</div>
      <div class="src">Source: <a href="${esc(safeUrl(i.url))}" target="_blank" rel="noopener noreferrer">${esc(i.source)}</a></div>
    </article>`;
}
function renderFeed(){
  const q = state.q.toLowerCase();
  let items = DATA.items.filter(i=>
    (!state.cat||i.category===state.cat) && (!state.sig||i.signal===state.sig) &&
    (!state.prod||i.tungsten_product.includes(state.prod)) &&
    (!q||[i.title,i.summary,i.vendor,i.tungsten_angle,i.tungsten_product].join(" ").toLowerCase().includes(q)));
  items.sort((a,b)=> state.sort==="impact"
    ? (impactRank[a.impact]-impactRank[b.impact]) || b.date.localeCompare(a.date)
    : b.date.localeCompare(a.date));
  const now = new Date(DATA.updated);
  const recent = items.filter(isRecent), older = items.filter(i=>!isRecent(i));
  document.getElementById("feed-h").textContent = `Latest (added in the last ${RECENT_DAYS} days) (${recent.length})`;
  document.getElementById("feed-old-h").textContent = `Not newest, but relevant (${older.length})`;
  document.getElementById("feed").innerHTML = recent.length ? recent.map(i=>card(i,now)).join("") : `<div class="empty">No recent items match these filters.</div>`;
  document.getElementById("feed-old").innerHTML = older.length ? older.map(i=>card(i,now)).join("") : `<div class="empty">No older items match these filters.</div>`;
}
document.getElementById("q").oninput = e=>{state.q=e.target.value;renderFeed();};
document.getElementById("prod").onchange = e=>{state.prod=e.target.value;renderFeed();};
document.getElementById("sort").onchange = e=>{state.sort=e.target.value;renderFeed();};

document.getElementById("matrices").innerHTML = (DATA.matrices||[]).map(m=>`
  <div class="panel" style="margin-bottom:14px"><strong>${esc(m.title)}</strong>
  <div class="tscroll"><table><thead><tr><th>Capability</th>${m.columns.map(c=>`<th>${esc(c)}</th>`).join("")}</tr></thead>
  <tbody>${m.rows.map(r=>`<tr><td>${esc(r.capability)}</td>${r.cells.map(c=>`<td class="${/^(Not checked|Confirm)/.test(c)?"nc":""}">${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
  <div class="note">${esc(m.note||"")}</div></div>`).join("");

document.getElementById("watch").innerHTML = (DATA.watchlist||[]).sort((a,b)=>a.date.localeCompare(b.date)).map(w=>{
  const days = Math.round((new Date(w.date+"T12:00:00")-new Date())/864e5);
  return `<div class="panel w"><div class="d">${esc(w.label)}</div><div><strong>${esc(w.title)}</strong></div>
  <div class="days">${days>0?`in about ${days} days`:"date reached"}</div><p class="note">${esc(w.why)} <a href="${esc(safeUrl(w.url))}" target="_blank" rel="noopener noreferrer">Source</a></p></div>`;}).join("");

renderKpis();renderChips();renderFeed();
