"use strict";

// En la app Tauri la página no la sirve el motor: hay que apuntar al puerto.
// En el navegador (servida por el motor) basta con rutas relativas.
const API = location.protocol.startsWith("http") ? "" : "http://127.0.0.1:8765";
const $ = (id) => document.getElementById(id);

async function api(ruta, datos) {
  const opciones = datos === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Forja": "1" },
    body: JSON.stringify(datos),
  };
  const r = await fetch(API + ruta, opciones);
  const cuerpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(cuerpo.error || `Error ${r.status}`);
  return cuerpo;
}

// --- estado del motor ---------------------------------------------------------

async function refrescarEstado() {
  try {
    const e = await api("/api/estado");
    const gpu = $("estado-gpu");
    if (e.gpu && e.gpu.gpu) {
      gpu.textContent = `${e.gpu.gpu} · ${e.gpu.vram_libre_gb}/${e.gpu.vram_total_gb} GB libres`;
      gpu.className = "pastilla ok";
    } else {
      gpu.textContent = "Sin GPU: la IA irá muy lenta";
      gpu.title = (e.gpu && e.gpu.motivo) || "";
      gpu.className = "pastilla aviso";
    }
    const faltan = e.modelos.filter((m) => !m.descargado).map((m) => m.alias);
    const mod = $("estado-modelos");
    if (faltan.length) {
      mod.textContent = `Faltan modelos: ${faltan.join(", ")}`;
      mod.className = "pastilla error";
    } else {
      mod.textContent = e.modelos_cargados ? "Modelos en memoria" : "Modelos en disco (VRAM libre)";
      mod.className = "pastilla";
    }
    $("btn-liberar").disabled = !e.modelos_cargados;
  } catch {
    $("estado-gpu").textContent = "Motor desconectado";
    $("estado-gpu").className = "pastilla error";
  }
}

$("btn-liberar").addEventListener("click", async () => {
  await api("/api/liberar", {});
  refrescarEstado();
});

// --- ajustes (Gemini, Qwen) ---------------------------------------------------------

let configGemini = { configurada: false };

function mostrarConfig(c) {
  configGemini = c.gemini;
  const g = c.gemini;
  $("gemini-estado").textContent = g.configurada
    ? `Clave configurada (termina en …${g.terminacion})${g.desde_entorno ? " desde la variable GEMINI_API_KEY" : ""}. Guardada en ${c.archivo}.`
    : "No hay clave configurada.";
  const opcion = $("motor-ia").querySelector('option[value="gemini"]');
  opcion.disabled = !g.configurada;
  opcion.textContent = g.configurada ? "Gemini (API externa, de pago)" : "Gemini (configura la clave en Ajustes)";
  if (!g.configurada && $("motor-ia").value === "gemini") { $("motor-ia").value = "local"; aplicarMotor(); }
  if (g.modelo && ![...$("gemini-modelo").options].some((o) => o.value === g.modelo)) {
    $("gemini-modelo").add(new Option(g.modelo, g.modelo));
  }
  if (g.modelo) $("gemini-modelo").value = g.modelo;
  const q = c.qwen;
  $("qwen-estado").textContent = `Servidor: ${q.url}${q.desde_entorno ? " (desde la variable QWEN_URL)" : ""}.`;
  if (document.activeElement !== $("qwen-url")) $("qwen-url").value = q.url;
}

async function cargarConfig() { mostrarConfig(await api("/api/config")); }

// Dos talleres (personajes y mapas); Ajustes ocupa la pantalla en lugar del taller activo.
let tallerActivo = "personajes";
function elegirTaller(nombre) {
  tallerActivo = nombre;
  try { localStorage.setItem("forja.taller", nombre); } catch { /* sin almacenamiento */ }
  for (const t of ["personajes", "mapas"]) $(`tab-taller-${t}`).setAttribute("aria-selected", String(t === nombre));
  if (!$("ajustes").hidden) cerrarAjustes();
  $("taller-personajes").hidden = nombre !== "personajes";
  $("taller-mapas").hidden = nombre !== "mapas";
  if (nombre === "mapas") aplicarProyectoMapa();
}
$("tab-taller-personajes").addEventListener("click", () => elegirTaller("personajes"));
$("tab-taller-mapas").addEventListener("click", () => elegirTaller("mapas"));

function abrirAjustes(pestana) {
  $("ajustes").hidden = false;
  for (const t of ["personajes", "mapas"]) $(`tab-taller-${t}`).setAttribute("aria-selected", "false");
  $("taller-personajes").hidden = true;
  $("taller-mapas").hidden = true;
  $("btn-ajustes").textContent = "Volver al taller";
  $("btn-ajustes").setAttribute("aria-expanded", "true");
  if (pestana) elegirPestana(pestana);
  window.scrollTo(0, 0);
}
function cerrarAjustes() {
  $("ajustes").hidden = true;
  for (const t of ["personajes", "mapas"]) $(`tab-taller-${t}`).setAttribute("aria-selected", String(t === tallerActivo));
  $("taller-personajes").hidden = tallerActivo !== "personajes";
  $("taller-mapas").hidden = tallerActivo !== "mapas";
  $("btn-ajustes").textContent = "Ajustes";
  $("btn-ajustes").setAttribute("aria-expanded", "false");
}
function elegirPestana(nombre) {
  for (const tab of document.querySelectorAll('.pestanas [role="tab"]')) {
    const activa = tab.id === `tab-${nombre}`;
    tab.setAttribute("aria-selected", String(activa));
    $(tab.getAttribute("aria-controls")).hidden = !activa;
  }
  if (nombre === "estilos") pintarListaEstilos();
}
$("btn-ajustes").addEventListener("click", () => ($("ajustes").hidden ? abrirAjustes() : cerrarAjustes()));
for (const tab of document.querySelectorAll('.pestanas [role="tab"]')) {
  tab.addEventListener("click", () => elegirPestana(tab.id.slice(4)));
}
document.addEventListener("click", (ev) => {
  const b = ev.target.closest(".ir-proyectos, .ir-plantillas, .ir-estilos");
  if (!b) return;
  const pestana = b.classList.contains("ir-proyectos") ? "proyectos"
    : b.classList.contains("ir-plantillas") ? "plantillas" : "estilos";
  if (pestana === "proyectos") {
    editarProyecto(tallerActivo === "mapas" ? $("mp-proyecto").value : $("proyecto").value);
  }
  abrirAjustes(pestana);
});
$("gemini-clave").addEventListener("input", () => { $("error-clave").hidden = true; });

$("btn-guardar-clave").addEventListener("click", async () => {
  const clave = $("gemini-clave").value.trim();
  if (!clave) {
    $("error-clave").textContent = "Pega una clave antes de guardarla.";
    $("error-clave").hidden = false;
    return;
  }
  try {
    mostrarConfig(await api("/api/config", { gemini_api_key: clave }));
    $("gemini-clave").value = "";
    $("ajustes-mensaje").textContent = "Clave guardada. Pulsa «Probar conexión» para elegir modelo.";
  } catch (e) {
    $("error-clave").textContent = e.message;
    $("error-clave").hidden = false;
  }
});

$("btn-borrar-clave").addEventListener("click", async () => {
  mostrarConfig(await api("/api/config", { gemini_api_key: "" }));
  $("ajustes-mensaje").textContent = "Clave borrada de este equipo.";
});

$("btn-probar-gemini").addEventListener("click", async () => {
  const b = $("btn-probar-gemini");
  b.disabled = true;
  $("ajustes-mensaje").textContent = "Probando…";
  try {
    const r = await api("/api/config/probar-gemini", { gemini_api_key: $("gemini-clave").value.trim() });
    const sel = $("gemini-modelo");
    sel.innerHTML = r.modelos.map((m) => `<option value="${m}">${m}</option>`).join("");
    if (configGemini.modelo && r.modelos.includes(configGemini.modelo)) sel.value = configGemini.modelo;
    else if (r.modelos.length) await api("/api/config", { gemini_modelo: r.modelos[0] });
    $("ajustes-mensaje").textContent = r.modelos.length
      ? `Conexión correcta: ${r.modelos.length} modelos de imagen disponibles.`
      : "La clave funciona, pero no tiene acceso a modelos de imagen.";
  } catch (e) {
    $("ajustes-mensaje").textContent = `Error: ${e.message}`;
  } finally {
    b.disabled = false;
  }
});

$("gemini-modelo").addEventListener("change", async () => {
  if ($("gemini-modelo").value) mostrarConfig(await api("/api/config", { gemini_modelo: $("gemini-modelo").value }));
});

$("btn-guardar-qwen").addEventListener("click", async () => {
  $("error-qwen").hidden = true;
  try {
    mostrarConfig(await api("/api/config", { qwen_url: $("qwen-url").value.trim() }));
    $("qwen-mensaje").textContent = "Dirección guardada.";
  } catch (e) {
    $("error-qwen").textContent = e.message;
    $("error-qwen").hidden = false;
  }
});

$("btn-probar-qwen").addEventListener("click", async () => {
  const b = $("btn-probar-qwen");
  b.disabled = true;
  $("qwen-mensaje").textContent = "Probando…";
  try {
    const r = await api("/api/config/probar-qwen", { qwen_url: $("qwen-url").value.trim() });
    $("qwen-mensaje").textContent = r.ok
      ? "Conexión correcta: el servidor Qwen-Image responde."
      : "El puente responde, pero el modelo (sd-server) no está listo; espera un minuto o revisa sus logs.";
  } catch (e) {
    $("qwen-mensaje").textContent = `Error: ${e.message}`;
  } finally {
    b.disabled = false;
  }
});

function aplicarMotor() {
  const motor = $("motor-ia").value;
  $("aviso-gemini").hidden = motor !== "gemini";
  $("aviso-qwen").hidden = motor !== "qwen";
  $("etiqueta-modo").hidden = motor !== "local";  // el modo (pasos) solo aplica a la IA local
}
$("motor-ia").addEventListener("change", () => { aplicarMotor(); aplicarTipoAnimacion(); });

// --- lienzo de boceto ---------------------------------------------------------

const lienzo = $("lienzo");
const ctx = lienzo.getContext("2d");
const historial = [];
let herramienta = "lapiz";
let dibujando = false;
let ultimo = null;
let hayTrazo = false;

function fondoBlanco() {
  ctx.fillStyle = "#fff";
  ctx.fillRect(0, 0, lienzo.width, lienzo.height);
}
fondoBlanco();

function guardarHistorial() {
  historial.push(ctx.getImageData(0, 0, lienzo.width, lienzo.height));
  if (historial.length > 30) historial.shift();
}

function punto(ev) {
  const r = lienzo.getBoundingClientRect();
  return { x: (ev.clientX - r.left) * lienzo.width / r.width, y: (ev.clientY - r.top) * lienzo.height / r.height };
}

lienzo.addEventListener("pointerdown", (ev) => {
  guardarHistorial();
  dibujando = true;
  ultimo = punto(ev);
  lienzo.setPointerCapture(ev.pointerId);
  trazar(ultimo);
});
lienzo.addEventListener("pointermove", (ev) => { if (dibujando) trazar(punto(ev)); });
["pointerup", "pointercancel"].forEach((t) => lienzo.addEventListener(t, () => { dibujando = false; }));

function trazar(p) {
  const grosor = Number($("grosor").value) * (herramienta === "goma" ? 3 : 1);
  ctx.strokeStyle = herramienta === "goma" ? "#fff" : "#111";
  ctx.lineWidth = grosor;
  ctx.lineCap = ctx.lineJoin = "round";
  ctx.beginPath();
  ctx.moveTo(ultimo.x, ultimo.y);
  ctx.lineTo(p.x + 0.01, p.y);
  ctx.stroke();
  ultimo = p;
  if (herramienta === "lapiz") hayTrazo = true;
}

function elegirHerramienta(nombre) {
  herramienta = nombre;
  for (const [id, n] of [["h-lapiz", "lapiz"], ["h-goma", "goma"]]) {
    $(id).classList.toggle("activa", n === nombre);
    $(id).setAttribute("aria-pressed", String(n === nombre));
  }
}
$("h-lapiz").onclick = () => elegirHerramienta("lapiz");
$("h-goma").onclick = () => elegirHerramienta("goma");
$("h-borrar").onclick = () => { guardarHistorial(); fondoBlanco(); hayTrazo = false; programarBoceto(); };
$("h-deshacer").onclick = deshacerBoceto;
function deshacerBoceto() {
  const previo = historial.pop();
  if (previo) ctx.putImageData(previo, 0, 0);
}
document.addEventListener("keydown", (ev) => {
  if ((ev.ctrlKey || ev.metaKey) && ev.key === "z" && !ev.target.matches("input, textarea")) {
    ev.preventDefault();
    if (!$("editor").hidden && editor.enfoque) editorDeshacer(); else deshacerBoceto();
  }
});

function ponerEnLienzo(img) {
  guardarHistorial();
  fondoBlanco();
  const escala = Math.min(lienzo.width / img.width, lienzo.height / img.height);
  const w = img.width * escala, h = img.height * escala;
  ctx.drawImage(img, (lienzo.width - w) / 2, (lienzo.height - h) / 2, w, h);
  hayTrazo = true;
  programarBoceto();
}

$("importar").addEventListener("change", async (ev) => {
  const archivo = ev.target.files[0];
  ev.target.value = "";
  if (!archivo) return;
  abrirPreparar(await createImageBitmap(archivo));
});

// --- preparar la imagen importada: recorte, giro y tratamiento -----------------
// Todo en el navegador: la foto se reduce a MAX_PREPARAR px para que sea instantáneo.

const MAX_PREPARAR = 1024;
const prep = { fuente: null, girada: null, recorte: { x0: 0, y0: 0, x1: 1, y1: 1 }, arrastre: null };

function abrirPreparar(bitmap) {
  const f = Math.min(1, MAX_PREPARAR / Math.max(bitmap.width, bitmap.height));
  const c = document.createElement("canvas");
  c.width = Math.round(bitmap.width * f);
  c.height = Math.round(bitmap.height * f);
  const cx = c.getContext("2d");
  cx.fillStyle = "#fff";  // PNG con transparencia: el fondo transparente es papel, no negro
  cx.fillRect(0, 0, c.width, c.height);
  cx.drawImage(bitmap, 0, 0, c.width, c.height);
  prep.fuente = c;
  prep.girada = c;
  prep.recorte = { x0: 0, y0: 0, x1: 1, y1: 1 };
  $("preparar").showModal();
  nivelAutomatico();
}

function girar90(c) {
  const g = document.createElement("canvas");
  g.width = c.height;
  g.height = c.width;
  const gx = g.getContext("2d");
  gx.translate(g.width, 0);
  gx.rotate(Math.PI / 2);
  gx.drawImage(c, 0, 0);
  return g;
}

function rectRecorte() {
  const { girada: g, recorte: r } = prep;
  const x = Math.round(Math.min(r.x0, r.x1) * g.width), y = Math.round(Math.min(r.y0, r.y1) * g.height);
  const w = Math.max(1, Math.round(Math.abs(r.x1 - r.x0) * g.width));
  const h = Math.max(1, Math.round(Math.abs(r.y1 - r.y0) * g.height));
  return { x, y, w, h };
}

// Gris del recorte, con la iluminación igualada si se pide (dividir por un fondo muy
// desenfocado: elimina sombras y el tono del papel, igual que hace el motor).
function grisRecorte() {
  const { x, y, w, h } = rectRecorte();
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const cx = c.getContext("2d", { willReadFrequently: true });
  cx.drawImage(prep.girada, x, y, w, h, 0, 0, w, h);
  const px = cx.getImageData(0, 0, w, h).data;
  const gris = new Float32Array(w * h);
  for (let i = 0; i < gris.length; i++) gris[i] = 0.299 * px[4 * i] + 0.587 * px[4 * i + 1] + 0.114 * px[4 * i + 2];
  if ($("pp-igualar").checked) {
    // Desenfoque barato: reducir mucho con suavizado y volver a ampliar.
    const peq = document.createElement("canvas");
    peq.width = Math.max(1, Math.round(w / 24));
    peq.height = Math.max(1, Math.round(h / 24));
    const px2 = peq.getContext("2d");
    px2.imageSmoothingQuality = "high";
    px2.drawImage(c, 0, 0, peq.width, peq.height);
    const fondoC = document.createElement("canvas");
    fondoC.width = w;
    fondoC.height = h;
    const fx = fondoC.getContext("2d", { willReadFrequently: true });
    fx.imageSmoothingQuality = "high";
    fx.filter = "blur(4px)";
    fx.drawImage(peq, 0, 0, w, h);
    const fd = fx.getImageData(0, 0, w, h).data;
    for (let i = 0; i < gris.length; i++) {
      const f = 0.299 * fd[4 * i] + 0.587 * fd[4 * i + 1] + 0.114 * fd[4 * i + 2];
      gris[i] = Math.min(255, (gris[i] / Math.max(f, 1)) * 255);
    }
  }
  return { gris, w, h };
}

function otsu(gris) {
  const hist = new Array(256).fill(0);
  for (const v of gris) hist[Math.round(v)]++;
  const total = gris.length;
  let suma = 0;
  for (let i = 0; i < 256; i++) suma += i * hist[i];
  let sumaB = 0, pesoB = 0, mejor = 0, umbral = 128;
  for (let t = 0; t < 256; t++) {
    pesoB += hist[t];
    if (!pesoB) continue;
    const pesoF = total - pesoB;
    if (!pesoF) break;
    sumaB += t * hist[t];
    const mB = sumaB / pesoB, mF = (suma - sumaB) / pesoF;
    const entre = pesoB * pesoF * (mB - mF) ** 2;
    if (entre > mejor) { mejor = entre; umbral = t; }
  }
  return umbral;
}

// Método del triángulo: para histogramas con un gran pico (el papel) y una cola (el
// trazo), donde Otsu falla porque no hay dos grupos parecidos. Se ignora el 0,5 % más
// oscuro (restos de bordes) para trazar la recta desde el pico.
function triangulo(gris) {
  const hist = new Array(256).fill(0);
  for (const v of gris) hist[Math.round(v)]++;
  let pico = 0;
  for (let i = 0; i < 256; i++) if (hist[i] > hist[pico]) pico = i;
  let acum = 0, fin = 0;
  for (let i = 0; i < 256; i++) { acum += hist[i]; if (acum > gris.length * 0.005) { fin = i; break; } }
  const dx = pico - fin, dy = hist[pico] - hist[fin], n = Math.hypot(dx, dy) || 1;
  let mejor = -1, umbral = pico;
  for (let i = fin; i < pico; i++) {
    const d = Math.abs(dy * (i - fin) - dx * (hist[i] - hist[fin])) / n;
    if (d > mejor) { mejor = d; umbral = i; }
  }
  return umbral;
}

function nivelAutomatico() {
  const { gris } = grisRecorte();
  // Tras igualar la iluminación el papel queda saturado en blanco: fuera del cálculo.
  const sinPapel = gris.filter((v) => v < 254);
  const nivel = sinPapel.length > gris.length * 0.01 ? triangulo(sinPapel) - 8 : otsu(gris);
  $("pp-nivel").value = Math.max(120, Math.min(245, nivel));
  pintarPreparar();
}

function resultadoPreparado() {
  const { gris, w, h } = grisRecorte();
  const nivel = Number($("pp-nivel").value);
  const bn = $("pp-bn").checked;
  const k = 1 + 4 * Number($("pp-contraste").value);
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const cx = c.getContext("2d");
  const datos = cx.createImageData(w, h);
  for (let i = 0; i < gris.length; i++) {
    let v;
    if (bn) v = gris[i] < nivel ? 0 : 255;
    else v = gris[i] >= nivel ? 255 : Math.max(0, Math.min(255, 255 - (nivel - gris[i]) * k));
    datos.data[4 * i] = datos.data[4 * i + 1] = datos.data[4 * i + 2] = v;
    datos.data[4 * i + 3] = 255;
  }
  cx.putImageData(datos, 0, 0);
  return c;
}

function ajustarCanvas(canvas, w, h, maximo = 420) {
  const f = Math.min(1, maximo / Math.max(w, h));
  canvas.width = Math.round(w * f);
  canvas.height = Math.round(h * f);
}

function pintarPreparar() {
  const g = prep.girada;
  const o = $("pp-original");
  ajustarCanvas(o, g.width, g.height);
  const ox = o.getContext("2d");
  ox.drawImage(g, 0, 0, o.width, o.height);
  const { x, y, w, h } = rectRecorte();
  const f = o.width / g.width;
  ox.fillStyle = "rgba(0, 0, 0, 0.55)";  // oscurece lo que queda fuera del recorte
  ox.fillRect(0, 0, o.width, y * f);
  ox.fillRect(0, (y + h) * f, o.width, o.height - (y + h) * f);
  ox.fillRect(0, y * f, x * f, h * f);
  ox.fillRect((x + w) * f, y * f, o.width - (x + w) * f, h * f);
  ox.strokeStyle = "#e8a33d";
  ox.lineWidth = 2;
  ox.strokeRect(x * f, y * f, w * f, h * f);

  const r = resultadoPreparado();
  const d = $("pp-resultado");
  ajustarCanvas(d, r.width, r.height);
  d.getContext("2d").drawImage(r, 0, 0, d.width, d.height);
  $("o-pp-nivel").textContent = $("pp-nivel").value;
  $("o-pp-contraste").textContent = Number($("pp-contraste").value).toFixed(2);
  $("pp-etiqueta-contraste").hidden = $("pp-bn").checked;
}

function puntoNormalizado(ev) {
  const r = $("pp-original").getBoundingClientRect();
  return {
    x: Math.min(1, Math.max(0, (ev.clientX - r.left) / r.width)),
    y: Math.min(1, Math.max(0, (ev.clientY - r.top) / r.height)),
  };
}
$("pp-original").addEventListener("pointerdown", (ev) => {
  const p = puntoNormalizado(ev);
  prep.arrastre = p;
  prep.recorte = { x0: p.x, y0: p.y, x1: p.x, y1: p.y };
  $("pp-original").setPointerCapture(ev.pointerId);
});
$("pp-original").addEventListener("pointermove", (ev) => {
  if (!prep.arrastre) return;
  const p = puntoNormalizado(ev);
  prep.recorte = { x0: prep.arrastre.x, y0: prep.arrastre.y, x1: p.x, y1: p.y };
  pintarPreparar();
});
["pointerup", "pointercancel"].forEach((t) => $("pp-original").addEventListener(t, () => {
  if (!prep.arrastre) return;
  prep.arrastre = null;
  const { w, h } = rectRecorte();
  if (w < 8 || h < 8) prep.recorte = { x0: 0, y0: 0, x1: 1, y1: 1 };  // un clic suelto: sin recorte
  nivelAutomatico();
}));
$("pp-girar").addEventListener("click", () => {
  prep.girada = girar90(prep.girada);
  const r = prep.recorte;  // el recorte gira con la imagen
  prep.recorte = { x0: 1 - r.y1, y0: r.x0, x1: 1 - r.y0, y1: r.x1 };
  pintarPreparar();
});
$("pp-todo").addEventListener("click", () => { prep.recorte = { x0: 0, y0: 0, x1: 1, y1: 1 }; nivelAutomatico(); });
$("pp-auto").addEventListener("click", nivelAutomatico);
$("pp-igualar").addEventListener("change", nivelAutomatico);
for (const id of ["pp-bn", "pp-nivel", "pp-contraste"]) $(id).addEventListener("input", pintarPreparar);
$("pp-aplicar").addEventListener("click", () => {
  ponerEnLienzo(resultadoPreparado());
  $("preparar").close();
});
$("pp-original-tal-cual").addEventListener("click", () => {
  const { x, y, w, h } = rectRecorte();  // respeta el recorte y el giro, sin tratar el color
  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  c.getContext("2d").drawImage(prep.girada, x, y, w, h, 0, 0, w, h);
  ponerEnLienzo(c);
  $("preparar").close();
});
$("pp-cancelar").addEventListener("click", () => $("preparar").close());

// --- proyectos, plantillas de personaje y galería ------------------------------

let proyectos = [];
let plantillas = [];  // biblioteca global (Ajustes › Plantillas de personaje)
const CLAVE_PROYECTO = "forja.proyecto";
const CLAVE_TIPO = "forja.tipo";

function guardarLocal(clave, valor) { try { localStorage.setItem(clave, valor); } catch { /* sin almacenamiento */ } }
function leerLocal(clave) { try { return localStorage.getItem(clave); } catch { return null; } }
function escapar(t) {
  return String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}
function textoParecido(p) { return p < 0.3 ? "libre" : p < 0.7 ? "equilibrado" : "fiel"; }

async function cargarProyectos(elegir) {
  plantillas = await api("/api/tipos");
  proyectos = await api("/api/proyectos");
  const opciones = proyectos.map((p) => `<option value="${p.id}">${escapar(p.nombre)}</option>`).join("");
  const sel = $("proyecto");
  const anterior = elegir ?? (sel.value || leerLocal(CLAVE_PROYECTO));
  sel.innerHTML = `<option value="">Sin proyecto (solo el estilo)</option>` + opciones;
  if (anterior && proyectos.some((p) => p.id === anterior)) sel.value = anterior;
  const cfg = $("cfg-proyecto");
  const editando = cfg.value;
  cfg.innerHTML = opciones || `<option value="">(aún no hay proyectos)</option>`;
  if (proyectos.some((p) => p.id === (elegir ?? editando))) cfg.value = elegir ?? editando;
  aplicarProyecto();
  editarProyecto(cfg.value);
  pintarPlantillas();
  const mp = $("mp-proyecto");
  const mpAnterior = elegir ?? (mp.value || $("proyecto").value);
  mp.innerHTML = opciones || `<option value="">(crea un proyecto en Ajustes)</option>`;
  if (proyectos.some((p) => p.id === mpAnterior)) mp.value = mpAnterior;
  if (!$("taller-mapas").hidden) aplicarProyectoMapa();
}

// --- taller de mapas -------------------------------------------------------------

function proyectoMapa() { return proyectos.find((p) => p.id === $("mp-proyecto").value); }

function aplicarProyectoMapa() {
  const p = proyectoMapa();
  const lateral = p?.perspectiva === "lateral";
  $("mp-cenital").hidden = lateral;
  $("mp-lateral").hidden = !lateral;
  const estilo = presets.find((x) => x.id === p?.preset);
  $("mp-forma").textContent = p
    ? `Vista ${lateral ? "lateral (plataformas)" : "cenital (todas direcciones)"} · baldosas de ${p.baldosa || 32} px` +
      ` · paleta de «${estilo ? estilo.nombre : p.preset}»`
    : "Crea un proyecto en Ajustes para generar su mapa.";
  $("btn-generar-mapa").disabled = !p;
  $("btn-generar-sala").disabled = !p;
  $("btn-generar-grande").disabled = !p;
  $("btn-rehacer-grande").disabled = !p;
  $("mp-error").hidden = true;
  pintarPisos();
  cargarGaleriaMapas();
}

// Pisos: ambientes de mapa guardados en el proyecto (dos suelos y, opcional, cielo).
function pintarPisos(elegir) {
  const pisos = proyectoMapa()?.pisos || [];
  const sel = $("mp-piso");
  const anterior = elegir ?? sel.value;
  sel.innerHTML = `<option value="">${pisos.length ? "— elige un piso para rellenar los suelos —" : "(sin pisos guardados)"}</option>` +
    pisos.map((x) => `<option value="${x.id}">${escapar(x.nombre)}</option>`).join("");
  if (pisos.some((x) => x.id === anterior)) sel.value = anterior;
  $("btn-borrar-piso").disabled = !sel.value;
  $("mp-piso-msg").textContent = "";
}
$("mp-piso").addEventListener("change", () => {
  const piso = (proyectoMapa()?.pisos || []).find((x) => x.id === $("mp-piso").value);
  $("btn-borrar-piso").disabled = !piso;
  if (!piso) return;
  // En vista lateral, el primer suelo es la superficie y el segundo el relleno.
  $("mp-a").value = $("mp-sup").value = piso.a;
  $("mp-b").value = $("mp-rel").value = piso.b || "";
  $("mp-cielo").value = piso.cielo || "";
  $("mp-piso-nombre").value = piso.nombre;
});

async function guardarPisos(pisos, mensaje, elegir) {
  const p = proyectoMapa();
  try {
    await api("/api/proyectos", { ...p, pisos });
    await cargarProyectos();
    $("mp-proyecto").value = p.id;
    pintarPisos(elegir ?? "");
    $("mp-piso-msg").textContent = mensaje;
  } catch (e) {
    $("mp-piso-msg").textContent = `Error: ${e.message}`;
  }
}
$("btn-guardar-piso").addEventListener("click", () => {
  const p = proyectoMapa();
  if (!p) return;
  const nombre = $("mp-piso-nombre").value.trim();
  const lateral = p.perspectiva === "lateral";
  const piso = lateral
    ? { nombre, a: $("mp-sup").value.trim(), b: $("mp-rel").value.trim(), cielo: $("mp-cielo").value.trim() }
    : { nombre, a: $("mp-a").value.trim(), b: $("mp-b").value.trim(), cielo: "" };
  if (!nombre) { $("mp-piso-msg").textContent = "Ponle un nombre al piso."; return; }
  if (!piso.a) { $("mp-piso-msg").textContent = "Escribe al menos el primer suelo."; return; }
  const pisos = (p.pisos || []).filter((x) => x.nombre.toLowerCase() !== nombre.toLowerCase());
  const reemplaza = pisos.length !== (p.pisos || []).length;
  guardarPisos([...pisos, piso], reemplaza ? `Piso «${nombre}» actualizado.` : `Piso «${nombre}» guardado.`);
});
$("btn-borrar-piso").addEventListener("click", () => {
  const p = proyectoMapa();
  const piso = (p?.pisos || []).find((x) => x.id === $("mp-piso").value);
  if (!piso || !confirm(`¿Borrar el piso «${piso.nombre}»? (los mapas ya generados no se borran)`)) return;
  guardarPisos(p.pisos.filter((x) => x.id !== piso.id), `Piso «${piso.nombre}» borrado.`);
});
$("mp-proyecto").addEventListener("change", aplicarProyectoMapa);

function piezasMapa() {
  const visible = proyectoMapa()?.perspectiva === "lateral" ? "mp-lateral" : "mp-cenital";
  return [...document.querySelectorAll(`#${visible} input[name="mp-pieza"]:checked`)].map((c) => c.value);
}

$("btn-generar-mapa").addEventListener("click", async () => {
  const p = proyectoMapa();
  if (!p) return;
  const lateral = p.perspectiva === "lateral";
  const materiales = lateral
    ? { superficie: $("mp-sup").value.trim(), relleno: $("mp-rel").value.trim(), cielo: $("mp-cielo").value.trim() }
    : { a: $("mp-a").value.trim(), b: $("mp-b").value.trim() };
  const piezas = piezasMapa();
  const error = $("mp-error");
  const fallo = !piezas.length ? "Elige al menos una pieza."
    : !lateral && !materiales.a ? "Escribe el suelo principal (p. ej. «pasto»)."
    : !lateral && piezas.includes("transiciones") && !materiales.b ? "Las transiciones necesitan un segundo suelo."
    : lateral && piezas.includes("plataformas") && !(materiales.superficie && materiales.relleno)
      ? "Los bloques de plataforma necesitan superficie y relleno." : "";
  if (fallo) { error.textContent = fallo; error.hidden = false; return; }
  error.hidden = true;
  const b = $("btn-generar-mapa");
  b.disabled = true;
  try {
    const { trabajo } = await api("/api/mapas", {
      proyecto: p.id, materiales, piezas, variantes: Number($("mp-variantes").value),
      legibilidad: $("mp-legibilidad").value, piso: $("mp-piso").value || undefined,
      semilla: $("mp-semilla").value === "" ? undefined : Number($("mp-semilla").value),
    });
    const inicio = Date.now();
    for (;;) {
      const t = await api(`/api/trabajos/${trabajo}`);
      const seg = Math.round((Date.now() - inicio) / 1000);
      if (t.estado === "listo") {
        $("mp-progreso").textContent = `Listo en ${seg} s · ${t.resultado.baldosas} baldosas`;
        mostrarMapa(t.resultado);
        cargarGaleriaMapas();
        break;
      }
      if (t.estado === "error") throw new Error(t.error);
      $("mp-progreso").textContent = t.estado === "en_cola" ? `En cola (posición ${t.posicion})…`
        : `Generando… ${seg} s${seg < 40 ? " (la primera vez carga los modelos)" : ""}`;
      await new Promise((r) => setTimeout(r, 1000));
    }
  } catch (e) {
    error.textContent = e.message;
    error.hidden = false;
    $("mp-progreso").textContent = "";
  } finally {
    b.disabled = false;
  }
});

// --- mapa grande de mazmorra ---------------------------------------------------------------

let ambientesGrandes = [];
async function cargarAmbientesGrandes() {
  ambientesGrandes = await api("/api/mapas-grandes/ambientes").catch(() => []);
  const sel = $("mp-grande-ambiente");
  const anterior = sel.value;
  sel.innerHTML = ambientesGrandes.map((a) => `<option value="${a.id}">${escapar(a.nombre)}</option>`).join("");
  if (ambientesGrandes.some((a) => a.id === anterior)) sel.value = anterior;
  estadoAmbienteGrande();
}
function estadoAmbienteGrande() {
  const a = ambientesGrandes.find((x) => x.id === $("mp-grande-ambiente").value);
  $("mp-grande-estado").textContent = !a ? "" : a.listo
    ? "Texturas listas: el mapa tarda unos segundos."
    : "Las texturas de este ambiente aún no están hechas: la primera vez tarda ~4 min y necesita el servidor Qwen.";
}
$("mp-grande-ambiente").addEventListener("change", estadoAmbienteGrande);

async function generarGrande(rehacer) {
  const p = proyectoMapa();
  if (!p) return;
  const [ancho, alto] = $("mp-grande-tamano").value.split("x").map(Number);
  const error = $("mp-grande-error");
  const botones = [$("btn-generar-grande"), $("btn-rehacer-grande")];
  error.hidden = true;
  botones.forEach((b) => { b.disabled = true; });
  try {
    const { trabajo } = await api("/api/mapas-grandes", {
      proyecto: p.id, ambiente: $("mp-grande-ambiente").value, ancho, alto, salas: Number($("mp-grande-salas").value),
      decorar: $("mp-grande-decorar").checked, rehacer_superficies: rehacer || undefined,
      semilla: $("mp-semilla").value === "" ? undefined : Number($("mp-semilla").value),
    });
    const inicio = Date.now();
    for (;;) {
      const t = await api(`/api/trabajos/${trabajo}`);
      const seg = Math.round((Date.now() - inicio) / 1000);
      if (t.estado === "listo") {
        const r = t.resultado;
        $("mp-grande-progreso").textContent = `Listo en ${seg} s · ${r.baldosas[0]}×${r.baldosas[1]} baldosas · ${r.salas} salas` +
          (r.decorados ? ` · ${r.decorados} decorados` : "");
        mostrarMapa(r);
        cargarGaleriaMapas();
        cargarAmbientesGrandes();
        break;
      }
      if (t.estado === "error") throw new Error(t.error);
      $("mp-grande-progreso").textContent = t.estado === "en_cola" ? `En cola (posición ${t.posicion})…`
        : `${t.progreso || "Generando el mapa"}… ${seg} s`;
      await new Promise((r) => setTimeout(r, 1000));
    }
  } catch (e) {
    error.textContent = e.message;
    error.hidden = false;
    $("mp-grande-progreso").textContent = "";
  } finally {
    botones.forEach((b) => { b.disabled = false; });
  }
}
$("btn-generar-grande").addEventListener("click", () => generarGrande(false));
$("btn-rehacer-grande").addEventListener("click", () => {
  if (confirm("¿Volver a pedir a Qwen las texturas de este ambiente? Tarda unos 4 minutos y cambia el aspecto de todos los mapas nuevos de este ambiente (los ya generados no cambian).")) generarGrande(true);
});

$("btn-generar-sala").addEventListener("click", async () => {
  const p = proyectoMapa();
  if (!p) return;
  const descripcion = $("mp-sala-desc").value.trim();
  const error = $("mp-sala-error");
  if (!descripcion) { error.textContent = "Describe la sala."; error.hidden = false; return; }
  error.hidden = true;
  const b = $("btn-generar-sala");
  b.disabled = true;
  try {
    const { trabajo } = await api("/api/salas", {
      proyecto: p.id, descripcion, forma: $("mp-sala-forma").value, detalle: $("mp-sala-detalle").value,
      variantes: Number($("mp-sala-variantes").value),
      semilla: $("mp-semilla").value === "" ? undefined : Number($("mp-semilla").value),
    });
    const inicio = Date.now();
    for (;;) {
      const t = await api(`/api/trabajos/${trabajo}`);
      const seg = Math.round((Date.now() - inicio) / 1000);
      if (t.estado === "listo") {
        $("mp-sala-progreso").textContent = `Listo en ${seg} s · ${t.resultado.salas.length} salas`;
        mostrarMapa(t.resultado);
        cargarGaleriaMapas();
        break;
      }
      if (t.estado === "error") throw new Error(t.error);
      $("mp-sala-progreso").textContent = t.estado === "en_cola" ? `En cola (posición ${t.posicion})…`
        : `Qwen está dibujando la sala… ${seg} s (~35 s por sala; la primera vez carga el modelo)`;
      await new Promise((r) => setTimeout(r, 1000));
    }
  } catch (e) {
    error.textContent = e.message;
    error.hidden = false;
    $("mp-sala-progreso").textContent = "";
  } finally {
    b.disabled = false;
  }
});

const NOMBRES_CAPA = { fondo_cielo: "Cielo", fondo_lejos: "Montañas lejanas", fondo_cerca: "Colinas cercanas" };

function mostrarMapa(m) {
  const v = `?v=${Date.now()}`;
  $("mp-vacio").hidden = true;
  $("mp-resultado").hidden = false;
  const u = m.urls;
  const leg = m.legibilidad;
  $("mp-escena-caja").hidden = !u.escena_prueba_x2;
  if (u.escena_prueba_x2) {
    $("mp-escena").src = API + u.escena_prueba_x2 + v;
    const nivelTxt = leg.automatica ? `automática (nivel ${leg.nivel})` : `nivel ${leg.nivel}`;
    const cam = leg.camuflaje && leg.camuflaje.personajes
      ? ` · ${Math.round(leg.camuflaje.medio * 100)} % de los colores de tus personajes se parecen al suelo` +
        ` (el peor, ${Math.round(leg.camuflaje.peor * 100)} %)` : "";
    $("mp-legib-info").textContent = `Legibilidad ${nivelTxt}${cam}.`;
  }
  const grande = m.tipo === "grande";
  const principal = grande ? u.mapa_prueba : u.mapa_prueba_x2;
  $("mp-prueba").hidden = !principal;
  if (principal) $("mp-prueba").src = API + principal + v;
  $("mp-prueba-titulo").textContent = grande ? `Mapa grande: ${(m.materiales || {}).ambiente || ""}` : "Mapa de prueba";
  $("mp-prueba-ayuda").textContent = grande
    ? `${m.baldosas[0]}×${m.baldosas[1]} baldosas (${m.pixeles[0]}×${m.pixeles[1]} px), ${m.salas} salas${m.decorados ? `, ${m.decorados} decorados` : ""}. Pulsa la imagen para verla a tamaño real.`
    : "Pintado automáticamente con las piezas generadas, para ver cómo encajan.";
  $("mp-prueba").onclick = grande ? () => window.open(API + principal + v, "_blank", "noopener") : null;
  $("mp-prueba").style.cursor = grande ? "zoom-in" : "";
  $("mp-tileset").hidden = !u.tileset_x4;
  $("mp-tileset-titulo").hidden = !u.tileset_x4;
  if (u.tileset_x4) $("mp-tileset").src = API + u.tileset_x4 + v;
  const salas = Array.isArray(m.salas) ? m.salas : [];  // en un mapa grande `salas` es un número
  const capas = Object.keys(NOMBRES_CAPA).filter((k) => u[k]);
  if (salas.length > 1) {  // varias salas: se pueden ver todas; pulsar una la muestra grande
    $("mp-capas").innerHTML = `<h3>Salas generadas</h3><div class="capas">` + salas.map((i) =>
      `<figure><img class="mapa-img capa sala-miniatura" data-grande="${u["sala_" + i + "_x2"]}" src="${API + u["sala_" + i + "_x2"] + v}" alt="Sala ${i}">
       <figcaption>Sala ${i}</figcaption></figure>`).join("") + "</div>";
    for (const im of $("mp-capas").querySelectorAll(".sala-miniatura")) {
      im.onclick = () => { $("mp-prueba").src = API + im.dataset.grande + v; };
    }
  } else if (grande && u.mapa_decorado) {  // con y sin decorados
    $("mp-capas").innerHTML = `<div class="fila"><button type="button" class="sec" id="gr-con">Con decorados</button>
      <button type="button" class="sec" id="gr-sin">Solo suelo y muros</button></div>`;
    $("gr-con").onclick = () => { $("mp-prueba").src = API + u.mapa_decorado + v; };
    $("gr-sin").onclick = () => { $("mp-prueba").src = API + u.mapa + v; };
  } else $("mp-capas").innerHTML = capas.length ? `<h3>Fondo en capas</h3><div class="capas">` + capas.map((k) =>
    `<figure><img class="mapa-img capa" src="${API + u[k] + v}" alt="${NOMBRES_CAPA[k]}">
     <figcaption>${NOMBRES_CAPA[k]}</figcaption></figure>`).join("") + "</div>" : "";
  const enlaces = [["tileset", "tileset.png"], ["tsx", "tileset.tsx (Tiled)"],
    ...(grande ? [["mapa_decorado", "mapa_decorado.png"], ["mapa", "mapa.png (sin decorados)"], ["tmx", "mapa.tmx (Tiled)"],
      ["decorados", "decorados_colocados.json"], ["plano", "plano.json"], ["colision", "colision.png"]] : []),
    ...capas.map((k) => [k, `${k}.png`]),
    ...salas.flatMap((i) => [[`sala_${i}`, `sala_${i}.png`], [`tmx_${i}`, `sala_${i}.tmx (Tiled)`]])].filter(([k]) => u[k]);
  $("mp-descargas").innerHTML = "Archivos: " + enlaces.map(([k, n]) =>
    `<a href="${API + u[k]}" download target="_blank" rel="noopener">${n}</a>`).join(" · ") +
    `<br>Carpeta: salidas/mapas/${escapar(m.carpeta)}`;
}

async function cargarGaleriaMapas() {
  const p = proyectoMapa();
  const grid = $("mp-galeria-grid");
  if (!p) { grid.innerHTML = ""; $("mp-galeria-n").textContent = ""; return; }
  const lista = await api(`/api/proyectos/${p.id}/mapas`).catch(() => []);
  $("mp-galeria-n").textContent = `(${lista.length})`;
  grid.innerHTML = lista.length ? "" : `<p class="ayuda">Aún no hay mapas en este proyecto.</p>`;
  for (const m of lista) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tarjeta";
    const img = m.urls.mapa_prueba || m.urls.tileset;
    const nombre = Object.values(m.materiales || {}).filter(Boolean).join(", ").slice(0, 60);
    b.innerHTML = `${img ? `<img src="${API + img}" alt="">` : ""}<div class="meta">${escapar(nombre)}<br>${m.perspectiva}</div>`;
    b.onclick = () => mostrarMapa(m);
    grid.appendChild(b);
  }
}

function proyectoActual() { return proyectos.find((p) => p.id === $("proyecto").value); }

// Tipos al generar: los comunes más los propios del proyecto (estos mandan si coinciden).
function tiposActuales() {
  const todos = new Map(plantillas.map((t) => [t.id, t]));
  for (const t of proyectoActual()?.tipos || []) todos.set(t.id, t);
  return [...todos.values()];
}

function aplicarProyecto() {
  const p = proyectoActual();
  guardarLocal(CLAVE_PROYECTO, $("proyecto").value);
  pintarSelectorTipo();
  if (p) {
    if (presets.some((x) => x.id === p.preset)) { $("preset").value = p.preset; aplicarPreset(); }
    aplicarParecidoDelTipo();  // el del tipo elegido o, si no tiene, el del proyecto
  }
  $("prompt-final").hidden = true;
  cargarGaleria();
}
$("proyecto").addEventListener("change", aplicarProyecto);

function pintarSelectorTipo() {
  const sel = $("tipo");
  const anterior = sel.value || leerLocal(CLAVE_TIPO);
  const propios = new Set((proyectoActual()?.tipos || []).map((t) => t.id));
  sel.innerHTML = `<option value="">Sin plantilla</option>` + tiposActuales().map((t) =>
    `<option value="${t.id}">${escapar(t.nombre)}${propios.has(t.id) ? " (del proyecto)" : ""}</option>`).join("");
  if (tiposActuales().some((t) => t.id === anterior)) sel.value = anterior;
  actualizarAyudaPrompt();
}
function tipoElegido() { return tiposActuales().find((t) => t.id === $("tipo").value) || null; }

// Con un tipo elegido su plantilla ya describe al personaje: la descripción pasa a ser opcional.
function actualizarAyudaPrompt() {
  const t = tipoElegido();
  $("ayuda-prompt").textContent = t?.prompt
    ? `Opcional: la plantilla «${t.nombre}» ya lo describe. Añade aquí solo lo propio de este personaje.`
    : "En español o en inglés. Lo propio de este personaje: qué es, un rasgo distintivo, materiales y un color destacado.";
  $("prompt").placeholder = t?.prompt ? `(opcional) ${t.prompt}` :
    "guerrero esqueleto con armadura oxidada y una espada rota, ojos verdes brillantes";
  $("error-prompt").hidden = true;
}
$("tipo").addEventListener("change", () => {
  guardarLocal(CLAVE_TIPO, $("tipo").value);
  $("prompt-final").hidden = true;
  actualizarAyudaPrompt();
  aplicarParecidoDelTipo();
});

// Un tipo puede traer su propio parecido al boceto (p. ej. efectos: libre); si no, el del proyecto.
function aplicarParecidoDelTipo() {
  const t = tipoElegido();
  const p = proyectoActual();
  const valor = t?.parecido_boceto ?? p?.parecido_boceto;
  if (valor != null) { $("parecido-boceto").value = valor; actualizarSalidas(); }
}

// Editor de filas de tipos (lo usan las plantillas comunes y los tipos propios de un proyecto).
function pintarTipos(caja, tipos) {
  caja.innerHTML = "";
  for (const t of tipos) caja.appendChild(filaTipo(t));
}

function filaTipo(t = { nombre: "", prompt: "", negativo: "" }) {
  const fila = document.createElement("div");
  fila.className = "tipo-fila";
  fila.dataset.id = t.id || "";
  fila.innerHTML = `
    <div class="fila"><input class="t-nombre" maxlength="40" placeholder="Nombre (p. ej. Espectro)" aria-label="Nombre del tipo">
      <button type="button" class="sec t-quitar" aria-label="Quitar tipo">Quitar</button></div>
    <input class="t-prompt" maxlength="300" placeholder="Qué es: «una sola criatura espectral, cuerpo completo…»" aria-label="Plantilla del tipo">
    <input class="t-negativo" maxlength="300" placeholder="Evitar en este tipo (opcional)" aria-label="Evitar en este tipo">
    <label class="t-parecido-etiqueta">Parecido al boceto
      <select class="t-parecido">
        <option value="">el del proyecto</option>
        <option value="0.15">libre (efectos, llamas, humo)</option>
        <option value="0.35">suelto</option>
        <option value="0.5">equilibrado</option>
        <option value="0.8">fiel (objetos con forma exacta)</option>
      </select></label>
    <label class="check t-ambiente-etiqueta" title="Desactívalo en efectos y objetos que la ambientación tiñe (una bola de fuego en un mundo de brillo verde)">
      <input type="checkbox" class="t-ambiente"> Usar la ambientación del proyecto</label>`;
  fila.querySelector(".t-nombre").value = t.nombre;
  fila.querySelector(".t-prompt").value = t.prompt;
  fila.querySelector(".t-negativo").value = t.negativo || "";
  fila.querySelector(".t-ambiente").checked = t.usar_ambientacion !== false;
  const parecido = fila.querySelector(".t-parecido");
  if (t.parecido_boceto != null) {
    if (![...parecido.options].some((o) => Number(o.value) === t.parecido_boceto && o.value !== "")) {
      parecido.add(new Option(`propio (${t.parecido_boceto})`, String(t.parecido_boceto)));
    }
    parecido.value = [...parecido.options].find((o) => o.value !== "" && Number(o.value) === t.parecido_boceto).value;
  }
  fila.querySelector(".t-quitar").onclick = () => fila.remove();
  return fila;
}

function tiposDe(caja) {
  return [...caja.children].map((f) => ({
    id: f.dataset.id || undefined,
    nombre: f.querySelector(".t-nombre").value.trim(),
    prompt: f.querySelector(".t-prompt").value.trim(),
    negativo: f.querySelector(".t-negativo").value.trim(),
    usar_ambientacion: f.querySelector(".t-ambiente").checked,
    parecido_boceto: f.querySelector(".t-parecido").value === "" ? null : Number(f.querySelector(".t-parecido").value),
  })).filter((t) => t.nombre || t.prompt);
}

function anadirFila(caja) {
  const fila = filaTipo();
  caja.appendChild(fila);
  fila.querySelector(".t-nombre").focus();
}

// --- Ajustes › Plantillas de personaje ---

function pintarPlantillas() { pintarTipos($("pl-tipos"), plantillas); }
$("btn-anadir-plantilla").addEventListener("click", () => anadirFila($("pl-tipos")));
$("btn-guardar-plantillas").addEventListener("click", async () => {
  const tipos = tiposDe($("pl-tipos"));
  if (tipos.some((t) => !t.nombre)) { $("pl-mensaje").textContent = "Cada plantilla necesita un nombre."; return; }
  try {
    plantillas = await api("/api/tipos", { tipos });
    pintarPlantillas();
    pintarSelectorTipo();
    $("pl-mensaje").textContent = `Guardadas ${plantillas.length} plantillas.`;
  } catch (e) {
    $("pl-mensaje").textContent = `Error: ${e.message}`;
  }
});

// --- Ajustes › Proyectos ---

function editarProyecto(id) {
  const p = proyectos.find((x) => x.id === id);
  if (p) $("cfg-proyecto").value = p.id;
  $("pr-nombre").value = p ? p.nombre : "";
  if (p && presets.some((x) => x.id === p.preset)) $("pr-preset").value = p.preset;
  $("pr-ambientacion").value = p ? p.ambientacion : "";
  $("pr-negativo").value = p ? p.negativo : "";
  $("pr-notas").value = p ? (p.notas || "") : "";
  $("pr-parecido").value = p ? p.parecido_boceto : 0.5;
  $("pr-perspectiva").value = p?.perspectiva || "cenital";
  $("pr-baldosa").value = String(p?.baldosa || 32);
  $("o-pr-parecido").textContent = textoParecido(Number($("pr-parecido").value));
  pintarTipos($("pr-tipos"), p ? p.tipos : []);
  $("pr-mensaje").textContent = "";
}
$("cfg-proyecto").addEventListener("change", () => editarProyecto($("cfg-proyecto").value));
$("pr-parecido").addEventListener("input", () => {
  $("o-pr-parecido").textContent = textoParecido(Number($("pr-parecido").value));
});
$("btn-anadir-tipo").addEventListener("click", () => anadirFila($("pr-tipos")));

let proyectoNuevo = false;
$("btn-nuevo-proyecto").addEventListener("click", () => {
  proyectoNuevo = true;
  $("cfg-proyecto").add(new Option("(nuevo proyecto sin guardar)", ""), 0);
  $("cfg-proyecto").value = "";
  editarProyecto("");
  $("pr-mensaje").textContent = "Escribe el nombre y la ambientación del mundo, elige su estilo y pulsa «Guardar proyecto».";
  $("pr-nombre").focus();
});

$("btn-guardar-proyecto").addEventListener("click", async () => {
  const nombre = $("pr-nombre").value.trim();
  if (!nombre) {
    $("pr-mensaje").textContent = "El proyecto necesita un nombre.";
    $("pr-nombre").focus();
    return;
  }
  const tipos = tiposDe($("pr-tipos"));
  if (tipos.some((t) => !t.nombre)) {
    $("pr-mensaje").textContent = "Cada tipo de personaje necesita un nombre.";
    return;
  }
  try {
    const id = $("cfg-proyecto").value || undefined;
    const guardado = await api("/api/proyectos", {
      id, nombre, preset: $("pr-preset").value,
      ambientacion: $("pr-ambientacion").value, negativo: $("pr-negativo").value, notas: $("pr-notas").value,
      parecido_boceto: Number($("pr-parecido").value), tipos,
      perspectiva: $("pr-perspectiva").value, baldosa: Number($("pr-baldosa").value),
      pisos: proyectos.find((x) => x.id === id)?.pisos || [],  // se editan en el taller de mapas
    });
    proyectoNuevo = false;
    // El taller pasa a usar el proyecto recién guardado.
    $("proyecto").value = guardado.id;
    await cargarProyectos(guardado.id);
    $("pr-mensaje").textContent = `Proyecto «${guardado.nombre}» guardado. Ya está elegido en el taller.`;
  } catch (e) {
    $("pr-mensaje").textContent = `Error: ${e.message}`;
  }
});

$("btn-ver-prompt").addEventListener("click", async () => {
  const caja = $("prompt-final");
  caja.hidden = false;
  caja.textContent = "Traduciendo y preparando…";
  try {
    const r = await api("/api/prompt", {
      proyecto: $("proyecto").value || undefined, preset: $("preset").value, tipo: $("tipo").value,
      prompt: $("prompt").value, ambientacion: $("pr-ambientacion").value, negativo: $("pr-negativo").value,
    });
    let html = `<strong>La IA recibirá</strong> (${r.tokens}/${r.limite} tokens):<br>${escapar(r.positivo)}`;
    if (r.ambientacion_fuera.length) {
      html += `<p class="aviso">No cabe parte de la ambientación: ${escapar(r.ambientacion_fuera.join(", "))}.
        Acórtala o pon primero lo más importante.</p>`;
    }
    html += `<br><strong>Evitar:</strong> ${escapar(r.negativo)}`;
    caja.innerHTML = html;
  } catch (e) {
    caja.textContent = `Error: ${e.message}`;
  }
});

async function cargarGaleria() {
  const p = proyectoActual();
  const caja = $("galeria");
  if (!p) { caja.hidden = true; return; }
  const lista = await api(`/api/proyectos/${p.id}/personajes`).catch(() => []);
  caja.hidden = false;
  $("galeria-n").textContent = `(${lista.length})`;
  const grid = $("galeria-grid");
  grid.innerHTML = lista.length ? "" : `<p class="ayuda">Aún no hay personajes en este proyecto.</p>`;
  for (const pj of lista) {
    const b = document.createElement("button");
    b.className = "tarjeta";
    b.title = pj.descripcion || "";
    b.innerHTML = `<img src="${API + pj.sprite_url}" alt="">
      <div class="meta">${escapar(pj.tipo || "")} ${pj.animaciones.length ? `· ${pj.animaciones.length} anim.` : ""}<br>
      ${escapar((pj.descripcion || "").slice(0, 40))}</div>`;
    b.onclick = async () => {
      mostrarResultado(await api(`/api/resultados/${pj.carpeta}`), pj.indice);
      $("progreso").textContent = `Abierto: ${pj.descripcion || pj.carpeta}`;
    };
    grid.appendChild(b);
  }
}

// --- vista previa del boceto ---------------------------------------------------

let temporizadorBoceto = null;
function opcionesBoceto() {
  return { simplificar: Number($("simplificar").value), grosor: Number($("grosor-linea").value) };
}
async function previsualizarBoceto() {
  if (!$("boceto-ia").open) return;
  if (!hayTrazo) { $("boceto-msg").textContent = "Dibuja o importa un boceto."; $("boceto-limpio").removeAttribute("src"); return; }
  $("boceto-msg").textContent = "Limpiando…";
  try {
    const r = await api("/api/boceto", {
      boceto: lienzo.toDataURL("image/png"), proyecto: $("proyecto").value || undefined,
      preset: $("preset").value, ...opcionesBoceto(),
    });
    $("boceto-limpio").src = r.imagen;
    $("boceto-msg").textContent = `Esto recibe la IA (${r.ancho}×${r.alto}): trazo blanco sobre negro.`;
  } catch (e) {
    $("boceto-msg").textContent = `Error: ${e.message}`;
  }
}
function programarBoceto() {
  clearTimeout(temporizadorBoceto);
  temporizadorBoceto = setTimeout(previsualizarBoceto, 400);
}
$("boceto-ia").addEventListener("toggle", previsualizarBoceto);
$("simplificar").addEventListener("input", () => {
  $("o-simplificar").textContent = Number($("simplificar").value).toFixed(2);
  programarBoceto();
});
$("grosor-linea").addEventListener("change", programarBoceto);
lienzo.addEventListener("pointerup", programarBoceto);

// --- presets y formulario ---------------------------------------------------

let presets = [];
async function cargarPresets(elegir) {
  presets = await api("/api/presets");
  const opciones = presets.map((p) => `<option value="${p.id}">${escapar(p.nombre)}</option>`).join("");
  for (const id of ["preset", "pr-preset"]) {
    const anterior = $(id).value;
    $(id).innerHTML = opciones;
    if (presets.some((p) => p.id === anterior)) $(id).value = anterior;
  }
  if (elegir) $("pr-preset").value = elegir;
  aplicarPreset();
  if (!$("pn-estilos").hidden) pintarListaEstilos();
}
function presetActual() { return presets.find((p) => p.id === $("preset").value); }
function aplicarPreset() {
  const p = presetActual();
  if (!p) return;
  $("preset-vista").src = API + p.vista_previa;
  const g = p.generacion || {};
  if (!proyectoActual() && g.parecido_boceto != null) $("parecido-boceto").value = g.parecido_boceto;
  if (g.fuerza_estilo != null) $("fuerza-estilo").value = g.fuerza_estilo;
  $("alto-figura").placeholder = p.sprite.alto_figura;
  $("colores").placeholder = p.colores_por_sprite.objetivo;
  actualizarSalidas();
}
$("preset").addEventListener("change", aplicarPreset);

// --- Ajustes › Estilos de píxel ---

let estiloEditado = null;
function pintarListaEstilos() {
  const caja = $("es-lista");
  caja.innerHTML = "";
  for (const p of presets) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "tarjeta";
    b.setAttribute("aria-selected", String(p.id === estiloEditado));
    b.innerHTML = `<img src="${API}/api/presets/${p.id}/${p.ejemplos[0]}" alt="" class="sprite">
      <div class="meta">${escapar(p.nombre)}<br>${p.sprite.ancho}×${p.sprite.alto} px · ${p.paleta.length} colores</div>`;
    b.onclick = () => editarEstilo(p.id);
    caja.appendChild(b);
  }
}
function editarEstilo(id) {
  const p = presets.find((x) => x.id === id);
  if (!p) { $("es-editor").hidden = true; estiloEditado = null; return; }
  estiloEditado = id;
  $("es-editor").hidden = false;
  $("es-titulo").textContent = p.nombre;
  $("es-vista").src = `${API + p.vista_previa}?v=${Date.now()}`;
  const usan = proyectos.filter((x) => x.preset === id).map((x) => x.nombre);
  $("es-datos").textContent = `${p.sprite.ancho}×${p.sprite.alto} px · contorno ${p.contorno.tiene ? `${p.contorno.grosor} px` : "no"}` +
    ` · ~${p.colores_por_sprite.objetivo} colores por sprite · paleta de ${p.paleta.length}` +
    (usan.length ? ` · lo usa: ${usan.join(", ")}` : "") + (p.creado_en_app ? "" : " · incluido con la app");
  $("es-nombre").value = p.nombre;
  $("es-prompt").value = p.generacion.prompt_base || "";
  $("es-negativo").value = p.generacion.prompt_negativo || "";
  $("btn-borrar-estilo").hidden = !p.creado_en_app;
  $("es-mensaje").textContent = "";
  pintarListaEstilos();
}
$("btn-guardar-estilo").addEventListener("click", async () => {
  try {
    await api(`/api/presets/${estiloEditado}`, {
      nombre: $("es-nombre").value, prompt_base: $("es-prompt").value, prompt_negativo: $("es-negativo").value,
    });
    await cargarPresets();
    editarEstilo(estiloEditado);
    $("es-mensaje").textContent = "Estilo guardado.";
  } catch (e) {
    $("es-mensaje").textContent = `Error: ${e.message}`;
  }
});
$("btn-borrar-estilo").addEventListener("click", async () => {
  const p = presets.find((x) => x.id === estiloEditado);
  if (!p || !confirm(`¿Borrar el estilo «${p.nombre}»? Sus sprites de ejemplo se eliminan de la app.`)) return;
  try {
    await api(`/api/presets/${p.id}/borrar`, {});
    estiloEditado = null;
    $("es-editor").hidden = true;
    await cargarPresets();
    $("es-mensaje").textContent = `Estilo «${p.nombre}» borrado.`;
  } catch (e) {
    $("es-mensaje").textContent = `Error: ${e.message}`;
  }
});

let ejemplosNuevos = [];
function leerComoDataUrl(archivo) {
  return new Promise((ok, mal) => {
    const r = new FileReader();
    r.onload = () => ok(r.result);
    r.onerror = mal;
    r.readAsDataURL(archivo);
  });
}
$("en-ejemplos").addEventListener("change", async (ev) => {
  ejemplosNuevos = await Promise.all([...ev.target.files].slice(0, 5).map(leerComoDataUrl));
  $("en-miniaturas").innerHTML = ejemplosNuevos.map((d) => `<img src="${d}" alt="Ejemplo">`).join("");
  $("en-mensaje").textContent = ev.target.files.length > 5 ? "Se usarán solo los 5 primeros." : "";
});
$("btn-crear-estilo").addEventListener("click", async () => {
  const nombre = $("en-nombre").value.trim();
  if (!nombre) { $("en-mensaje").textContent = "Ponle un nombre al estilo."; return; }
  if (ejemplosNuevos.length < 2) { $("en-mensaje").textContent = "Elige al menos 2 sprites de ejemplo."; return; }
  const b = $("btn-crear-estilo");
  b.disabled = true;
  $("en-mensaje").textContent = "Analizando los ejemplos…";
  try {
    const p = await api("/api/presets", {
      nombre, ejemplos: ejemplosNuevos, estilo: $("en-estilo").value, max_paleta: $("en-paleta").value || undefined,
    });
    await cargarPresets();
    editarEstilo(p.id);
    for (const id of ["en-nombre", "en-estilo", "en-paleta", "en-ejemplos"]) $(id).value = "";
    ejemplosNuevos = [];
    $("en-miniaturas").innerHTML = "";
    $("es-nuevo").open = false;
    $("en-mensaje").textContent = "";
    $("es-mensaje").textContent = `Estilo «${p.nombre}» creado: ${p.sprite.ancho}×${p.sprite.alto} px, ` +
      `${p.paleta.length} colores. Ya puedes elegirlo en un proyecto o en el taller.`;
  } catch (e) {
    $("en-mensaje").textContent = `Error: ${e.message}`;
  } finally {
    b.disabled = false;
  }
});

function actualizarSalidas() {
  const p = Number($("parecido-boceto").value);
  $("o-boceto").textContent = p < 0.3 ? "libre" : p < 0.7 ? "equilibrado" : "fiel";
  $("o-estilo").textContent = Number($("fuerza-estilo").value).toFixed(2);
}
$("parecido-boceto").addEventListener("input", actualizarSalidas);
$("fuerza-estilo").addEventListener("input", actualizarSalidas);
$("prompt").addEventListener("input", () => { $("error-prompt").hidden = true; });

$("form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const prompt = $("prompt").value.trim();
  const error = $("error-prompt");
  if (!prompt && !tipoElegido()?.prompt) {
    error.textContent = "Escribe una descripción del personaje o elige un tipo de personaje.";
    error.hidden = false;
    $("prompt").focus();
    return;
  }
  if (!hayTrazo) {
    error.textContent = "Dibuja o importa un boceto primero.";
    error.hidden = false;
    return;
  }
  const datos = {
    boceto: lienzo.toDataURL("image/png"),
    preset: $("preset").value,
    proyecto: $("proyecto").value || undefined,
    tipo: $("tipo").value || undefined,
    ...opcionesBoceto(),
    prompt,
    variantes: Number($("variantes").value),
    modo: $("modo").value,
    parecido_boceto: Number($("parecido-boceto").value),
    motor_ia: $("motor-ia").value,
    fuerza_estilo: Number($("fuerza-estilo").value),
    semilla: $("semilla").value,
    alto_figura: $("alto-figura").value,
    colores: $("colores").value,
  };
  $("btn-generar").disabled = true;
  try {
    const { trabajo } = await api("/api/generar", datos);
    const resultado = await esperarTrabajo(trabajo);
    mostrarResultado(resultado);
    cargarGaleria();
  } catch (e) {
    $("progreso").textContent = `Error: ${e.message}`;
  } finally {
    $("btn-generar").disabled = false;
    refrescarEstado();
  }
});

async function esperarTrabajo(id) {
  const inicio = Date.now();
  for (;;) {
    const t = await api(`/api/trabajos/${id}`);
    const seg = Math.round((Date.now() - inicio) / 1000);
    if (t.estado === "listo") {
      const tr = t.resultado.info.prompt_traducido;
      $("progreso").textContent = `Listo en ${seg} s${tr ? ` · Traducido: “${tr}”` : ""} · ${t.resultado.ruta}`;
      return t.resultado;
    }
    if (t.estado === "error") throw new Error(t.error);
    $("progreso").textContent = t.estado === "en_cola"
      ? `En cola (posición ${t.posicion})…`
      : $("motor-ia").value === "gemini"
        ? `Generando con Gemini… ${seg} s`
      : $("motor-ia").value === "qwen"
        ? `Generando con Qwen… ${seg} s${seg < 90 ? " (la primera vez carga el modelo; cada variante tarda ~1 min)" : ""}`
        : `Generando… ${seg} s${seg < 40 ? " (la primera vez carga los modelos)" : ""}`;
    await new Promise((r) => setTimeout(r, 1000));
  }
}

// --- resultados ---------------------------------------------------------------

let resultado = null;

function mostrarResultado(r, indice = null) {
  resultado = r;
  $("vacio").hidden = true;
  const grid = $("variantes-grid");
  grid.innerHTML = "";
  grid.setAttribute("role", "listbox");
  for (const v of r.variantes) {
    const t = document.createElement("button");
    t.className = "tarjeta";
    t.setAttribute("role", "option");
    t.setAttribute("aria-selected", "false");
    t.dataset.indice = v.indice;
    t.innerHTML = `<img class="sprite" src="${API + v.sprite_vista}" alt="Sprite variante ${v.indice}">
      <div class="meta"><img class="ia" src="${API + v.ia}" alt="">
      ${v.origen ? `<strong>${escapar(v.nombre || (v.origen.tipo === "color" ? "Otro color" : "Rediseño"))}</strong>
        <br>de la ${v.origen.variante}` : `semilla ${v.semilla}`} · ${v.colores.length} colores</div>`;
    t.onclick = () => seleccionar(v.indice);
    grid.appendChild(t);
  }
  seleccionar(r.variantes.some((v) => v.indice === indice) ? indice : r.variantes[0].indice);
  // En pantallas estrechas los resultados quedan debajo de todo: llevarlos a la vista.
  const arriba = grid.getBoundingClientRect().top;
  if (arriba > innerHeight * 0.8 || arriba < 0) grid.scrollIntoView({ behavior: "smooth", block: "start" });
}

function seleccionar(indice) {
  for (const t of $("variantes-grid").querySelectorAll(".tarjeta")) {
    t.setAttribute("aria-selected", String(Number(t.dataset.indice) === indice));
  }
  const v = resultado.variantes.find((x) => x.indice === indice);
  abrirEditor(v);
  pintarRediseno(v);
}

// --- rediseño: otro color (sin IA) o elementos nuevos (Gemini), como variante nueva ---------

let rdSeleccion = new Set();  // colores del sprite a cambiar (vacío = todos)
let rdTemporizador = null;

function pintarRediseno(v) {
  rdSeleccion = new Set();
  const caja = $("rd-paleta");
  caja.innerHTML = "";
  for (const hex of v.colores) {
    const b = document.createElement("button");
    b.type = "button";
    b.style.background = hex;
    b.title = hex;
    b.setAttribute("aria-pressed", "false");
    b.onclick = () => {
      if (rdSeleccion.has(hex)) rdSeleccion.delete(hex); else rdSeleccion.add(hex);
      b.setAttribute("aria-pressed", String(rdSeleccion.has(hex)));
      programarRediseno();
    };
    caja.appendChild(b);
  }
  $("rd-antes").src = API + v.sprite_vista;
  $("rd-msg").textContent = "";
  programarRediseno();
}

function datosColor(extra = {}) {
  return {
    carpeta: resultado.carpeta, indice: editor.variante.indice, objetivo: $("rd-color").value,
    modo: $("rd-modo").value, saturacion: Number($("rd-int").value), brillo: Number($("rd-brillo").value),
    colores: rdSeleccion.size ? [...rdSeleccion] : undefined, ajustar: $("rd-ajustar").checked, ...extra,
  };
}

async function previsualizarRediseno() {
  if (!resultado || !editor.variante || !$("rediseno").open) return;
  try {
    const r = await api("/api/recolorear", datosColor());
    $("rd-despues").src = r.imagen;
  } catch (e) {
    $("rd-msg").textContent = `Error: ${e.message}`;
  }
}
function programarRediseno() {
  clearTimeout(rdTemporizador);
  rdTemporizador = setTimeout(previsualizarRediseno, 200);
}
for (const id of ["rd-color", "rd-modo", "rd-int", "rd-brillo", "rd-ajustar"]) {
  $(id).addEventListener("input", () => {
    $("o-rd-int").textContent = Number($("rd-int").value).toFixed(2);
    $("o-rd-brillo").textContent = Number($("rd-brillo").value).toFixed(2);
    programarRediseno();
  });
}
$("rediseno").addEventListener("toggle", programarRediseno);

function variantesNuevas(r) {  // muestra el resultado con la variante nueva elegida
  mostrarResultado(r, r.nueva);
  $("rediseno").open = true;
}

$("btn-rd-color").addEventListener("click", async () => {
  const b = $("btn-rd-color");
  b.disabled = true;
  $("rd-msg").textContent = "Guardando…";
  try {
    const r = await api("/api/recolorear", datosColor({ guardar: true, nombre: $("rd-nombre").value.trim() }));
    $("rd-nombre").value = "";
    variantesNuevas(r);
    $("rd-msg").textContent = `Variante ${r.nueva} creada. La original sigue como estaba.`;
  } catch (e) {
    $("rd-msg").textContent = `Error: ${e.message}`;
  } finally {
    b.disabled = false;
  }
});

// «Local» no sabe aplicar cambios: se usa Gemini, como siempre.
const motorRediseno = () => ($("motor-ia").value === "qwen" ? "qwen" : "gemini");

$("btn-rd-ia").addEventListener("click", async () => {
  const cambio = $("rd-cambio").value.trim();
  if (!cambio) { $("rd-msg").textContent = "Describe qué cambiar."; $("rd-cambio").focus(); return; }
  const b = $("btn-rd-ia");
  b.disabled = true;
  try {
    const { trabajo } = await api("/api/redisenar", {
      carpeta: resultado.carpeta, indice: editor.variante.indice, cambio, motor_ia: motorRediseno(),
      nombre: $("rd-nombre").value.trim() || undefined,
    });
    const inicio = Date.now();
    for (;;) {
      const t = await api(`/api/trabajos/${trabajo}`);
      if (t.estado === "listo") {
        $("rd-nombre").value = "";
        variantesNuevas(t.resultado);
        $("rd-msg").textContent = `Variante ${t.resultado.nueva} creada.`;
        break;
      }
      if (t.estado === "error") throw new Error(t.error);
      $("rd-msg").textContent = t.estado === "en_cola" ? `En cola (posición ${t.posicion})…`
        : `${motorRediseno() === "qwen" ? "Qwen" : "Gemini"} está rediseñando… ${Math.round((Date.now() - inicio) / 1000)} s`;
      await new Promise((r) => setTimeout(r, 1000));
    }
  } catch (e) {
    $("rd-msg").textContent = `Error: ${e.message}`;
  } finally {
    b.disabled = false;
  }
});

// --- editor de píxeles ------------------------------------------------------

const edLienzo = $("ed-lienzo");
const edCtx = edLienzo.getContext("2d");
const editor = { variante: null, datos: null, ancho: 0, alto: 0, zoom: 12, color: null,
                 herramienta: "pintar", historial: [], enfoque: false };

async function abrirEditor(v) {
  const cambia = !editor.variante || editor.variante.indice !== v.indice || editor.carpeta !== resultado.carpeta;
  if (cambia) reiniciarAnimacion();
  editor.variante = v;
  editor.carpeta = resultado.carpeta;
  if (cambia) cargarAnimaciones();
  $("editor").hidden = false;
  $("ed-num").textContent = v.indice;
  $("ed-mensaje").textContent = "";
  const img = await cargarImagen(API + v.sprite);
  editor.ancho = img.width;
  editor.alto = img.height;
  const tmp = new OffscreenCanvas(img.width, img.height);
  const tctx = tmp.getContext("2d");
  tctx.drawImage(img, 0, 0);
  editor.datos = tctx.getImageData(0, 0, img.width, img.height);
  editor.historial = [];
  editor.zoom = Math.max(4, Math.floor(Math.min(360 / img.width, 420 / img.height)));
  edLienzo.width = img.width * editor.zoom;
  edLienzo.height = img.height * editor.zoom;
  const p = presetActual();
  $("rp-alto").placeholder = p.sprite.alto_figura;
  $("rp-colores").placeholder = p.colores_por_sprite.objetivo;
  pintarPaleta([...new Set([...v.colores, ...p.paleta])]);
  dibujarEditor();
}

function cargarImagen(url) {
  return new Promise((ok, mal) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => ok(img);
    img.onerror = mal;
    img.src = url;
  });
}

function pintarPaleta(colores) {
  const caja = $("ed-paleta");
  caja.innerHTML = "";
  for (const c of colores) {
    const b = document.createElement("button");
    b.style.background = c;
    b.title = c;
    b.setAttribute("role", "option");
    b.setAttribute("aria-label", c);
    b.onclick = () => elegirColor(c);
    caja.appendChild(b);
  }
  elegirColor(colores[0]);
}

function elegirColor(c) {
  editor.color = c;
  for (const b of $("ed-paleta").children) b.setAttribute("aria-selected", String(b.title === c));
}

function dibujarEditor() {
  const { datos, zoom, ancho, alto } = editor;
  edCtx.clearRect(0, 0, edLienzo.width, edLienzo.height);
  for (let y = 0; y < alto; y++) {
    for (let x = 0; x < ancho; x++) {
      const i = (y * ancho + x) * 4;
      if (datos.data[i + 3] === 0) continue;
      edCtx.fillStyle = `rgb(${datos.data[i]},${datos.data[i + 1]},${datos.data[i + 2]})`;
      edCtx.fillRect(x * zoom, y * zoom, zoom, zoom);
    }
  }
  edCtx.strokeStyle = "rgba(255,255,255,0.06)";
  edCtx.lineWidth = 1;
  for (let x = 0; x <= ancho; x++) { edCtx.beginPath(); edCtx.moveTo(x * zoom + .5, 0); edCtx.lineTo(x * zoom + .5, alto * zoom); edCtx.stroke(); }
  for (let y = 0; y <= alto; y++) { edCtx.beginPath(); edCtx.moveTo(0, y * zoom + .5); edCtx.lineTo(ancho * zoom, y * zoom + .5); edCtx.stroke(); }
}

function celda(ev) {
  const r = edLienzo.getBoundingClientRect();
  const x = Math.floor((ev.clientX - r.left) / r.width * editor.ancho);
  const y = Math.floor((ev.clientY - r.top) / r.height * editor.alto);
  return x >= 0 && y >= 0 && x < editor.ancho && y < editor.alto ? { x, y } : null;
}

function aplicarEn(c) {
  if (!c) return;
  const i = (c.y * editor.ancho + c.x) * 4;
  const d = editor.datos.data;
  if (editor.herramienta === "cuenta") {
    if (d[i + 3]) elegirColor("#" + [d[i], d[i + 1], d[i + 2]].map((v) => v.toString(16).padStart(2, "0")).join(""));
    return;
  }
  if (editor.herramienta === "borrar") {
    d[i + 3] = 0;
  } else {
    const hex = editor.color.slice(1);
    d[i] = parseInt(hex.slice(0, 2), 16);
    d[i + 1] = parseInt(hex.slice(2, 4), 16);
    d[i + 2] = parseInt(hex.slice(4, 6), 16);
    d[i + 3] = 255;
  }
  dibujarEditor();
}

let edPintando = false;
edLienzo.addEventListener("pointerdown", (ev) => {
  editor.enfoque = true;
  if (editor.herramienta !== "cuenta") {
    editor.historial.push(new ImageData(new Uint8ClampedArray(editor.datos.data), editor.ancho));
  }
  edPintando = true;
  edLienzo.setPointerCapture(ev.pointerId);
  aplicarEn(celda(ev));
});
edLienzo.addEventListener("pointermove", (ev) => { if (edPintando && editor.herramienta !== "cuenta") aplicarEn(celda(ev)); });
["pointerup", "pointercancel"].forEach((t) => edLienzo.addEventListener(t, () => { edPintando = false; }));
lienzo.addEventListener("pointerdown", () => { editor.enfoque = false; });

function editorHerramienta(nombre) {
  editor.herramienta = nombre;
  for (const [id, n] of [["e-pintar", "pintar"], ["e-borrar", "borrar"], ["e-cuenta", "cuenta"]]) {
    $(id).classList.toggle("activa", n === nombre);
    $(id).setAttribute("aria-pressed", String(n === nombre));
  }
}
$("e-pintar").onclick = () => editorHerramienta("pintar");
$("e-borrar").onclick = () => editorHerramienta("borrar");
$("e-cuenta").onclick = () => editorHerramienta("cuenta");
$("e-deshacer").onclick = editorDeshacer;
function editorDeshacer() {
  const previo = editor.historial.pop();
  if (previo) { editor.datos = previo; dibujarEditor(); }
}

function spriteDataURL() {
  const c = document.createElement("canvas");
  c.width = editor.ancho;
  c.height = editor.alto;
  c.getContext("2d").putImageData(editor.datos, 0, 0);
  return c.toDataURL("image/png");
}

$("btn-repixelizar").addEventListener("click", async () => {
  const b = $("btn-repixelizar");
  b.disabled = true;
  try {
    const r = await api("/api/pixelizar", {
      carpeta: resultado.carpeta, indice: editor.variante.indice, preset: $("preset").value,
      alto_figura: $("rp-alto").value, colores: $("rp-colores").value, contorno: $("rp-contorno").checked,
    });
    const indice = editor.variante.indice;
    mostrarResultado(r);
    seleccionar(indice);
    $("ed-mensaje").textContent = "Re-pixelizado.";
  } catch (e) {
    $("ed-mensaje").textContent = `Error: ${e.message}`;
  } finally {
    b.disabled = false;
  }
});

$("btn-guardar").addEventListener("click", async () => {
  try {
    const r = await api("/api/guardar", { carpeta: resultado.carpeta, indice: editor.variante.indice,
                                          sprite: spriteDataURL() });
    $("ed-mensaje").textContent = `Guardado en ${r.ruta}`;
  } catch (e) {
    $("ed-mensaje").textContent = `Error: ${e.message}`;
  }
});

$("btn-descargar").addEventListener("click", () => {
  const a = document.createElement("a");
  a.href = spriteDataURL();
  a.download = `sprite_${editor.variante.indice}.png`;
  a.click();
});

// --- animación -----------------------------------------------------------------

const ATAQUES = new Set(["ataque", "zarpazo", "rugido", "especial"]);
const NOMBRES_ANIM = {
  reposo: "Reposo", caminar: "Caminar", ataque: "Ataque", zarpazo: "Zarpazo", rugido: "Rugido",
  especial: "Ataque especial", dano: "Daño", muerte: "Muerte",
};
const reproductor = { frames: [], duraciones: [], i: 0, temporizador: null, bucle: true };
const FLECHAS = { abajo: "↓", arriba: "↑", derecha: "→", izquierda: "←" };

function etiquetaAnim(nombre) {
  const m = nombre.match(/^(.*)_(abajo|arriba|derecha|izquierda)$/);
  const base = m ? m[1] : nombre;
  return `${NOMBRES_ANIM[base] || base}${m ? ` ${FLECHAS[m[2]]}` : ""}`;
}

// --- direcciones: vistas de frente, de espaldas y de lado ---
const VISTAS = [["abajo", "De frente ↓"], ["arriba", "De espaldas ↑"], ["lado", "De lado → (←)"]];
let vistas = {};

function pintarVistas() {
  const caja = $("dir-vistas");
  caja.innerHTML = "";
  for (const [vista, titulo] of VISTAS) {
    const v = vistas[vista];
    const f = document.createElement("figure");
    f.className = "dir-vista";
    f.innerHTML = `<figcaption>${titulo}</figcaption>
      ${v ? `<img src="${API + v.url}" alt="${titulo}">` : `<div class="dir-vacia">sin generar</div>`}
      <div class="dir-botones">
        <button type="button" class="sec" data-modo="ia" title="La IA la dibuja con la identidad de la variante">${v ? "Otra (IA)" : "IA"}</button>
        <button type="button" class="sec" data-modo="dibujo" title="La IA sigue tu dibujo del lienzo">Mi dibujo</button>
        <button type="button" class="sec" data-modo="variante" title="Usar la variante tal cual (si ya está en esta vista)">Variante</button>
      </div>`;
    for (const b of f.querySelectorAll("button")) b.onclick = () => generarVista(vista, b.dataset.modo);
    caja.appendChild(f);
  }
  // Solo se puede animar una dirección cuya vista existe.
  const sel = $("an-direccion");
  for (const o of sel.options) {
    const vista = { abajo: "abajo", arriba: "arriba", derecha: "lado" }[o.value];
    o.disabled = Boolean(vista) && !vistas[vista];
  }
  if (sel.selectedOptions[0]?.disabled) sel.value = "";
  aplicarDireccion();
}

async function cargarVistas() {
  if (!resultado || !editor.variante) return;
  vistas = (await api(`/api/direcciones/${resultado.carpeta}/${editor.variante.indice}`).catch(() => ({}))).vistas || {};
  pintarVistas();
}

async function generarVista(vista, modo) {
  if (modo === "dibujo" && !hayTrazo) {
    $("dir-progreso").textContent = "Dibuja la vista en el lienzo (1. Boceto) y pulsa «Mi dibujo».";
    return;
  }
  const botones = $("dir-vistas").querySelectorAll("button");
  botones.forEach((b) => { b.disabled = true; });
  try {
    const { trabajo } = await api("/api/direcciones", {
      carpeta: resultado.carpeta, indice: editor.variante.indice, vista, modo,
      motor_ia: $("motor-ia").value,  // el elegido en Especificaciones (Gemini y Qwen giran mejor al personaje)
      texto: $("dir-texto").value.trim() || undefined,
      boceto_pose: modo === "dibujo" ? lienzo.toDataURL("image/png") : undefined,
    });
    const inicio = Date.now();
    for (;;) {
      const t = await api(`/api/trabajos/${trabajo}`);
      if (t.estado === "listo") { vistas = t.resultado.vistas; break; }
      if (t.estado === "error") throw new Error(t.error);
      const motor = { gemini: "Gemini", qwen: "Qwen" }[$("motor-ia").value] || "La IA";
      $("dir-progreso").textContent = modo === "variante" ? "Guardando…"
        : `${motor} está dibujando la vista… ${Math.round((Date.now() - inicio) / 1000)} s`;
      await new Promise((r) => setTimeout(r, 800));
    }
    $("dir-progreso").textContent = "";
    pintarVistas();
  } catch (e) {
    $("dir-progreso").textContent = `Error: ${e.message}`;
    botones.forEach((b) => { b.disabled = false; });
  }
}

function aplicarDireccion() {
  $("an-mira-etiqueta").hidden = Boolean($("an-direccion").value);  // las vistas ya saben hacia dónde miran
}
$("an-direccion").addEventListener("change", aplicarDireccion);

function reiniciarAnimacion() {
  detenerReproductor();
  vistas = {};
  pintarVistas();
  $("an-actual").hidden = true;
  $("an-progreso").textContent = "";
  $("an-lista").innerHTML = `<p class="ayuda">Todavía no hay animaciones.</p>`;
  $("an-hoja").textContent = "";
}

function golpeElegido() { return document.querySelector('input[name="golpe"]:checked').value; }

function aplicarTipoAnimacion() {
  const ataque = ATAQUES.has($("an-tipo").value);
  $("an-golpe").hidden = !ataque;
  const golpe = ataque ? golpeElegido() : "deformacion";
  $("an-texto").hidden = !($("an-tipo").value === "especial" && golpe !== "deformacion");
  // «Parecido al personaje» solo afecta a la IA local: Qwen parte de la imagen del personaje.
  $("an-identidad").hidden = golpe === "deformacion" || $("motor-ia").value === "qwen";
  $("error-an").hidden = true;
}
$("an-tipo").addEventListener("change", aplicarTipoAnimacion);
for (const r of document.querySelectorAll('input[name="golpe"]')) r.addEventListener("change", aplicarTipoAnimacion);
$("an-texto").addEventListener("input", () => { $("error-an").hidden = true; });
$("cl-identidad").addEventListener("input", () => {
  $("o-identidad").textContent = Number($("cl-identidad").value).toFixed(2);
});

async function generarAnimacion() {
  const nombre = $("an-tipo").value;
  const golpe = ATAQUES.has(nombre) ? golpeElegido() : "deformacion";
  const error = $("error-an");
  if (golpe === "dibujo" && !hayTrazo) {
    error.textContent = "Dibuja la pose del golpe en el lienzo (1. Boceto).";
    error.hidden = false;
    return;
  }
  if (nombre === "especial" && golpe !== "deformacion" && !$("an-texto").value.trim()) {
    error.textContent = "Describe el ataque especial.";
    error.hidden = false;
    $("an-texto").focus();
    return;
  }
  const b = $("btn-animar");
  b.disabled = true;
  $("btn-otra-pose").disabled = true;
  try {
    const { trabajo } = await api("/api/animar", {
      carpeta: resultado.carpeta, indice: editor.variante.indice, animacion: nombre, golpe,
      mira: $("an-mira").value, texto: $("an-texto").value.trim(), identidad: Number($("cl-identidad").value),
      direccion: $("an-direccion").value || undefined,
      motor_ia: $("motor-ia").value === "qwen" ? "qwen" : "local",  // Gemini no dibuja poses de ataque
      boceto_pose: golpe === "dibujo" ? lienzo.toDataURL("image/png") : undefined,
    });
    const inicio = Date.now();
    let t;
    for (;;) {
      t = await api(`/api/trabajos/${trabajo}`);
      if (t.estado === "listo" || t.estado === "error") break;
      const seg = Math.round((Date.now() - inicio) / 1000);
      $("an-progreso").textContent = golpe === "deformacion"
        ? "Generando fotogramas…"
        : `${$("motor-ia").value === "qwen" ? "Qwen" : "La IA"} está dibujando la pose del golpe… ${seg} s`;
      await new Promise((r) => setTimeout(r, 800));
    }
    if (t.estado === "error") throw new Error(t.error);
    $("an-progreso").textContent = "";
    mostrarAnimaciones(t.resultado);
    const actual = t.resultado.animaciones.find((a) => a.nombre === (t.resultado.recien || nombre));
    if (actual) mostrarAnimacionActual(actual);
  } catch (e) {
    $("an-progreso").textContent = `Error: ${e.message}`;
  } finally {
    b.disabled = false;
    $("btn-otra-pose").disabled = false;
  }
}
$("btn-animar").addEventListener("click", generarAnimacion);
$("btn-otra-pose").addEventListener("click", generarAnimacion);  // nueva semilla = otra pose

function mostrarAnimacionActual(a) {
  $("an-actual").hidden = false;
  $("an-titulo").textContent = `${etiquetaAnim(a.nombre)} · ${a.fotogramas} fotogramas` +
    (a.golpe && a.golpe !== "deformacion" ? (a.golpe === "ia" ? " · golpe de la IA" : " · golpe de tu dibujo") : "");
  $("btn-otra-pose").hidden = a.golpe !== "ia";
  const tira = $("an-tira");
  tira.innerHTML = "";
  a.frames_url.forEach((url, i) => {
    const f = document.createElement("figure");
    f.innerHTML = `<img src="${API + url}" alt="Fotograma ${i + 1}"><figcaption>${i + 1} · ${a.duraciones_ms[i]} ms</figcaption>`;
    tira.appendChild(f);
  });
  reproducir(a.frames_url, a.duraciones_ms, a.bucle);
}

function detenerReproductor() {
  clearTimeout(reproductor.temporizador);
  reproductor.temporizador = null;
}

async function reproducir(urls, duraciones, bucle) {
  detenerReproductor();
  const frames = await Promise.all(urls.map((u) => cargarImagen(API + u)));
  Object.assign(reproductor, { frames, duraciones, i: 0, bucle });
  const c = $("an-reproductor");
  const escala = Math.max(2, Math.floor(260 / Math.max(frames[0].width, frames[0].height)));
  c.width = frames[0].width * escala;
  c.height = frames[0].height * escala;
  const ctx2 = c.getContext("2d");
  ctx2.imageSmoothingEnabled = false;
  const paso = () => {
    ctx2.clearRect(0, 0, c.width, c.height);
    ctx2.drawImage(reproductor.frames[reproductor.i], 0, 0, c.width, c.height);
    const espera = reproductor.duraciones[reproductor.i] || 100;
    reproductor.i += 1;
    if (reproductor.i >= reproductor.frames.length) {
      reproductor.i = 0;
      // Las animaciones sin bucle (ataque, muerte) se repiten con una pausa para poder verlas.
      reproductor.temporizador = setTimeout(paso, bucle ? espera : espera + 700);
      return;
    }
    reproductor.temporizador = setTimeout(paso, espera);
  };
  paso();
}

function mostrarAnimaciones(r) {
  const caja = $("an-lista");
  caja.innerHTML = r.animaciones.length ? "" : `<p class="ayuda">Todavía no hay animaciones.</p>`;
  for (const a of r.animaciones) {
    const f = document.createElement("figure");
    f.innerHTML = `<img src="${API + a.gif_url}" alt="Animación ${a.nombre}" title="Ver fotogramas">
      <figcaption><span>${etiquetaAnim(a.nombre)} · ${a.fotogramas}</span>
      <button type="button" class="sec" aria-label="Borrar ${a.nombre}">Borrar</button></figcaption>`;
    f.querySelector("img").onclick = () => mostrarAnimacionActual(a);
    f.querySelector("button").onclick = async () => {
      mostrarAnimaciones(await api("/api/animaciones/borrar",
        { carpeta: resultado.carpeta, indice: editor.variante.indice, animacion: a.nombre }));
      $("an-actual").hidden = true;
      detenerReproductor();
    };
    caja.appendChild(f);
  }
  $("an-hoja").innerHTML = r.hoja_url
    ? `Hoja de sprites + JSON (Aseprite) en ${escapar(r.ruta)} · <a href="${API + r.hoja_x4_url}" target="_blank" rel="noopener">ver hoja</a>`
    : "";
}

async function cargarAnimaciones() {
  if (!resultado || !editor.variante) return;
  cargarVistas();
  mostrarAnimaciones(await api(`/api/animaciones/${resultado.carpeta}/${editor.variante.indice}`));
}

// --- generar todo para el sprite elegido ---------------------------------------

$("btn-todo").addEventListener("click", async () => {
  const direcciones = [...$("todo-direcciones").querySelectorAll("input:checked")].map((i) => i.value);
  if (!direcciones.length) { $("todo-progreso").textContent = "Error: elige al menos una dirección."; return; }
  const motor = $("motor-ia").value;
  const b = $("btn-todo");
  b.disabled = true;
  $("todo-progreso").textContent = "Preparando…";
  try {
    const { trabajo } = await api("/api/generar-todo", {
      carpeta: resultado.carpeta, indice: editor.variante.indice, motor_ia: motor, direcciones,
      rehacer: $("todo-rehacer").checked, poses_ia: $("todo-poses").checked && motor !== "gemini",
      texto: $("dir-texto").value.trim() || undefined,
    });
    const inicio = Date.now();
    let t;
    for (;;) {
      t = await api(`/api/trabajos/${trabajo}`);
      if (t.estado === "listo" || t.estado === "error") break;
      const min = Math.floor((Date.now() - inicio) / 60000), seg = Math.round((Date.now() - inicio) / 1000) % 60;
      $("todo-progreso").textContent = t.estado === "en_cola" ? `En cola (posición ${t.posicion})…`
        : `${t.progreso || "Trabajando"}… ${min ? `${min} min ` : ""}${seg} s`;
      await new Promise((r) => setTimeout(r, 1000));
    }
    if (t.estado === "error") throw new Error(t.error);
    const r = t.resultado;
    await cargarAnimaciones();
    $("todo-progreso").textContent = `Listo: ${r.animaciones_hechas} de ${r.animaciones_total} animaciones y `
      + `${r.vistas.length} vistas.${r.errores.length ? ` Con problemas (${r.errores.length}): ${r.errores.join(" · ")}` : ""}`;
  } catch (e) {
    $("todo-progreso").textContent = `Error: ${e.message}`;
  } finally {
    b.disabled = false;
  }
});

// --- arranque -----------------------------------------------------------------

(async function iniciar() {
  for (let intento = 0; intento < 30; intento++) {  // en la app, el motor tarda unos segundos en arrancar
    try {
      await cargarPresets();
      await cargarProyectos();
      await cargarConfig();
      cargarAmbientesGrandes();
      break;
    } catch {
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  try { if (localStorage.getItem("forja.taller") === "mapas") elegirTaller("mapas"); } catch { /* sin almacenamiento */ }
  refrescarEstado();
  setInterval(refrescarEstado, 10000);
})();

// Señal de trabajo: mientras un texto de progreso muestre "…" (Generando…, En cola…), se anima una barra bajo él.
new MutationObserver((cambios) => {
  for (const c of cambios) {
    const el = (c.target.nodeType === 3 ? c.target.parentElement : c.target)?.closest?.(".progreso, [role=status]");
    if (el) el.classList.toggle("trabajando", el.textContent.includes("…") && !el.textContent.startsWith("Error"));
  }
}).observe(document.body, { subtree: true, childList: true, characterData: true });
