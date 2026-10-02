# -*- coding: utf-8 -*-
"""
Vigía del Agua · Yumbo (versión nube)
Corre en GitHub Actions cada 15 min, aunque tu PC esté apagado.
Revisa noticias + Instagram de Emcali (lee las imágenes con OCR) y si hay
corte de agua en Yumbo te manda una notificación urgente al celular (ntfy).
"""
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
import unicodedata
import urllib.parse
from email.utils import parsedate_to_datetime

import requests
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
ESTADO = os.path.join(BASE, "estado.json")
TEMA = os.environ.get("NTFY_TEMA", "").strip()
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Mobile Safari/537.36")

MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
         "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
         "noviembre": 11, "diciembre": 12}

CFG = {
    "noticias": ["Yumbo agua Emcali", "Yumbo corte de agua", "Yumbo suspensión acueducto",
                 "Emcali cortes de agua Yumbo", "Yumbo sin agua", "Emcali mantenimiento Yumbo"],
    "instagram": ["emcalioficial"],
    "dias_maximos": 3,
    "palabras_agua": ["agua", "acueducto", "suspension", "suspensiones", "corte", "cortes",
                      "sin servicio", "interrupcion", "baja presion", "racionamiento", "carrotanque"],
    "palabras_yumbo": ["yumbo", "fray pena", "uribe uribe", "guacanda", "puerto isaacs",
                       "portales de yumbo", "acopi", "arroyohondo", "las cruces yumbo",
                       "belalcazar yumbo"],
}


def log(*a):
    print(dt.datetime.now().strftime("%H:%M"), *a, flush=True)


def normalizar(t):
    t = unicodedata.normalize("NFD", t or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", t.lower())


def clave(*partes):
    return hashlib.sha1("|".join(partes).encode("utf-8")).hexdigest()[:16]


# ───────────────────────── análisis del texto ─────────────────────────
DIAS_SEMANA = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4,
               "sabado": 5, "domingo": 6}
MESES_NOMBRE = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                "septiembre", "octubre", "noviembre", "diciembre"]


def fechas_en_texto(texto_norm, hoy, ref=None):
    """Fechas del aviso: '7 de octubre', 'octubre 7', 'hoy', 'mañana', 'este martes'.
    'ref' es el día en que se publicó (para noticias); por defecto hoy."""
    hoy = ref or hoy
    fechas = []
    patrones = [r"(\d{1,2})\s+de\s+(" + "|".join(MESES) + r")",
                r"(" + "|".join(MESES) + r")\s+(\d{1,2})\b"]
    for i, p in enumerate(patrones):
        for m in re.finditer(p, texto_norm):
            d, mes = (m.group(1), m.group(2)) if i == 0 else (m.group(2), m.group(1))
            try:
                f = dt.date(hoy.year, MESES[mes], int(d))
                # avisos de diciembre leídos en enero, etc.
                if (f - hoy).days > 200:
                    f = f.replace(year=hoy.year - 1)
                elif (hoy - f).days > 200:
                    f = f.replace(year=hoy.year + 1)
                fechas.append(f)
            except ValueError:
                pass
    if fechas:
        return sorted(set(fechas))  # una fecha escrita completa manda sobre "el sábado", "mañana", etc.
    for m in re.finditer(r"\b(este|esta|el|proximo|para el)\s+(" + "|".join(DIAS_SEMANA) + r")\b", texto_norm):
        delta = (DIAS_SEMANA[m.group(2)] - hoy.weekday()) % 7
        fechas.append(hoy + dt.timedelta(days=delta))
    if re.search(r"\bhoy\b", texto_norm):
        fechas.append(hoy)
    if re.search(r"\bmanana\b", texto_norm) and not re.search(r"\bde la manana\b|\ben la manana\b", texto_norm):
        fechas.append(hoy + dt.timedelta(days=1))
    return sorted(set(fechas))


def horas_en_texto(texto):
    pat = (r"(?<![\d:])(\d{1,2}:\d{2}\s*(?:a\.?\s?m\.?|p\.?\s?m\.?|horas|hrs|h\b)?"
           r"|\d{1,2}\s*(?:a\.?\s?m\.?|p\.?\s?m\.?))")
    vistos, out = set(), []
    for m in re.finditer(pat, texto, flags=re.I):
        h = re.sub(r"\s+", " ", m.group(1)).strip()
        if h.lower() not in vistos:
            vistos.add(h.lower())
            out.append(h)
    return out[:4]


def fragmento_yumbo(texto, palabras_yumbo, largo=260):
    tn = normalizar(texto)
    pos = min([tn.find(p) for p in palabras_yumbo if tn.find(p) >= 0] or [0])
    ini = max(0, pos - 60)
    frag = re.sub(r"\s+", " ", texto)[ini:ini + largo].strip()
    return ("…" if ini > 0 else "") + frag + ("…" if len(texto) > ini + largo else "")


def analizar(texto, cfg, hoy, ref=None):
    """Decide si el texto anuncia un corte de agua en Yumbo."""
    tn = normalizar(texto)
    agua = [p for p in cfg["palabras_agua"] if re.search(r"\b" + re.escape(p), tn)]
    yumbo = [p for p in cfg["palabras_yumbo"] if p in tn]
    if not agua or not yumbo:
        return None
    # 'corte' solo, sin nada de agua, puede ser de energía
    if set(agua) <= {"corte", "cortes", "suspension", "suspensiones", "sin servicio", "interrupcion"}:
        if not re.search(r"\b(agua|acueducto|hidric)", tn):
            return None
    fechas = fechas_en_texto(tn, hoy, ref)
    if fechas and max(fechas) < hoy:
        return None  # aviso viejo
    if not fechas and ref and (hoy - ref).days >= 1:
        return None  # noticia sin fecha y de ayer o antes: probablemente ya pasó
    return {
        "fechas": [f.isoformat() for f in fechas],
        "horas": horas_en_texto(texto),
        "resumen": fragmento_yumbo(texto, yumbo),
    }



def fecha_bonita(alerta):
    if alerta.get("fecha_texto"):
        return alerta["fecha_texto"]
    if not alerta.get("fechas"):
        return "Fecha por confirmar"
    dias = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    meses = MESES_NOMBRE
    hoy = dt.date.today()
    partes = []
    for s in alerta["fechas"][:2]:
        f = dt.date.fromisoformat(s)
        rel = " (hoy)" if f == hoy else " (mañana)" if f == hoy + dt.timedelta(days=1) else ""
        partes.append(f"{dias[f.weekday()].capitalize()} {f.day} de {meses[f.month - 1]}{rel}")
    return " · ".join(partes)


# ───────────────────────── fuentes ─────────────────────────
def noticias():
    items = []
    limite = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=CFG["dias_maximos"])
    urls = []
    for q in CFG["noticias"]:
        qq = urllib.parse.quote(q)
        urls.append(f"https://news.google.com/rss/search?q={qq}&hl=es-419&gl=CO&ceid=CO:es-419")
        urls.append(f"https://www.bing.com/news/search?q={qq}&format=rss&setlang=es")
    for url in urls:
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
            entradas = leer_rss(r.text)
        except Exception as e:
            log("noticias falló", url[:60], e)
            continue
        for it in entradas:
            titulo = it["title"]
            link = it["link"]
            desc = re.sub("<[^>]+>", " ", it["description"])
            try:
                f = parsedate_to_datetime(it["pubDate"])
                if f.tzinfo is None:
                    f = f.replace(tzinfo=dt.timezone.utc)
            except Exception:
                f = None
            if f and f < limite:
                continue
            items.append({"id": clave("news", normalizar(titulo)[:80]), "fuente": "Noticias",
                          "texto": titulo + ". " + desc, "titulo": titulo, "url": link,
                          "imagen": None, "ref": f.astimezone().date() if f else None})
    return items


def leer_rss(xml):
    """Lector tolerante: aguanta RSS mal formado (como el de Bing)."""
    import html
    out = []
    for bloque in re.findall(r"<item\b.*?</item>", xml, flags=re.S | re.I):
        def campo(tag):
            m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", bloque, flags=re.S | re.I)
            if not m:
                return ""
            v = re.sub(r"^<!\[CDATA\[|\]\]>$", "", m.group(1).strip())
            return html.unescape(v).strip()
        out.append({"title": campo("title"), "link": campo("link"),
                    "description": campo("description"), "pubDate": campo("pubDate")})
    return out


def instagram(estado):
    items = []
    for usuario in CFG["instagram"]:
        try:
            r = None
            for dominio in ("https://www.instagram.com", "https://i.instagram.com"):
                r = requests.get(
                    dominio + "/api/v1/users/web_profile_info/",
                    params={"username": usuario},
                    headers={"User-Agent": UA, "x-ig-app-id": "936619743392459",
                             "Accept": "application/json", "Referer": "https://www.instagram.com/"},
                    timeout=25)
                if r.status_code == 200:
                    break
            if r.status_code != 200:
                log(f"Instagram @{usuario}: respondió {r.status_code} (bloqueo temporal)")
                continue
            edges = r.json()["data"]["user"]["edge_owner_to_timeline_media"]["edges"]
        except Exception as e:
            log(f"Instagram @{usuario} falló:", e)
            continue
        for e in edges[:8]:
            n = e["node"]
            iid = clave("ig", n["shortcode"])
            if iid in estado["vistos"]:
                continue
            fecha = dt.datetime.fromtimestamp(n.get("taken_at_timestamp", time.time()))
            if (dt.datetime.now() - fecha).days > CFG["dias_maximos"]:
                estado["vistos"][iid] = time.time()
                continue
            cap = ""
            try:
                cap = n["edge_media_to_caption"]["edges"][0]["node"]["text"]
            except Exception:
                pass
            urls_img = [n.get("display_url")]
            for h in (n.get("edge_sidecar_to_children") or {}).get("edges", [])[:4]:
                urls_img.append(h["node"].get("display_url"))
            texto = cap + "\n" + (n.get("accessibility_caption") or "")
            img_bytes = None
            for u in [x for x in urls_img if x]:
                try:
                    b = requests.get(u, headers={"User-Agent": UA}, timeout=30).content
                    img_bytes = img_bytes or b
                    texto += "\n" + ocr(b)
                except Exception as ex:
                    log("imagen falló", ex)
            items.append({"id": iid, "fuente": f"Instagram @{usuario}", "texto": texto,
                          "titulo": "", "url": f"https://www.instagram.com/p/{n['shortcode']}/",
                          "imagen": img_bytes, "ref": fecha.date()})
    return items


def ocr(img_bytes):
    try:
        import pytesseract
        img = Image.open(io.BytesIO(img_bytes)).convert("L")
        if img.width < 1400:
            f = 1400 / img.width
            img = img.resize((int(img.width * f), int(img.height * f)), Image.LANCZOS)
        return pytesseract.image_to_string(img, lang="spa")
    except Exception as e:
        log("OCR falló:", e)
        return ""


# ───────────────────────── aviso al celular ─────────────────────────
def enviar(alerta):
    if not TEMA:
        log("Falta el secreto NTFY_TEMA")
        return
    partes = [fecha_bonita(alerta)]
    if alerta.get("horas"):
        partes.append(" a ".join(alerta["horas"][:2]))
    cuerpo = " · ".join(partes) + "\n" + (alerta.get("titulo") or alerta.get("resumen") or "")
    params = {"title": "Corte de agua en Yumbo", "message": cuerpo[:900], "priority": "5",
              "tags": "droplet,warning", "click": alerta.get("url") or ""}
    url = "https://ntfy.sh/" + TEMA
    if alerta.get("imagen"):
        r = requests.put(url, data=alerta["imagen"], params={**params, "filename": "aviso.jpg"}, timeout=40)
    else:
        r = requests.post(url, params=params, data=b"", timeout=20)
    log("ntfy:", r.status_code)


# ───────────────────────── ciclo ─────────────────────────
def main():
    if "--prueba" in sys.argv:
        enviar({"fechas": [(dt.date.today() + dt.timedelta(days=1)).isoformat()],
                "horas": ["8:00 a.m.", "6:00 p.m."], "url": "https://www.instagram.com/emcalioficial/",
                "resumen": "Prueba desde la nube: así te llegará un aviso real aunque el PC esté apagado."})
        return
    try:
        estado = json.load(open(ESTADO, encoding="utf-8"))
    except Exception:
        estado = {"vistos": {}, "alertas": []}
    hoy = dt.date.today()
    items = noticias() + instagram(estado)
    nuevas = 0
    for it in items:
        if it["id"] in estado["vistos"]:
            continue
        estado["vistos"][it["id"]] = time.time()
        res = analizar(it["texto"], CFG, hoy, it.get("ref"))
        if not res or repetida(res, it, estado):
            continue
        alerta = {**res, "fuente": it["fuente"], "url": it["url"], "titulo": it["titulo"],
                  "imagen": it["imagen"], "detectada": dt.datetime.now().isoformat(timespec="minutes")}
        enviar(alerta)
        alerta.pop("imagen")
        estado["alertas"].append(alerta)
        nuevas += 1
        log("ALERTA", it["fuente"], it["url"])
    if len(estado["vistos"]) > 3000:
        estado["vistos"] = dict(sorted(estado["vistos"].items(), key=lambda kv: kv[1])[-2000:])
    estado["alertas"] = estado["alertas"][-30:]
    json.dump(estado, open(ESTADO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"{len(items)} elementos revisados · {nuevas} alertas")


def repetida(res, it, estado):
    def palabras(t):
        return set(re.findall(r"[a-z]{4,}", normalizar(t)))
    nuevo = palabras(it.get("titulo") or res["resumen"])
    limite = (dt.datetime.now() - dt.timedelta(hours=48)).isoformat()
    for a in estado.get("alertas", []):
        if a.get("detectada", "") < limite:
            continue
        if res["fechas"] and a.get("fechas") == res["fechas"]:
            return True
        viejo = palabras(a.get("titulo") or a.get("resumen", ""))
        if nuevo and viejo and len(nuevo & viejo) / len(nuevo | viejo) > 0.45:
            return True
    return False


if __name__ == "__main__":
    main()
