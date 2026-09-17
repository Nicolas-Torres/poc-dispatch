import { MineScene, STATE_LABELS, stateColours } from "./scene.js";

const el = (id) => document.getElementById(id);
const scene = new MineScene(el("canvas"));

let data = null;
let simTime = 0;
let playing = false;
let cursor = 0;
let accumulated = null;

const fmt = (n, digits = 0) =>
  n.toLocaleString("es", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const clock = (s) =>
  `${String(Math.floor(s / 3600)).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}`;

async function boot() {
  const meta = await (await fetch("/api/demos")).json();
  el("demos").innerHTML = meta.demos
    .map(
      (d) =>
        `<button class="demo" data-id="${d.id}" aria-pressed="false"><b>${d.title}</b><span>${d.blurb}</span></button>`,
    )
    .join("");
  el("scenario").innerHTML = meta.scenarios.map((s) => `<option>${s}</option>`).join("");
  el("policy").innerHTML = meta.policies.map((p) => `<option>${p}</option>`).join("");

  el("demos").addEventListener("click", (event) => {
    const button = event.target.closest(".demo");
    if (!button) return;
    const demo = meta.demos.find((d) => d.id === button.dataset.id);
    document.querySelectorAll(".demo").forEach((b) => b.setAttribute("aria-pressed", b === button));
    el("scenario").value = demo.scenario;
    el("policy").value = demo.policy;
    el("hours").value = demo.hours;
    run();
  });

  el("legend").innerHTML = Object.entries(stateColours())
    .map(
      ([state, colour]) =>
        `<div><i class="dot" style="background:${colour}"></i>${STATE_LABELS[state]}</div>`,
    )
    .join("");

  el("run").addEventListener("click", run);
  el("play").addEventListener("click", () => setPlaying(!playing));
  el("scrub").addEventListener("input", () => {
    if (!data) return;
    seek((el("scrub").value / 1000) * data.duration_s);
  });
  el("demos").querySelector(".demo")?.click();
}

async function run() {
  el("run").disabled = true;
  el("overlay").hidden = false;
  el("overlay").textContent = "Simulando…";
  setPlaying(false);
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        scenario: el("scenario").value,
        policy: el("policy").value,
        hours: Number(el("hours").value),
      }),
    });
    if (!response.ok) throw new Error(await response.text());
    data = await response.json();
    scene.load(data);
    seek(0);
    el("overlay").hidden = true;
    setPlaying(true);
  } catch (error) {
    el("overlay").textContent = `No se pudo simular: ${error.message}`;
  } finally {
    el("run").disabled = false;
  }
}

function setPlaying(next) {
  playing = next;
  el("play").textContent = playing ? "❚❚" : "▶";
}

function seek(seconds) {
  simTime = Math.max(0, Math.min(seconds, data ? data.duration_s : 0));
  cursor = 0;
  accumulated = freshTotals();
  advanceTotals();
  paint();
}

function freshTotals() {
  return {
    tonnes: 0,
    cycles: 0,
    value: 0,
    byDump: new Map(),
    byShovel: new Map(),
    blend: new Map(),
    states: new Map(),
    shovelStates: new Map(),
  };
}

function advanceTotals() {
  const events = data.events;
  while (cursor < events.length && events[cursor].t <= simTime) {
    const event = events[cursor];
    const totals = accumulated;
    if (event.kind === "dump_end") {
      totals.tonnes += event.payload_t;
      totals.cycles += 1;
      totals.value +=
        event.payload_t * (data.values[event.zone] ?? 1) * (data.dump_values[event.dump] ?? 1);
      totals.byDump.set(event.dump, (totals.byDump.get(event.dump) ?? 0) + event.payload_t);
      const zone = data.zones[event.zone];
      for (const [element, grade] of Object.entries(zone?.grades ?? {})) {
        const key = `${event.dump}|${element}`;
        const row = totals.blend.get(key) ?? { tonnes: 0, graded: 0 };
        row.tonnes += event.payload_t;
        row.graded += event.payload_t * grade;
        totals.blend.set(key, row);
      }
    } else if (event.kind === "load_end") {
      const row = totals.byShovel.get(event.shovel) ?? { loads: 0, tonnes: 0 };
      row.loads += 1;
      row.tonnes += event.payload_t;
      totals.byShovel.set(event.shovel, row);
    }
    cursor += 1;
  }
  for (const entry of data.states) {
    if (entry.t > simTime) break;
    accumulated.states.set(entry.truck, entry.state);
  }
  for (const entry of data.shovel_states ?? []) {
    if (entry.t > simTime) break;
    accumulated.shovelStates.set(entry.shovel, entry.state);
  }
}

function paint() {
  const totals = accumulated;
  const hours = Math.max(simTime / 3600, 1e-6);
  el("tonnes").innerHTML = `${fmt(totals.tonnes)} <small>t volteadas</small>`;
  el("tph").textContent = simTime > 60 ? `${fmt(totals.tonnes / hours)} t/h` : "—";
  el("cycles").textContent = fmt(totals.cycles);
  el("value").textContent = fmt(totals.value);
  el("clock").textContent = `${clock(simTime)} / ${clock(data.duration_s)}`;
  el("scrub").value = (simTime / data.duration_s) * 1000;

  el("blends").innerHTML =
    (data.blend_targets ?? [])
      .map((target) => {
        const row = totals.blend.get(`${target.dump}|${target.element}`);
        if (!row || !row.tonnes) return `<div class="kpi"><span>${target.dump}</span><b>—</b></div>`;
        const grade = row.graded / row.tonnes;
        const low = target.min ?? -Infinity;
        const high = target.max ?? Infinity;
        const ok = grade >= low - 1e-9 && grade <= high + 1e-9;
        return `<div class="kpi"><span>${target.dump} · ${target.element}</span>
          <b>${grade.toFixed(3)} <span class="chip ${ok ? "ok" : "bad"}">${ok ? "en spec" : "fuera"}</span></b></div>
          <div class="kpi"><span style="font-size:11px">ventana</span>
          <b style="font-weight:400;color:var(--dim)">${target.min ?? "—"} – ${target.max ?? "—"}</b></div>`;
      })
      .join("") || `<div class="kpi"><span>sin ventana declarada</span><b>—</b></div>`;

  el("shovels").innerHTML =
    `<tr><th>pala</th><th>cargas</th><th>t</th></tr>` +
    data.shovels
      .map((shovel) => {
        const row = totals.byShovel.get(shovel.id) ?? { loads: 0, tonnes: 0 };
        const down = totals.shovelStates.get(shovel.id) === "down";
        const name = down ? `${shovel.id} <span class="chip bad">parada</span>` : shovel.id;
        return `<tr><td>${name}</td><td>${row.loads}</td><td>${fmt(row.tonnes)}</td></tr>`;
      })
      .join("");

  el("dumps").innerHTML =
    `<tr><th>destino</th><th>t</th></tr>` +
    data.dumps
      .map(
        (dump) =>
          `<tr><td>${dump.id}</td><td>${fmt(totals.byDump.get(dump.id) ?? 0)}</td></tr>`,
      )
      .join("");
}

let last = performance.now();
function frame(now) {
  const delta = Math.min((now - last) / 1000, 0.1);
  last = now;
  if (playing && data) {
    simTime += delta * Number(el("speed").value);
    if (simTime >= data.duration_s) {
      simTime = data.duration_s;
      setPlaying(false);
    }
    advanceTotals();
    paint();
  }
  if (data) scene.setTime(simTime, accumulated.states, accumulated.shovelStates);
  scene.render();
  requestAnimationFrame(frame);
}

requestAnimationFrame(frame);
boot();
