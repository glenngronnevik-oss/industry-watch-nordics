"use strict";
const params = new URLSearchParams(location.search);
const wantAdmin = params.get("next") === "admin";
if (wantAdmin) document.getElementById("hint").textContent = "Enter the admin PIN to manage access.";
const f = document.getElementById("f"), pin = document.getElementById("pin"),
      err = document.getElementById("err"), go = document.getElementById("go");
pin.addEventListener("input", () => { pin.value = pin.value.replace(/\D/g, ""); err.textContent = ""; });
f.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!pin.value) { err.textContent = "Enter your PIN."; return; }
  go.disabled = true; err.textContent = "";
  try {
    const r = await fetch("/api/login", {method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin", body: JSON.stringify({pin: pin.value})});
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      location.replace(wantAdmin && j.role === "admin" ? "/admin" : "/");
      return;
    }
    err.textContent = r.status === 429 ? "Too many attempts. Wait 15 minutes and try again." :
      r.status === 401 ? "Wrong PIN." : (j.detail || "Sign in failed.");
  } catch { err.textContent = "Network error. Try again."; }
  pin.value = ""; pin.focus(); go.disabled = false;
});
