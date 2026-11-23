#!/usr/bin/env python3
"""
Simulador de sensores del Invernadero IoT
=========================================

Reemplaza al hardware (ESP32 + sensores). Cada "tick":
  1. avanza el reloj simulado (por defecto 10 minutos por tick),
  2. recalcula temperatura, humedad ambiente, humedad del suelo y luz,
  3. publica cada valor por MQTT en  invernadero/sensores/<sensor>.

También se SUSCRIBE a los actuadores que maneja Node-RED:
  invernadero/actuadores/riego        ON / OFF  -> sube la humedad del suelo
  invernadero/actuadores/ventilador   ON / OFF  -> baja la temperatura
y al tópico  invernadero/simulador/escenario  para cambiar el escenario en vivo.

Usa "última voluntad" (LWT) de MQTT: si el simulador se cae, el broker publica
"offline" en  invernadero/simulador/conexion  y Node-RED dispara una alerta.

Uso:
  apt install python3-paho-mqtt        (o: pip install -r requirements.txt)
  python3 simulador_invernadero.py --usuario invernadero --clave invernadero123
  python3 simulador_invernadero.py --usuario invernadero --clave invernadero123 --escenario ola_calor
"""

import argparse
import math
import os
import random
import sys
import time
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt

PREFIJO = "invernadero"
ESCENARIOS = {
    "normal":      "Día típico de primavera/verano",
    "ola_calor":   "Ola de calor: el exterior pasa los 40 °C",
    "sequia":      "Sustrato que se seca el doble de rápido",
    "falla_riego": "La bomba no responde: el riego no moja",
}

TOPICO_ESCENARIO = f"{PREFIJO}/simulador/escenario"
TOPICO_CONEXION = f"{PREFIJO}/simulador/conexion"
TOPICO_HORA = f"{PREFIJO}/simulador/hora"


def limitar(valor, minimo, maximo):
    return max(minimo, min(maximo, valor))


class Invernadero:
    """Modelo físico simplificado del invernadero."""

    def __init__(self, escenario="normal", minutos_por_tick=10):
        self.escenario = escenario
        self.minutos_por_tick = minutos_por_tick
        # El día simulado arranca a las 06:00 para que la demo empiece con el amanecer
        self.reloj = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
        self.temperatura = 21.0
        self.humedad_ambiente = 80.0
        self.humedad_suelo = 28.0 if escenario == "sequia" else 45.0
        self.luz = 0.0
        self.nubosidad = 0.85          # 1 = cielo despejado, 0.5 = muy nublado
        self.riego = False
        self.ventilador = False
        self.cerrando = False

    # ----------------------------------------------------------------- entorno
    def _hora_decimal(self):
        return self.reloj.hour + self.reloj.minute / 60

    def temperatura_exterior(self):
        """Ciclo diario: mínima de madrugada (~03 h), máxima a las 15 h."""
        base, amplitud = (33, 8) if self.escenario == "ola_calor" else (26, 7)
        return base + amplitud * math.sin((self._hora_decimal() - 9) / 24 * 2 * math.pi)

    def luz_solar(self):
        """Luz entre las 06 y las 20 h, con nubes que cambian de a poco."""
        h = self._hora_decimal()
        if not 6 <= h <= 20:
            return 0.0
        self.nubosidad = limitar(self.nubosidad + random.uniform(-0.05, 0.05), 0.5, 1.0)
        return max(0.0, math.sin((h - 6) / 14 * math.pi)) * 45000 * self.nubosidad

    # ------------------------------------------------------------------ física
    def avanzar(self):
        escala = self.minutos_por_tick / 10      # todo está calibrado para ticks de 10 min
        self.reloj += timedelta(minutes=self.minutos_por_tick)

        # Luz
        self.luz = self.luz_solar() + random.uniform(0, 150 if self.luz else 0)

        # Temperatura interior: tiende al exterior + efecto invernadero (sol) - ventilación
        objetivo = self.temperatura_exterior() + 4 * (self.luz / 40000)
        if self.ventilador:
            objetivo -= 5
        self.temperatura += (objetivo - self.temperatura) * min(1.0, 0.25 * escala)
        self.temperatura += random.gauss(0, 0.15)

        # Humedad ambiente: baja con el calor, sube al regar, baja al ventilar
        objetivo_ha = 95 - 1.4 * (self.temperatura - 15)
        if self.riego and self.escenario != "falla_riego":
            objetivo_ha += 5
        if self.ventilador:
            objetivo_ha -= 8
        self.humedad_ambiente += (objetivo_ha - self.humedad_ambiente) * min(1.0, 0.3 * escala)
        self.humedad_ambiente = limitar(self.humedad_ambiente + random.gauss(0, 0.4), 30, 98)

        # Humedad del suelo: se evapora con calor y sol, sube con el riego
        evaporacion = 0.15 + 0.03 * max(0.0, self.temperatura - 20) + 0.3 * (self.luz / 40000)
        if self.escenario == "sequia":
            evaporacion *= 2.5
        self.humedad_suelo -= evaporacion * escala
        if self.riego and self.escenario != "falla_riego":
            self.humedad_suelo += 4.0 * escala
        self.humedad_suelo = limitar(self.humedad_suelo + random.gauss(0, 0.1), 0, 100)

    def lecturas(self):
        return {
            "temperatura": round(self.temperatura, 1),
            "humedad_ambiente": round(self.humedad_ambiente, 1),
            "humedad_suelo": round(self.humedad_suelo, 1),
            "luz": int(round(self.luz)),
        }

    def cambiar_escenario(self, escenario):
        self.escenario = escenario
        if escenario == "sequia":
            self.humedad_suelo = min(self.humedad_suelo, 28.0)


# ======================================================================= MQTT
def crear_cliente(client_id):
    """Compatible con paho-mqtt 1.x y 2.x."""
    try:
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:
        return mqtt.Client(client_id=client_id)


def al_conectar(client, inv, flags, reason_code, properties=None):
    if reason_code == 0:
        print(f"[MQTT] Conectado al broker. Escenario: {inv.escenario}")
        client.subscribe(f"{PREFIJO}/actuadores/+")
        client.subscribe(TOPICO_ESCENARIO)
        client.publish(TOPICO_CONEXION, "online", qos=1, retain=True)   # "nacimiento"
    else:
        print(f"[MQTT] El broker rechazó la conexión (código {reason_code}). "
              "Revisá --usuario y --clave.")


def al_desconectar(client, inv, *args):
    if not inv.cerrando:
        print("[MQTT] Se perdió la conexión con el broker, reintentando...")


def al_recibir(client, inv, msg):
    payload = msg.payload.decode("utf-8", errors="ignore").strip()
    if msg.topic == f"{PREFIJO}/actuadores/riego":
        inv.riego = payload.upper() == "ON"
        print(f"   >> Riego {'ENCENDIDO' if inv.riego else 'apagado'}")
    elif msg.topic == f"{PREFIJO}/actuadores/ventilador":
        inv.ventilador = payload.upper() == "ON"
        print(f"   >> Ventilador {'ENCENDIDO' if inv.ventilador else 'apagado'}")
    elif msg.topic == TOPICO_ESCENARIO:
        if payload in ESCENARIOS:
            inv.cambiar_escenario(payload)
            print(f"   >> Escenario cambiado a '{payload}': {ESCENARIOS[payload]}")
        else:
            print(f"   >> Escenario desconocido '{payload}'. Opciones: {', '.join(ESCENARIOS)}")


def conectar(client, broker, puerto):
    while True:
        try:
            client.connect(broker, puerto, keepalive=30)
            return
        except OSError as error:
            print(f"[MQTT] No se pudo conectar a {broker}:{puerto} ({error}). Reintento en 5 s...")
            time.sleep(5)


# ======================================================================= main
def main():
    parser = argparse.ArgumentParser(description="Simulador de sensores del invernadero IoT")
    parser.add_argument("--broker", default=os.getenv("MQTT_BROKER", "localhost"),
                        help="Host del broker MQTT (ej. dietpi.local o la IP de la VM)")
    parser.add_argument("--puerto", type=int, default=int(os.getenv("MQTT_PUERTO", "1883")))
    parser.add_argument("--usuario", default=os.getenv("MQTT_USUARIO"))
    parser.add_argument("--clave", default=os.getenv("MQTT_CLAVE"))
    parser.add_argument("--intervalo", type=float, default=5,
                        help="Segundos reales entre lecturas (default 5)")
    parser.add_argument("--minutos-por-tick", type=int, default=10,
                        help="Minutos simulados por lectura (default 10: un día = 12 min reales)")
    parser.add_argument("--escenario", choices=ESCENARIOS.keys(), default="normal")
    parser.add_argument("--ticks", type=int, default=0,
                        help="Cantidad de lecturas y salir (0 = infinito)")
    args = parser.parse_args()

    inv = Invernadero(args.escenario, args.minutos_por_tick)

    client = crear_cliente(f"simulador-invernadero-{random.randint(1000, 9999)}")
    client.user_data_set(inv)
    if args.usuario:
        client.username_pw_set(args.usuario, args.clave)
    # Última voluntad: si el proceso muere sin despedirse, el broker avisa "offline"
    client.will_set(TOPICO_CONEXION, "offline", qos=1, retain=True)
    client.on_connect = al_conectar
    client.on_disconnect = al_desconectar
    client.on_message = al_recibir
    client.reconnect_delay_set(min_delay=1, max_delay=30)

    print(f"Conectando a {args.broker}:{args.puerto} ...")
    conectar(client, args.broker, args.puerto)
    client.loop_start()
    time.sleep(1)  # deja llegar los estados retenidos de los actuadores

    tick = 0
    try:
        while args.ticks == 0 or tick < args.ticks:
            inv.avanzar()
            datos = inv.lecturas()
            for sensor, valor in datos.items():
                client.publish(f"{PREFIJO}/sensores/{sensor}", str(valor), qos=0)
            client.publish(TOPICO_HORA, inv.reloj.strftime("%H:%M"))

            aviso = "" if client.is_connected() else "  << SIN CONEXIÓN: no se envió"
            print(f"[{inv.reloj:%H:%M} sim] "
                  f"T={datos['temperatura']:5.1f}°C  "
                  f"HA={datos['humedad_ambiente']:5.1f}%  "
                  f"HS={datos['humedad_suelo']:5.1f}%  "
                  f"Luz={datos['luz']:6d} lx  | "
                  f"riego {'ON ' if inv.riego else 'OFF'}  "
                  f"vent {'ON ' if inv.ventilador else 'OFF'}  [{inv.escenario}]{aviso}")
            tick += 1
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        print("\nCerrando simulador...")
    finally:
        inv.cerrando = True
        # Despedida explícita: Node-RED también lo toma como "sensores desconectados"
        info = client.publish(TOPICO_CONEXION, "offline", qos=1, retain=True)
        try:
            info.wait_for_publish(timeout=2)
        except (RuntimeError, ValueError):
            pass
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    sys.exit(main())
