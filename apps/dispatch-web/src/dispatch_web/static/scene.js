import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const STATE_COLOUR = {
  hauling_empty: 0x58a6ff,
  hauling_loaded: 0xe3873c,
  queueing: 0xd29922,
  loading: 0x3fb950,
  dumping: 0xa371f7,
  down: 0xf85149,
  idle: 0x6e7681,
};

export const STATE_LABELS = {
  hauling_empty: "viajando vacío",
  hauling_loaded: "viajando cargado",
  queueing: "en cola",
  loading: "cargando",
  dumping: "descargando",
  down: "fuera de servicio",
  idle: "detenido",
};

export function stateColours() {
  return Object.fromEntries(
    Object.entries(STATE_COLOUR).map(([k, v]) => [k, `#${v.toString(16).padStart(6, "0")}`]),
  );
}

function label(text, colour = "#e6edf3", tier = 0) {
  const pad = 18;
  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  ctx.font = "600 44px ui-sans-serif, system-ui, sans-serif";
  canvas.width = Math.ceil(ctx.measureText(text).width) + pad * 2;
  canvas.height = 72;
  const c2 = canvas.getContext("2d");
  c2.font = "600 44px ui-sans-serif, system-ui, sans-serif";
  c2.fillStyle = "rgba(13,17,23,.78)";
  c2.fillRect(0, 0, canvas.width, canvas.height);
  c2.fillStyle = colour;
  c2.textBaseline = "middle";
  c2.fillText(text, pad, canvas.height / 2 + 2);

  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({
      map: new THREE.CanvasTexture(canvas),
      depthTest: false,
      transparent: true,
      // Constant screen size: a pit is kilometres deep, and distance-scaled
      // labels come out unreadable at one end and enormous at the other.
      sizeAttenuation: false,
    }),
  );
  sprite.scale.set((canvas.width / canvas.height) * 0.035, 0.035, 1);
  // Stacking has to happen in screen space too. Offsetting the anchor in metres
  // looks fine up close and collapses to nothing at the bottom of the pit, where
  // the benches sit a few metres apart and the labels land on top of each other.
  // `center` is measured in sprite heights, which stay constant on screen.
  sprite.center.set(0.5, -0.15 - tier * 1.25);
  sprite.renderOrder = 10;
  return sprite;
}

export class MineScene {
  constructor(canvas) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0e1116);
    // Fog is set once the mine is loaded and its real extent is known: a pit is
    // kilometres wide, so a fixed near plane swallows the whole scene.
    this.scene.fog = null;

    this.camera = new THREE.PerspectiveCamera(50, 1, 1, 30000);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.maxPolarAngle = Math.PI * 0.49;

    const sun = new THREE.DirectionalLight(0xffffff, 2.1);
    sun.position.set(1200, 2200, 900);
    this.scene.add(sun, new THREE.HemisphereLight(0x9fb6d0, 0x2a2118, 1.5));

    this.world = new THREE.Group();
    this.scene.add(this.world);
    this.trucks = new Map();
    this.shovels = new Map();
    this.movements = new Map();

    this.viewport = canvas.parentElement;
    new ResizeObserver(() => this.resize()).observe(this.viewport);
    this.resize();
  }

  resize() {
    const { clientWidth: w, clientHeight: h } = this.viewport;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  clear() {
    this.world.clear();
    this.trucks.clear();
    this.shovels.clear();
    this.movements.clear();
  }

  load(data) {
    this.clear();
    const at = (node) => new THREE.Vector3(...data.nodes[node]);

    const bounds = new THREE.Box3();
    Object.values(data.nodes).forEach((p) => bounds.expandByPoint(new THREE.Vector3(...p)));
    const centre = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3());
    const reach = Math.max(size.x, size.z, 1500);

    // Equipment drawn to scale on a four-kilometre pit would be a few pixels, so
    // it is sized against the mine's extent instead. The roads and elevations
    // stay true — only the machines are exaggerated, as in any mine plan.
    const unit = reach / 60;

    const grid = new THREE.GridHelper(reach * 1.05, 18, 0x2a3340, 0x171d25);
    grid.position.set(centre.x, bounds.min.y - unit * 1.5, centre.z);
    this.world.add(grid);

    // Roads: a tube per segment, so grade is visible as the ramp climbs.
    const road = new THREE.MeshStandardMaterial({ color: 0x4a5462, roughness: 0.95 });
    const seen = new Set();
    for (const edge of data.edges) {
      const key = [edge.from, edge.to].sort().join("|");
      if (seen.has(key)) continue;
      seen.add(key);
      const curve = new THREE.CatmullRomCurve3([at(edge.from), at(edge.to)]);
      this.world.add(
        new THREE.Mesh(new THREE.TubeGeometry(curve, 12, unit * 0.34, 6, false), road),
      );
    }

    const floor = bounds.min.y - unit * 1.5;
    const dropLine = new THREE.LineBasicMaterial({ color: 0x2f3a47 });
    for (const node of Object.keys(data.nodes)) {
      const point = at(node);
      const pad = new THREE.Mesh(
        new THREE.CylinderGeometry(unit * 0.9, unit * 1.1, unit * 0.18, 20),
        new THREE.MeshStandardMaterial({ color: 0x39424f, roughness: 1 }),
      );
      pad.position.copy(point).add(new THREE.Vector3(0, unit * 0.09, 0));
      // A plumb line down to the grid: without it the pit reads as flat, since
      // a hundred and fifty metres of depth is nothing against four kilometres.
      this.world.add(
        pad,
        new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([
            point.clone(),
            new THREE.Vector3(point.x, floor, point.z),
          ]),
          dropLine,
        ),
      );
    }

    data.shovels.forEach((shovel, index) => {
      const colour = shovel.is_ore ? 0xd9a441 : 0x7d8590;
      const base = at(shovel.node);
      const body = new THREE.Mesh(
        new THREE.BoxGeometry(unit * 1.7, unit * 1.1, unit * 1.7),
        new THREE.MeshStandardMaterial({ color: colour, roughness: 0.6, metalness: 0.15 }),
      );
      body.position.copy(base).add(new THREE.Vector3(0, unit * 0.7, 0));
      const boom = new THREE.Mesh(
        new THREE.BoxGeometry(unit * 2.6, unit * 0.34, unit * 0.34),
        new THREE.MeshStandardMaterial({ color: 0xf0c674, roughness: 0.5 }),
      );
      boom.position.copy(base).add(new THREE.Vector3(unit * 1.25, unit * 1.5, 0));
      boom.rotation.z = -0.42;
      const tag = label(
        `${shovel.id} · ${shovel.material}`,
        shovel.is_ore ? "#f0c674" : "#c9d1d9",
        index,
      );
      tag.position.copy(base).add(new THREE.Vector3(0, unit * 2.4, 0));
      this.world.add(body, boom, tag);
      this.shovels.set(shovel.id, { body, boom, tag, colour, unit });
    });

    data.dumps.forEach((dump, index) => {
      const base = at(dump.node);
      const mesh = dump.accepts_ore
        ? new THREE.Mesh(
            new THREE.CylinderGeometry(unit * 1.4, unit * 2.0, unit * 2.0, 18),
            new THREE.MeshStandardMaterial({ color: 0x4d7ea8, roughness: 0.5, metalness: 0.3 }),
          )
        : new THREE.Mesh(
            new THREE.ConeGeometry(unit * 2.3, unit * 1.7, 20),
            new THREE.MeshStandardMaterial({ color: 0x6b6357, roughness: 1 }),
          );
      mesh.position.copy(base).add(new THREE.Vector3(0, unit, 0));
      const tag = label(dump.id, dump.accepts_ore ? "#79c0ff" : "#c9d1d9", index);
      tag.position.copy(base).add(new THREE.Vector3(0, unit * 2.6, 0));
      this.world.add(mesh, tag);
    });

    const start = centre.clone();
    for (const truck of data.trucks) {
      const group = new THREE.Group();
      const chassis = new THREE.Mesh(
        new THREE.BoxGeometry(unit * 1.5, unit * 0.45, unit * 0.75),
        new THREE.MeshStandardMaterial({ color: 0x30363d, roughness: 0.8 }),
      );
      const tipper = new THREE.Mesh(
        new THREE.BoxGeometry(unit * 1.1, unit * 0.6, unit * 0.82),
        new THREE.MeshStandardMaterial({ color: STATE_COLOUR.idle, roughness: 0.55 }),
      );
      tipper.position.set(-unit * 0.15, unit * 0.52, 0);
      group.add(chassis, tipper);
      group.position.copy(start);
      this.world.add(group);
      this.trucks.set(truck.id, { group, tipper, position: start.clone() });
    }

    for (const movement of data.movements) {
      const list = this.movements.get(movement.truck) ?? [];
      const points = movement.path.map((p) => new THREE.Vector3(...p));
      const spans = [];
      let total = 0;
      for (let i = 1; i < points.length; i += 1) {
        const span = points[i].distanceTo(points[i - 1]);
        spans.push(span);
        total += span;
      }
      list.push({ t0: movement.t0, t1: movement.t1, points, spans, total });
      this.movements.set(movement.truck, list);
    }
    for (const list of this.movements.values()) list.sort((a, b) => a.t0 - b.t0);

    this.frame(bounds);
  }

  frame(bounds) {
    const centre = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3());

    // A pit is a long diagonal, so look at it across its long axis and spend the
    // wider horizontal field of view on it — framing against the vertical one
    // either crops the ends or leaves half the screen empty.
    const along = new THREE.Vector3(size.x, 0, size.z).normalize();
    const across = new THREE.Vector3(-along.z, 0, along.x);
    const span = Math.hypot(size.x, size.z);

    const vFov = (this.camera.fov * Math.PI) / 180;
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * this.camera.aspect);
    const distance = Math.max(
      span * 0.72 / Math.tan(hFov / 2),
      (size.y + span * 0.16) * 0.7 / Math.tan(vFov / 2),
    );

    // Low enough that the ramp reads as a climb: the expensive leg of the cycle
    // is invisible from straight above.
    const elevation = 0.42;
    this.camera.position
      .copy(centre)
      .addScaledVector(across, distance * Math.cos(elevation))
      .addScaledVector(new THREE.Vector3(0, 1, 0), distance * Math.sin(elevation));
    this.scene.fog = new THREE.Fog(0x0e1116, distance * 1.2, distance * 4.0);
    this.controls.target.copy(centre);
    this.controls.update();
  }

  setTime(seconds, states, shovelStates) {
    // Blink against the wall clock, not simulated time: at 600x a sim-second
    // blink is a 100 Hz strobe that reads as a rendering fault.
    const blink = Math.floor(performance.now() / 450) % 2 === 0;

    for (const [id, shovel] of this.shovels) {
      const down = shovelStates?.get(id) === "down";
      shovel.body.material.color.setHex(down ? STATE_COLOUR.down : shovel.colour);
      shovel.boom.visible = !down || blink;
    }

    for (const [id, truck] of this.trucks) {
      const list = this.movements.get(id) ?? [];
      let placed = false;
      for (let i = list.length - 1; i >= 0; i -= 1) {
        const move = list[i];
        if (seconds >= move.t1) {
          truck.position.copy(move.points[move.points.length - 1]);
          placed = true;
          break;
        }
        if (seconds >= move.t0) {
          truck.position.copy(pointAlong(move, (seconds - move.t0) / (move.t1 - move.t0 || 1)));
          placed = true;
          break;
        }
      }
      if (!placed && list.length) truck.position.copy(list[0].points[0]);
      truck.group.position.lerp(truck.position, 0.5);

      const state = states.get(id) ?? "idle";
      truck.tipper.material.color.setHex(STATE_COLOUR[state] ?? STATE_COLOUR.idle);
      truck.group.visible = state !== "down" || blink;
    }
  }

  render() {
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }
}

function pointAlong(move, fraction) {
  const target = move.total * Math.max(0, Math.min(1, fraction));
  let walked = 0;
  for (let i = 0; i < move.spans.length; i += 1) {
    if (walked + move.spans[i] >= target) {
      const local = move.spans[i] ? (target - walked) / move.spans[i] : 0;
      return move.points[i].clone().lerp(move.points[i + 1], local);
    }
    walked += move.spans[i];
  }
  return move.points[move.points.length - 1].clone();
}
