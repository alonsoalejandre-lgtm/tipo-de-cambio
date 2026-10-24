"""
Captura diaria del tipo de cambio peso-dolar de BBVA y Banregio.
Cada dia crea una carpeta con la fecha (ej. 2026-10-26) y guarda ahi
"2026-10-26 BBVA.png" y "2026-10-26 Banregio.png".
Tambien anota compra y venta en historial.csv (se abre con Excel).
"""
import csv
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

# Si alguna pagina no es la que consultas, cambia aqui la direccion.
BANCOS = {
    "BBVA": "https://www.bbva.mx/personas/informacion-financiera-al-dia.html",
    "Banregio": "https://www.banregio.com/divisas.php",
}

# Uso: python capturar.py <carpeta> [Banco ...]
# Sin bancos, captura todos. Ej: python capturar.py capturas Banregio
BASE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("capturas")
ELEGIDOS = sys.argv[2:] or list(BANCOS)
HISTORIAL = BASE / ("historial.csv" if set(ELEGIDOS) == set(BANCOS) else "historial " + " ".join(ELEGIDOS) + ".csv")
COLUMNAS = ["fecha", "hora", "banco", "compra", "venta", "estado"]

BOTONES_COOKIES = ["Aceptar", "Aceptar todas", "Aceptar cookies", "Entendido", "Acepto", "Cerrar"]


def cerrar_avisos(page):
    for texto in BOTONES_COOKIES:
        try:
            boton = page.get_by_role("button", name=texto, exact=False).first
            if boton.is_visible(timeout=800):
                boton.click(timeout=1500)
                page.wait_for_timeout(500)
        except Exception:
            pass


BUSCAR_RECUADRO = r"""
() => {
  // Recorre toda la pagina, incluidos los componentes con "shadow DOM" (como los de BBVA)
  const textos = new Map();
  const elementos = [];
  const visitar = (nodo) => {
    if (nodo.nodeType === 3) return nodo.data;
    if (nodo.nodeType !== 1 && nodo.nodeType !== 11) return '';
    const tag = nodo.nodeType === 1 ? nodo.tagName : '';
    if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT') return '';
    let t = '';
    if (nodo.shadowRoot) t += ' ' + visitar(nodo.shadowRoot);
    const hijos = (tag === 'SLOT' && nodo.assignedNodes().length) ? [] : nodo.childNodes;
    for (const h of hijos) t += ' ' + visitar(h);
    if (nodo.nodeType === 1) { textos.set(nodo, t); elementos.push(nodo); }
    return t;
  };
  visitar(document.documentElement);

  const tieneNum = t => /\d{2}\.\d{2}/.test(t);
  const buscar = (conEuro) => {
    let mejor = null, mejorArea = Infinity;
    for (const el of elementos) {
      const t = textos.get(el).toLowerCase();
      if (!/d[oó]lar/.test(t) || !t.includes('compra') || !t.includes('venta') || !tieneNum(t)) continue;
      if (conEuro && !t.includes('euro')) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 150 || r.height < 40) continue;
      const area = r.width * r.height;
      if (area < mejorArea) { mejor = r; mejorArea = area; }
    }
    return mejor;
  };
  const caja = buscar(true) || buscar(false);
  if (!caja) return null;
  let top = caja.top, left = caja.left, right = caja.right, bottom = caja.bottom;
  // Incluir el titulo "Divisas..." de arriba (y la fecha que trae debajo)
  for (const el of elementos) {
    const t = textos.get(el).replace(/\s+/g, ' ').trim();
    if (t.length > 60 || !/divisas/i.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || r.height > 200) continue;
    if (r.bottom <= caja.top + 5 && caja.top - r.top < 500 && r.top < top) {
      top = r.top; left = Math.min(left, r.left); right = Math.max(right, r.right);
    }
  }
  return {x: left + window.scrollX, y: top + window.scrollY,
          left: left, top: top, width: right - left, height: bottom - top};
}
"""


def texto_de_todo(page):
    """Texto de la pagina y de todos sus marcos internos (iframes)."""
    partes = []
    for frame in page.frames:
        try:
            partes.append(frame.evaluate(TEXTO_PAGINA))
        except Exception:
            pass
    return " ".join(partes)


def esperar_datos(page, segundos=25):
    """Espera a que aparezcan los numeros del dolar (algunas paginas los cargan tarde)."""
    for _ in range(segundos // 2):
        t = " ".join(texto_de_todo(page).split()).lower()
        if re.search(r"d[oó]lar", t) and "compra" in t and re.search(r"\d{2}\.\d{2}", t):
            return True
        page.wait_for_timeout(2000)
    return False


def buscar_caja(page):
    """Busca el recuadro en la pagina y en sus marcos; devuelve coordenadas de la pagina completa."""
    scroll = page.evaluate("[window.scrollX, window.scrollY]")
    for frame in page.frames:
        try:
            caja = frame.evaluate(BUSCAR_RECUADRO)
        except Exception:
            continue
        if not caja or caja["width"] <= 0 or caja["height"] <= 0:
            continue
        if frame == page.main_frame:
            return caja
        try:
            marco = frame.frame_element().bounding_box()
        except Exception:
            marco = None
        if not marco:
            continue
        return {
            "x": marco["x"] + caja["left"] + scroll[0],
            "y": marco["y"] + caja["top"] + scroll[1],
            "width": caja["width"],
            "height": caja["height"],
        }
    return None


def capturar_recuadro(page, archivo):
    """Recorta solo el recuadro del tipo de cambio. Si no lo encuentra, captura la pagina completa."""
    try:
        caja = buscar_caja(page)
    except Exception:
        caja = None
    if caja:
        m = 25  # margen alrededor del recuadro
        clip = {
            "x": max(caja["x"] - m, 0),
            "y": max(caja["y"] - m, 0),
            "width": caja["width"] + 2 * m,
            "height": caja["height"] + 2 * m,
        }
        page.screenshot(path=str(archivo), clip=clip, full_page=True)
        return "recorte"
    page.screenshot(path=str(archivo), full_page=True)
    return "pagina completa"


def diagnostico(page, banco):
    """Notas para revisar si algo falla (no afecta las capturas)."""
    lineas = [f"== {banco} ==", f"url final: {page.url}"]
    for frame in page.frames:
        try:
            t = " ".join(frame.evaluate(TEXTO_PAGINA).split())
        except Exception as e:
            t = f"(no se pudo leer: {type(e).__name__})"
        m = re.search(r"d[oó]lar", t, re.IGNORECASE)
        extracto = t[m.start(): m.start() + 200] if m else t[:200]
        lineas.append(f"- marco {frame.url[:120]} | {len(t)} letras | {extracto}")
    return "\n".join(lineas)


TEXTO_PAGINA = r"""
() => {
  const visitar = (n) => {
    if (n.nodeType === 3) return n.data;
    if (n.nodeType !== 1 && n.nodeType !== 11) return '';
    if (n.nodeType === 1 && ['SCRIPT','STYLE','NOSCRIPT'].includes(n.tagName)) return '';
    let t = n.shadowRoot ? ' ' + visitar(n.shadowRoot) : '';
    const hijos = (n.tagName === 'SLOT' && n.assignedNodes().length) ? [] : n.childNodes;
    for (const h of hijos) t += ' ' + visitar(h);
    return t;
  };
  return visitar(document.body);
}
"""


def leer_dolar(texto):
    """Intenta encontrar compra y venta del dolar en el texto de la pagina."""
    texto = " ".join(texto.split())
    idx = re.search(r"d[oó]lar", texto, re.IGNORECASE)
    zona = texto[idx.start(): idx.start() + 600] if idx else texto
    compra = re.search(r"compra\D{0,40}?(\d{2}\.\d{2,4})", zona, re.IGNORECASE)
    venta = re.search(r"venta\D{0,40}?(\d{2}\.\d{2,4})", zona, re.IGNORECASE)
    if compra and venta:
        return compra.group(1), venta.group(1)
    nums = re.findall(r"\b\d{2}\.\d{2,4}\b", zona)
    if len(nums) >= 2:
        return nums[0], nums[1]
    return "", ""


def main():
    ahora = datetime.now()
    fecha = ahora.strftime("%Y-%m-%d")
    hora = ahora.strftime("%H:%M")
    destino = BASE / fecha
    destino.mkdir(parents=True, exist_ok=True)

    filas = []
    notas = []
    with sync_playwright() as p:
        # Se presenta como un Chrome normal de Windows: algunos bancos
        # muestran "pagina no encontrada" a los navegadores automaticos.
        navegador = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        contexto = navegador.new_context(
            viewport={"width": 1366, "height": 900},
            locale="es-MX",
            timezone_id="America/Mexico_City",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "es-MX,es;q=0.9,en;q=0.8"},
        )
        contexto.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        for banco in ELEGIDOS:
            url = BANCOS[banco]
            archivo = destino / f"{fecha} {banco}.png"
            page = contexto.new_page()
            estado = "ok"
            compra = venta = ""
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    page.wait_for_load_state("networkidle", timeout=20000)
                except Exception:
                    pass
                page.wait_for_timeout(3000)
                cerrar_avisos(page)
                esperar_datos(page)
                estado = capturar_recuadro(page, archivo)
                compra, venta = leer_dolar(texto_de_todo(page))
            except Exception as e:
                estado = f"error: {type(e).__name__}"
                try:
                    page.screenshot(path=str(archivo), full_page=True)
                except Exception:
                    pass
            try:
                notas.append(diagnostico(page, banco))
            except Exception:
                pass
            filas.append([fecha, hora, banco, compra, venta, estado])
            print(banco, compra, venta, estado)
            page.close()
        navegador.close()

    if notas:
        (BASE / "diagnostico.txt").write_text("\n\n".join(notas), encoding="utf-8")
    if not filas:
        return
    # Una fila por banco y por dia: si se vuelve a correr el mismo dia, se reemplaza
    previas = []
    if HISTORIAL.exists():
        with HISTORIAL.open(newline="", encoding="utf-8-sig") as f:
            previas = [r for r in csv.reader(f)][1:]
    nuevas = {(r[0], r[2]) for r in filas}
    previas = [r for r in previas if len(r) >= 3 and (r[0], r[2]) not in nuevas]
    todas = sorted(previas + filas, key=lambda r: (r[0], r[2]))
    # utf-8-sig para que Excel muestre bien los acentos
    with HISTORIAL.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(COLUMNAS)
        w.writerows(todas)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        BASE.mkdir(parents=True, exist_ok=True)
        with (BASE / "errores.txt").open("a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M} ---\n{traceback.format_exc()}")
        raise
