"use strict";
const post = (url, body) => fetch(url, {method: "POST", credentials: "same-origin",
  headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
const show = (el, text, ok) => { el.textContent = text; el.className = "msg " + (ok ? "ok" : "bad"); };

document.querySelectorAll("form[data-target]").forEach(form => {
  form.querySelectorAll("input").forEach(i => i.addEventListener("input", () => { i.value = i.value.replace(/\D/g, ""); }));
  form.addEventListener("submit", async e => {
    e.preventDefault();
    const msg = form.querySelector(".msg"), v = n => form.elements[n].value;
    if (v("new") !== v("repeat")) return show(msg, "The new PINs do not match.", false);
    const r = await post("/api/admin/pin", {target: form.dataset.target, new_pin: v("new"), current_admin_pin: v("admin")});
    const j = await r.json().catch(() => ({}));
    if (r.status === 401) { location.replace("/login?next=admin"); return; }
    show(msg, r.ok ? `${form.dataset.target === "viewer" ? "Viewer" : "Admin"} PIN changed.` : (j.detail || "Change failed."), r.ok);
    if (r.ok) form.reset();
  });
});

document.getElementById("revoke").onclick = async () => {
  const r = await post("/api/admin/revoke");
  show(document.getElementById("revoke-msg"), r.ok ? "All viewer sessions signed out." : "Failed.", r.ok);
};
document.getElementById("logout").onclick = async () => { await post("/api/logout"); location.replace("/login"); };

fetch("/api/status", {credentials: "same-origin"}).then(r => r.json()).then(s => {
  const rows = [["Data updated", s.updated_label], ["Items", s.items],
    ["Sync", s.sync.enabled ? `${s.sync.source}, last ${s.sync.last_run || "not yet"}: ${s.sync.last_result}` : "off"],
    ["Ingest API", s.ingest_enabled ? `on, last push ${s.last_ingest || "none"}` : "off"]];
  const dl = document.getElementById("status"); dl.textContent = "";
  rows.forEach(([k, v]) => { const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = k; dd.textContent = v; dl.append(dt, dd); });
}).catch(() => {});
