# -*- coding: utf-8 -*-
"""
Sismos recientes que se pudieron sentir en Yumbo.
Fuentes: USGS (Estados Unidos) y EMSC (Europa), catálogos públicos que publican
los sismos de Colombia pocos minutos después de ocurridos.
No predice nada: solo informa sismos que YA ocurrieron.
"""
import datetime as dt
import math

import requests

YUMBO = (3.5858, -76.4958)
COL = dt.timezone(dt.timedelta(hours=-5))      # hora de Colombia (sin horario de verano)
UA = {"User-Agent": "VigiaDelAgua-Yumbo/1.0"}

DIRECCIONES = ["norte", "nororiente", "oriente", "suroriente", "sur", "suroccidente",
               "occidente", "noroccidente"]


def _distancia_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def _rumbo(a, b):
    """Dirección desde Yumbo hacia el epicentro."""
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    ang = (math.degrees(math.atan2(y, x)) + 360) % 360
    return DIRECCIONES[int((ang + 22.5) // 45) % 8]


def intensidad(mag, dist_km, prof_km):
    """Intensidad Mercalli estimada en Yumbo (relación de Bakun y Wentworth)."""
    r = max(math.hypot(dist_km, prof_km), 5.0)
    return 3.67 + 1.17 * mag - 3.19 * math.log10(r)


def describir(mmi):
    if mmi < 3:
        return "No se alcanzó a sentir"
    if mmi < 4:
        return "Leve: lo notaron algunas personas en reposo"
    if mmi < 5:
        return "Moderado: lo notó casi todo el mundo"
    if mmi < 6:
        return "Fuerte: pudo mover objetos"
    return "Muy fuerte: revisa daños en la casa"


def _romano(mmi):
    return ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"][max(0, min(9, int(mmi) - 1))]


def _usgs(desde, radio_km, mag_min):
    r = requests.get("https://earthquake.usgs.gov/fdsnws/event/1/query", headers=UA, timeout=20, params={
        "format": "geojson", "latitude": YUMBO[0], "longitude": YUMBO[1],
        "maxradiuskm": radio_km, "minmagnitude": mag_min,
        "starttime": desde.strftime("%Y-%m-%dT%H:%M:%S")})
    r.raise_for_status()
    out = []
    for f in r.json().get("features", []):
        p, g = f["properties"], f["geometry"]["coordinates"]
        out.append({"mag": float(p["mag"]), "lat": g[1], "lon": g[0], "prof": float(g[2] or 0),
                    "t": dt.datetime.fromtimestamp(p["time"] / 1000, dt.timezone.utc),
                    "url": p.get("url") or "", "fuente": "USGS"})
    return out


def _emsc(desde, radio_km, mag_min):
    r = requests.get("https://www.seismicportal.eu/fdsnws/event/1/query", headers=UA, timeout=20, params={
        "format": "json", "lat": YUMBO[0], "lon": YUMBO[1], "maxradius": radio_km / 111.0,
        "minmag": mag_min, "start": desde.strftime("%Y-%m-%dT%H:%M:%S")})
    if r.status_code == 204:
        return []
    r.raise_for_status()
    out = []
    for f in r.json().get("features", []):
        p = f["properties"]
        t = dt.datetime.fromisoformat(p["time"].replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        out.append({"mag": float(p["mag"]), "lat": float(p["lat"]), "lon": float(p["lon"]),
                    "prof": abs(float(p.get("depth") or 0)), "t": t,
                    "url": f"https://www.seismicportal.eu/eventdetails.html?unid={p.get('unid', '')}",
                    "fuente": "EMSC"})
    return out


def _mismo(a, b):
    return abs((a["t"] - b["t"]).total_seconds()) < 120 and \
        _distancia_km((a["lat"], a["lon"]), (b["lat"], b["lon"])) < 120


def revisar_sismos(vistos, mag_min=4.0, minutos=60, mmi_min=3.0, radio_km=600, log=None, info=None):
    """
    vistos: lista (se modifica) con los sismos ya avisados [{t, lat, lon}].
    Devuelve alertas listas para mostrar, solo de sismos recientes que se sintieron en Yumbo.
    """
    ahora = dt.datetime.now(dt.timezone.utc)
    desde = ahora - dt.timedelta(minutes=minutos)
    eventos = []
    fallidas = []
    for fuente in (_usgs, _emsc):
        try:
            for e in fuente(desde, radio_km, mag_min):
                if not any(_mismo(e, x) for x in eventos):
                    eventos.append(e)
        except Exception as ex:
            fallidas.append(fuente.__name__[1:].upper())
            if log:
                log(f"Sismos {fuente.__name__[1:].upper()} falló: {ex}")
    if info is not None:
        info.clear()
        info.update({"hora": ahora.astimezone(COL).isoformat(timespec="minutes"),
                     "fallidas": fallidas, "eventos": 0, "mayor": None})
        recientes = [e for e in eventos if e["t"] >= desde and e["mag"] >= mag_min]
        info["eventos"] = len(recientes)
        if recientes:
            def _mmi(e):
                return intensidad(e["mag"], _distancia_km(YUMBO, (e["lat"], e["lon"])), e["prof"])
            m = max(recientes, key=_mmi)
            info["mayor"] = {"mag": round(m["mag"], 1),
                             "dist_km": round(_distancia_km(YUMBO, (m["lat"], m["lon"]))),
                             "mmi": round(_mmi(m), 1), "sentido": _mmi(m) >= mmi_min}
    alertas = []
    for e in eventos:
        if e["t"] < desde or e["mag"] < mag_min:
            continue
        previo = [{"t": dt.datetime.fromisoformat(v["t"]), "lat": v["lat"], "lon": v["lon"]} for v in vistos]
        if any(_mismo(e, v) for v in previo):
            continue
        dist = _distancia_km(YUMBO, (e["lat"], e["lon"]))
        mmi = intensidad(e["mag"], dist, e["prof"])
        vistos.append({"t": e["t"].isoformat(), "lat": e["lat"], "lon": e["lon"]})
        if mmi < mmi_min:
            continue
        local = e["t"].astimezone(COL)
        hora = local.strftime("%I:%M %p").lstrip("0").replace("AM", "a.m.").replace("PM", "p.m.")
        donde = "muy cerca de Yumbo" if dist < 15 else f"a {dist:.0f} km al {_rumbo(YUMBO, (e['lat'], e['lon']))} de Yumbo"
        resumen = (f"Sismo de magnitud {e['mag']:.1f} a las {hora}, {donde}, "
                   f"a {e['prof']:.0f} km de profundidad. Intensidad estimada en Yumbo: "
                   f"{_romano(mmi)}. {describir(mmi)}.")
        alertas.append({
            "tipos": ["sismo"], "mag": round(e["mag"], 1), "dist_km": round(dist), "prof_km": round(e["prof"]),
            "mmi": round(mmi, 1), "hora_sismo": local.isoformat(timespec="minutes"),
            "fechas": [local.date().isoformat()], "horas": [],
            "resumen": resumen, "titulo": "", "fuente": f"Sismos ({e['fuente']})", "url": e["url"],
            "imagen": None, "detectada": dt.datetime.now(COL).isoformat(timespec="minutes"),
        })
    del vistos[:-60]   # guardar solo los últimos
    return alertas


def titulo_y_etiquetas(alerta):
    """Título y etiquetas para la notificación del celular."""
    tipos = alerta.get("tipos") or ["agua"]
    if "sismo" in tipos:
        return f"Sismo M{alerta.get('mag', '')} sentido en Yumbo", "warning,earth_americas"
    if "agua" in tipos and "energia" in tipos:
        return "Corte de agua y luz en Yumbo", "droplet,zap,warning"
    if "energia" in tipos:
        return "Corte de energía en Yumbo", "zap,warning"
    return "Corte de agua en Yumbo", "droplet,warning"
