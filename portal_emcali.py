# -*- coding: utf-8 -*-
"""
Reparaciones y mantenimientos publicados en el portal de Emcali.
Las publicaciones diarias de Facebook ("Aquí te informamos si tu barrio...") enlazan a
emcali.com.co/noticias-detalles, que carga sus noticias de este archivo:
    https://portalemcali.onrender.com/data/news.json
Ahí está el texto completo con todos los barrios, separado por Acueducto, Energía y
Alcantarillado. Es una consulta liviana: no necesita navegador ni sesión.
"""
import datetime as dt
import hashlib
import re

import requests

URL_DATOS = "https://portalemcali.onrender.com/data/news.json"
URL_NOTICIA = "https://portalemcali.onrender.com/noticias-detalles?id="   # en emcali.com.co sale "no encontrada"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126 Safari/537.36"}
TITULO_SECCION = re.compile(r"^\s*(ACUEDUCTO|ENERG[IÍ]A|ALCANTARILLADO|ALUMBRADO( P[UÚ]BLICO)?|TELCO|"
                            r"TELECOMUNICACIONES)\s*:?\s*$", re.I | re.M)


def secciones(texto):
    """Parte el texto en bloques que empiezan por su título (ACUEDUCTO, ENERGÍA...)."""
    cortes = [m.start() for m in TITULO_SECCION.finditer(texto)]
    if not cortes:
        return [texto]
    partes = [texto[:cortes[0]]] if texto[:cortes[0]].strip() else []
    for a, b in zip(cortes, cortes[1:] + [len(texto)]):
        partes.append(texto[a:b].strip())
    return partes


def resumen_yumbo(texto, palabras, normalizar):
    """'Acueducto: Acopi (Carrera 37 entre...); Juan Pablo - Yumbo (Calle 15 # 17 - 124, pendiente por reparación)'."""
    lineas = [x.strip() for x in texto.splitlines()]
    out, seccion, estado = [], "", ""
    for i, ln in enumerate(lineas):
        if not ln:
            continue
        if TITULO_SECCION.match(ln):
            seccion, estado = ln.capitalize().rstrip(":"), ""
            continue
        if ln.endswith(":") and not ln.startswith("*"):
            estado = ln.rstrip(":").strip().lower()
            continue
        if any(p in normalizar(ln) for p in palabras):
            ant = next((x for x in reversed(lineas[:i]) if x), "")
            if not ln.startswith("*") and ant.startswith("*"):     # la línea de detalle de un punto
                nombre, sig = ant.lstrip("*• ").strip(), ln
            else:
                nombre = ln.lstrip("*• ").strip()
                sig = lineas[i + 1] if i + 1 < len(lineas) and lineas[i + 1] and not lineas[i + 1].startswith("*") else ""
            det = ", ".join(x for x in (sig, estado if "pendiente" in estado else "") if x)
            out.append((seccion, nombre + (f" ({det})" if det else "")))
    if not out:
        return ""
    grupos = {}
    for sec, txt in out:
        grupos.setdefault(sec, []).append(txt)
    return " · ".join((f"{sec}: " if sec else "") + "; ".join(v) for sec, v in grupos.items())


def revisar_portal(dias=2, info=None, timeout=30):
    """Noticias de reparaciones de los últimos 'dias' (y las de mañana, si ya están)."""
    if info is not None:
        info.clear()
        info.update({"ok": False, "noticias": 0, "error": None})
    try:
        r = requests.get(URL_DATOS, headers=UA, timeout=timeout)
        r.raise_for_status()
        datos = r.json()
    except Exception as e:
        if info is not None:
            info["error"] = f"{type(e).__name__}: {e}"[:200]
        raise
    noticias = datos.get("news", datos) if isinstance(datos, dict) else datos
    desde = (dt.date.today() - dt.timedelta(days=dias)).isoformat()
    items = []
    for n in noticias:
        fecha = str(n.get("date") or "")[:10]
        if fecha < desde or n.get("active") is False:
            continue
        titulo = n.get("title") or ""
        cuerpo = n.get("fullContent") or n.get("description") or ""
        if not (n.get("isRepair") or re.search(r"repara|manten|suspensi|corte|interrup|moderniza",
                                               titulo + cuerpo[:300], re.I)):
            continue
        huella = hashlib.sha1(cuerpo.encode("utf-8")).hexdigest()[:8]
        items.append({
            "id": hashlib.sha1(f"portal|{n.get('id')}|{huella}".encode()).hexdigest()[:16],
            "fuente": "Portal Emcali", "titulo": "", "nombre": titulo,
            "texto": titulo + "\n" + cuerpo, "partes": secciones(cuerpo),
            "url": URL_NOTICIA + str(n.get("id", "")), "imagen": None, "con_fecha": True,
            "ref": fecha or None,
        })
    if info is not None:
        info.update({"ok": True, "noticias": len(items)})
    return items
