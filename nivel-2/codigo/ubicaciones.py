"""Reconstruye dónde estuvo cada una de las doce personas entre 22:40:00 y 22:55:00 (hora de Atenas, 15/08/2026).

Entradas (rutas por argumento, nada se descarga):
  --nivel1  carpeta del nivel 1: informe-inicial.md y accesos.csv
  --nivel2  carpeta del nivel 2: celulares/ (WhatsApp .txt, Telegram result.json, SMS sms.xml) y fotos/ (JPG + indice.csv)
  --salida  ruta del JSON a escribir (por defecto ubicaciones.json)

Requiere Python 3.9+ y Pillow sólo para leer EXIF de las fotos.

Cómo produce el JSON:
  1. Normaliza todas las fuentes a hora de Atenas (UTC+3):
     - accesos.csv: Norte y Cella están en hora de Atenas; el controlador Sur atrasa. El desfase se calcula
       comparando el mismo corte de luz en los tres controladores (POWER_FAIL y POWER_RESTORE) y se suma a todo Sur.
     - WhatsApp: la exportación trae la hora local del teléfono (Atenas); las copias del mismo chat en dos
       teléfonos son idénticas, así que no hay desfase entre teléfonos.
     - Telegram: se usa date_unixtime (UTC) convertido a UTC+3; el script verifica que coincide con el campo date.
     - SMS: se usa el epoch en milisegundos convertido a UTC+3.
     - Fotos: GPSDateStamp + GPSTimeStamp (UTC) + 3 h. DateTimeOriginal no es confiable en todos los equipos
       (el Redmi de Leon tiene la fecha en 2025-01-01; la Nikon de Ariadna está en UTC+2). La posición GPS se
       asigna al sector más cercano de la tabla del informe.
  2. Arma la línea de tiempo de cada persona a partir de una tabla de tramos. Cada tramo cita las evidencias que
     lo sostienen (id de evento del registro, mensaje o foto). El script comprueba que cada evidencia citada
     existe en las fuentes normalizadas, con la hora y, si corresponde, el lugar que el tramo le atribuye.
  3. Valida que cada persona cubra 22:40:00–22:55:00 sin huecos ni superposiciones y sólo con lugares del
     informe, y escribe el JSON.
"""

import argparse
import csv
import glob
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

ATENAS = timezone(timedelta(hours=3))
DIA = "2026-08-15"
INICIO, FIN = "22:40:00", "22:55:00"
LUGARES = ["cella", "templo", "terraza", "jardín", "cocina", "puerta-norte", "puerta-sur", "estacionamiento", "afuera"]
SECTORES = {
    "cella": (37.97564, 23.72148),
    "templo": (37.97591, 23.72151),
    "terraza": (37.97582, 23.72179),
    "jardín": (37.97540, 23.72195),
    "cocina": (37.97532, 23.72146),
    "puerta-norte": (37.97630, 23.72162),
    "puerta-sur": (37.97515, 23.72135),
    "estacionamiento": (37.97656, 23.72215),
    "afuera": (37.97491, 23.72125),
}
CREDENCIAL = {
    "dafne": "AG-001", "nikos": "AG-002", "theo": "AG-003", "petros": "AG-004", "marina": "AG-005",
    "kyra": "AG-006", "ariadna": "AG-007", "eleni": "AG-008", "yannis": "AG-009", "stavros": "AG-010",
    "ioanna": "AG-011", "leon": "AG-012",
}


def hms(dt):
    return dt.astimezone(ATENAS).strftime("%H:%M:%S")


def distancia_m(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    x = (lo2 - lo1) * math.cos((la1 + la2) / 2)
    return 6371000 * math.hypot(x, la2 - la1)


def sector_mas_cercano(p):
    return min(SECTORES, key=lambda s: distancia_m(p, SECTORES[s]))


def leer_accesos(nivel1):
    filas = list(csv.DictReader(open(os.path.join(nivel1, "accesos.csv"), encoding="utf-8")))
    hora = {(f["puerta"], f["tipo"]): datetime.fromisoformat(f["timestamp"]) for f in filas if f["tipo"].startswith("POWER")}
    desfases = {hora[("Norte", t)] - hora[("Sur", t)] for t in ("POWER_FAIL", "POWER_RESTORE")}
    desfases |= {hora[("Cella", t)] - hora[("Sur", t)] for t in ("POWER_FAIL", "POWER_RESTORE")}
    if len(desfases) != 1:
        sys.exit(f"desfase de Sur inconsistente: {desfases}")
    desfase_sur = desfases.pop()
    eventos = {}
    for f in filas:
        t = datetime.fromisoformat(f["timestamp"])
        if f["puerta"] == "Sur":
            t += desfase_sur
        eventos[f["evento"]] = {"hora": t.replace(tzinfo=ATENAS), "puerta": f["puerta"], "credencial": f["credencial"],
                                "tipo": f["tipo"], "resultado": f["resultado"]}
    return eventos, desfase_sur


def leer_whatsapp(nivel2):
    mensajes = {}
    patron = re.compile(r"^(\d+)/(\d+)/(\d+), (\d+):(\d+) - ([^:]+): (.*)$")
    for ruta in sorted(glob.glob(os.path.join(nivel2, "celulares", "*", "whatsapp", "*.txt"))):
        dueno = ruta.split(os.sep)[-3]
        chat = os.path.splitext(os.path.basename(ruta))[0]
        for linea in open(ruta, encoding="utf-8"):
            m = patron.match(linea.rstrip("\n"))
            if not m:
                continue
            d, mes, a, h, mi, autor, texto = m.groups()
            t = datetime(2000 + int(a), int(mes), int(d), int(h), int(mi), tzinfo=ATENAS)
            clave = ("whatsapp", frozenset({dueno, chat}) if chat != "Staff Gala" else chat, t, autor, texto)
            mensajes[clave] = {"fuente": f"whatsapp {dueno}/{chat}", "hora": t, "autor": autor, "texto": texto}
    return list(mensajes.values())


def leer_telegram(nivel2):
    salida = []
    for ruta in sorted(glob.glob(os.path.join(nivel2, "celulares", "*", "telegram", "result.json"))):
        dueno = ruta.split(os.sep)[-3]
        datos = json.load(open(ruta, encoding="utf-8"))
        for chat in datos["chats"]["list"]:
            for m in chat["messages"]:
                t = datetime.fromtimestamp(int(m["date_unixtime"]), timezone.utc).astimezone(ATENAS)
                if t.strftime("%Y-%m-%dT%H:%M:%S") != m["date"]:
                    sys.exit(f"Telegram {ruta} id {m['id']}: date y date_unixtime no coinciden")
                texto = m["text"] if isinstance(m["text"], str) else "".join(
                    x if isinstance(x, str) else x["text"] for x in m["text"])
                salida.append({"fuente": f"telegram {dueno}/{chat['name']}#{m['id']}", "hora": t,
                               "autor": m.get("from"), "texto": texto})
    return salida


def leer_sms(nivel2):
    salida = []
    for ruta in sorted(glob.glob(os.path.join(nivel2, "celulares", "*", "sms.xml"))):
        dueno = ruta.split(os.sep)[-2]
        for s in ET.parse(ruta).getroot():
            t = datetime.fromtimestamp(int(s.get("date")) / 1000, timezone.utc).astimezone(ATENAS)
            autor = dueno if s.get("type") == "2" else s.get("contact_name")
            salida.append({"fuente": f"sms {dueno}", "hora": t, "autor": autor, "texto": s.get("body")})
    return salida


def leer_fotos(nivel2):
    from PIL import Image
    carpeta = os.path.join(nivel2, "fotos")
    dueno = {f["archivo"]: f["dueño"] for f in csv.DictReader(open(os.path.join(carpeta, "indice.csv"), encoding="utf-8"))}
    fotos = {}
    for archivo, quien in dueno.items():
        exif = Image.open(os.path.join(carpeta, archivo)).getexif()
        gps = exif.get_ifd(0x8825)
        if not gps or 29 not in gps:
            continue

        def grados(v, ref):
            x = float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600
            return -x if ref in ("S", "W") else x

        p = (grados(gps[2], gps[1]), grados(gps[4], gps[3]))
        h, m, s = (int(float(x)) for x in gps[7])
        t = datetime.strptime(gps[29], "%Y:%m:%d").replace(hour=h, minute=m, second=s, tzinfo=timezone.utc).astimezone(ATENAS)
        fotos[archivo] = {"dueño": quien, "hora": t, "lugar": sector_mas_cercano(p),
                          "distancia_m": round(distancia_m(p, SECTORES[sector_mas_cercano(p)]), 1)}
    return fotos


TRAMOS = {
    "dafne": [
        ("22:40:00", "22:46:00", "cella", ["registro:C-0143", "registro:C-0147", "foto:IMG_6152.jpg"]),
        ("22:46:00", "22:47:00", "templo", ["msg:22:46:salí, voy por el pasillo"]),
        ("22:47:00", "22:55:00", "terraza", ["msg:22:47:te veo. acá junto a la baranda", "foto:IMG_7305.jpg",
                                             "msg:22:52:estoy con Kyra todavía"]),
    ],
    "nikos": [
        ("22:40:00", "22:43:00", "cella", ["registro:C-0144", "registro:C-0146", "msg:22:40:ya corté la alarma"]),
        ("22:43:00", "22:44:00", "templo", ["registro:C-0146"]),
        ("22:44:00", "22:52:00", "puerta-norte", ["msg:22:45:estoy con Eleni y tu sobrino",
                                                  "sms:22:40:No me muevo de acá hasta que venga Nikos"]),
        ("22:52:00", "22:54:00", "templo", ["msg:22:52:ya dejo Norte. vuelvo por adentro"]),
        ("22:54:00", "22:55:00", "cella", ["registro:C-0149", "registro:C-0150"]),
    ],
    "theo": [
        ("22:40:00", "22:41:00", "templo", ["msg:22:40:estoy en el pasillo del templo"]),
        ("22:41:00", "22:53:00", "cella", ["registro:C-0145", "foto:IMG_5739.jpg", "registro:C-0148"]),
        ("22:53:00", "22:55:00", "templo", ["msg:22:53:estoy afuera de la sala con el bolso",
                                            "msg:22:54:te vi recién en el pasillo", "registro:S-0029"]),
    ],
    "petros": [
        ("22:40:00", "22:43:00", "templo", ["msg:22:41:estoy en el templo hablando con uno del museo"]),
        ("22:43:00", "22:44:00", "puerta-norte", ["msg:22:43:ya estoy en Norte", "registro:N-0066"]),
        ("22:44:00", "22:55:00", "estacionamiento", ["registro:N-0066", "foto:IMG_5128.jpg",
                                                     "msg:22:56:yo espero acá un minuto", "registro:N-0069"]),
    ],
    "marina": [
        ("22:40:00", "22:44:00", "templo", ["msg:22:41:estoy en el templo hablando con uno del museo",
                                            "msg:22:41:yo tampoco bajé todavía"]),
        ("22:44:00", "22:45:00", "puerta-norte", ["msg:22:44:llegando a la puerta", "registro:N-0067"]),
        ("22:45:00", "22:55:00", "estacionamiento", ["registro:N-0067", "msg:22:50:guardá el sobre abajo de la manta",
                                                     "msg:22:56:entro yo primero", "registro:N-0068"]),
    ],
    "kyra": [
        ("22:40:00", "22:55:00", "terraza", ["msg:22:38:yo sigo arriba con el grabador", "msg:22:44:estoy en la terraza",
                                             "foto:IMG_7305.jpg", "foto:IMG_9140.jpg"]),
    ],
    "ariadna": [
        ("22:40:00", "22:55:00", "jardín", ["msg:22:38:me planto en el jardín hasta q termine la banda"]),
    ],
    "eleni": [
        ("22:40:00", "22:55:00", "puerta-norte", ["sms:22:40:Leon sigue en Norte. No me muevo de acá",
                                                  "foto:IMG_4661.jpg", "msg:22:45:estoy con Eleni y tu sobrino"]),
    ],
    "yannis": [
        ("22:40:00", "22:55:00", "puerta-sur", ["sms:22:40:Sigo en Sur hasta relevo de las 23:30"]),
    ],
    "stavros": [
        ("22:40:00", "22:48:00", "cocina", ["msg:22:39:acá en cocina hay dos packs"]),
        ("22:48:00", "22:49:00", "puerta-sur", ["msg:22:48:estoy en Sur, vuelvo con hielo"]),
        ("22:49:00", "22:55:00", "afuera", ["registro:S-0027", "registro:S-0028"]),
    ],
    "ioanna": [
        ("22:40:00", "22:55:00", "terraza", ["msg:22:39:ya subí con las bandejas, hago terraza hasta las once",
                                             "foto:IMG_9140.jpg", "msg:22:48:sigo en terraza"]),
    ],
    "leon": [
        ("22:40:00", "22:55:00", "puerta-norte", ["sms:22:40:Leon sigue en Norte", "foto:IMG_4661.jpg",
                                                  "msg:22:46:no me estan dejando pasar"]),
    ],
}


def comprobar_evidencia(persona, desde, hasta, lugar, ref, eventos, mensajes, fotos):
    tipo, _, resto = ref.partition(":")
    if tipo == "registro":
        e = eventos[resto]
        assert e["credencial"] == CREDENCIAL[persona] and e["resultado"] == "OK", ref
        return f"{resto} {e['puerta']} {e['tipo']} {hms(e['hora'])}"
    if tipo == "foto":
        f = fotos[resto]
        assert f["lugar"] == lugar, (ref, f)
        assert desde <= hms(f["hora"]) < hasta or persona != f["dueño"], (ref, f)
        return f"{resto} de {f['dueño']} {hms(f['hora'])} en {f['lugar']} ({f['distancia_m']} m)"
    hora, texto = resto[:5], resto[6:]
    fuentes = [m for m in mensajes if m["hora"].strftime("%Y-%m-%d") == DIA and m["hora"].strftime("%H:%M") == hora
               and texto in m["texto"] and (tipo != "sms" or m["fuente"].startswith("sms"))]
    assert fuentes, ref
    m = fuentes[0]
    return f"{m['fuente']} {hms(m['hora'])} {m['autor']}: {m['texto']}"


def validar(lineas):
    assert set(lineas) == set(CREDENCIAL), "faltan o sobran personas"
    for persona, tramos in lineas.items():
        assert tramos[0]["desde"] == INICIO and tramos[-1]["hasta"] == FIN, persona
        for a, b in zip(tramos, tramos[1:]):
            assert a["hasta"] == b["desde"], (persona, a, b)
        for t in tramos:
            assert t["desde"] < t["hasta"] and t["lugar"] in LUGARES, (persona, t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nivel1", required=True)
    ap.add_argument("--nivel2", required=True)
    ap.add_argument("--salida", default="ubicaciones.json")
    args = ap.parse_args()

    eventos, desfase_sur = leer_accesos(args.nivel1)
    mensajes = leer_whatsapp(args.nivel2) + leer_telegram(args.nivel2) + leer_sms(args.nivel2)
    fotos = leer_fotos(args.nivel2)
    print(f"desfase del controlador Sur: +{desfase_sur}")

    lineas = {}
    for persona, tramos in TRAMOS.items():
        lineas[persona] = []
        for desde, hasta, lugar, refs in tramos:
            print(f"{persona:8s} {desde}-{hasta} {lugar}")
            for r in refs:
                print("           ", comprobar_evidencia(persona, desde, hasta, lugar, r, eventos, mensajes, fotos))
            lineas[persona].append({"desde": desde, "hasta": hasta, "lugar": lugar})
    validar(lineas)
    with open(args.salida, "w", encoding="utf-8") as f:
        json.dump(lineas, f, ensure_ascii=False, indent=2)
    print(f"escrito {args.salida}")


if __name__ == "__main__":
    main()
