import csv
import logging
import queue
import sqlite3
import struct
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import serial
from serial.tools import list_ports


APP_DIR = Path.home() / "PZEM Monitor"
DB_PATH = APP_DIR / "lecturas.db"
LOG_PATH = APP_DIR / "pzem_monitor.log"
LOGGER = logging.getLogger("pzem_monitor")


def configure_logging():
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not LOGGER.handlers:
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)


def modbus_crc(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_request(address: int) -> bytes:
    body = bytes((address, 0x04, 0x00, 0x00, 0x00, 0x0A))
    return body + struct.pack("<H", modbus_crc(body))


def parse_response(data: bytes, address: int) -> dict:
    if len(data) != 25:
        raise ValueError(f"Respuesta incompleta ({len(data)} de 25 bytes)")
    if data[0] != address or data[1] != 0x04 or data[2] != 20:
        raise ValueError("Respuesta Modbus no valida")
    if modbus_crc(data[:-2]) != struct.unpack("<H", data[-2:])[0]:
        raise ValueError("CRC incorrecto")

    registers = struct.unpack(">10H", data[3:23])
    return {
        "voltage": registers[0] / 10,
        "current": (registers[2] << 16 | registers[1]) / 1000,
        "power": (registers[4] << 16 | registers[3]) / 10,
        "energy": (registers[6] << 16 | registers[5]) / 1000,
        "frequency": registers[7] / 10,
        "power_factor": registers[8] / 100,
    }


class ReadingStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS readings (
            timestamp TEXT NOT NULL, voltage REAL, current REAL, power REAL,
            energy REAL, frequency REAL, power_factor REAL,
            status TEXT NOT NULL DEFAULT 'OK', detail TEXT)"""
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(readings)")}
        if "status" not in columns:
            self.connection.execute(
                "ALTER TABLE readings ADD COLUMN status TEXT NOT NULL DEFAULT 'OK'"
            )
        if "detail" not in columns:
            self.connection.execute("ALTER TABLE readings ADD COLUMN detail TEXT")
        self.connection.commit()

    def add(self, reading: dict):
        self.connection.execute(
            """INSERT INTO readings
            (timestamp, voltage, current, power, energy, frequency, power_factor, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OK')""",
            (reading["timestamp"], reading["voltage"], reading["current"],
             reading["power"], reading["energy"], reading["frequency"],
             reading["power_factor"]),
        )
        self.connection.commit()

    def add_event(self, event: dict):
        self.connection.execute(
            "INSERT INTO readings (timestamp, status, detail) VALUES (?, ?, ?)",
            (event["timestamp"], event["status"], event["detail"]),
        )
        self.connection.commit()

    def recent(self, limit=120):
        return self.connection.execute(
            """SELECT timestamp, power FROM readings
            WHERE status = 'OK' AND power IS NOT NULL
            ORDER BY rowid DESC LIMIT ?""", (limit,)
        ).fetchall()[::-1]

    def export_csv(self, destination: str):
        rows = self.connection.execute(
            """SELECT timestamp, voltage, current, power, energy, frequency,
            power_factor, status, detail FROM readings ORDER BY rowid"""
        ).fetchall()
        with open(destination, "w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("fecha", "voltaje_V", "corriente_A", "potencia_W",
                             "energia_kWh", "frecuencia_Hz", "factor_potencia",
                             "estado", "detalle"))
            writer.writerows(rows)


class PzemWorker(threading.Thread):
    def __init__(self, port, address, interval, retry_interval, messages, stop_event):
        super().__init__(daemon=True)
        self.port = port
        self.address = address
        self.interval = interval
        self.retry_interval = retry_interval
        self.messages = messages
        self.stop_event = stop_event

    def run(self):
        outage_started = None
        while not self.stop_event.is_set():
            try:
                with serial.Serial(self.port, 9600, bytesize=8, parity="N", stopbits=1,
                                   timeout=1) as connection:
                    while not self.stop_event.is_set():
                        connection.reset_input_buffer()
                        connection.write(build_request(self.address))
                        reading = parse_response(connection.read(25), self.address)
                        reading["timestamp"] = datetime.now().isoformat(
                            sep=" ", timespec="seconds"
                        )
                        if outage_started is not None:
                            duration = int(time.monotonic() - outage_started)
                            self.messages.put(("recovered", {
                                "timestamp": reading["timestamp"],
                                "status": "RECUPERADO",
                                "detail": f"Interrupcion de {duration} segundos",
                                "duration": duration,
                            }))
                            outage_started = None
                        self.messages.put(("reading", reading))
                        self.stop_event.wait(self.interval)
            except Exception as error:
                if self.stop_event.is_set():
                    break
                if outage_started is None:
                    outage_started = time.monotonic()
                event = {
                    "timestamp": datetime.now().isoformat(sep=" ", timespec="seconds"),
                    "status": "SIN_RESPUESTA",
                    "detail": str(error),
                }
                self.messages.put(("outage", event))
                self.stop_event.wait(self.retry_interval)


class MonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PZEM-004T Monitor | INTECSA")
        self.geometry("1000x650")
        self.minsize(850, 560)
        configure_logging()
        self.store = ReadingStore(DB_PATH)
        self.messages = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.outage_active = False
        self.values = {}
        self._build_ui()
        self.refresh_ports()
        self.draw_graph()
        self.after(100, self.process_messages)
        self.protocol("WM_DELETE_WINDOW", self.close)
        LOGGER.info("Aplicacion iniciada")

    def _build_ui(self):
        controls = ttk.LabelFrame(self, text="Configuracion", padding=10)
        controls.pack(fill="x", padx=12, pady=(10, 0))
        ttk.Label(controls, text="Puerto COM:").pack(side="left")
        self.port = ttk.Combobox(controls, width=15, state="readonly")
        self.port.pack(side="left", padx=5)
        ttk.Button(controls, text="Actualizar", command=self.refresh_ports).pack(side="left")
        ttk.Label(controls, text="Direccion:").pack(side="left", padx=(15, 2))
        self.address = tk.IntVar(value=1)
        ttk.Spinbox(controls, from_=1, to=247, textvariable=self.address, width=5).pack(side="left")
        ttk.Label(controls, text="Medicion (s):").pack(side="left", padx=(15, 2))
        self.interval = tk.DoubleVar(value=3)
        ttk.Spinbox(controls, from_=1, to=3600, textvariable=self.interval, width=6).pack(side="left")
        ttk.Label(controls, text="Reconexion (s):").pack(side="left", padx=(15, 2))
        self.retry_interval = tk.DoubleVar(value=5)
        ttk.Spinbox(
            controls, from_=1, to=3600, textvariable=self.retry_interval, width=6
        ).pack(side="left")
        self.connect_button = ttk.Button(controls, text="CONECTAR", command=self.toggle_connection)
        self.connect_button.pack(side="right")

        self.alert = tk.StringVar(value="Listo para conectar")
        self.alert_label = tk.Label(
            self, textvariable=self.alert, fg="#555555", font=("Segoe UI", 10, "bold")
        )
        self.alert_label.pack(fill="x", padx=20, pady=(6, 0))

        readings = ttk.Frame(self, padding=(20, 10))
        readings.pack(fill="x")
        fields = (("voltage", "Voltaje", "V"), ("current", "Corriente", "A"),
                  ("power", "Potencia", "W"), ("energy", "Energia", "kWh"),
                  ("frequency", "Frecuencia", "Hz"),
                  ("power_factor", "Factor de potencia", ""))
        for index, (key, label, unit) in enumerate(fields):
            box = ttk.LabelFrame(readings, text=label, padding=10)
            box.grid(row=index // 3, column=index % 3, padx=6, pady=6, sticky="nsew")
            readings.columnconfigure(index % 3, weight=1)
            value = tk.StringVar(value=f"-- {unit}")
            self.values[key] = (value, unit)
            ttk.Label(box, textvariable=value, font=("Segoe UI", 18)).pack()

        graph_box = ttk.LabelFrame(self, text="Potencia reciente", padding=8)
        graph_box.pack(fill="both", expand=True, padx=20, pady=8)
        self.canvas = tk.Canvas(graph_box, background="white", height=230)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.draw_graph())

        footer = ttk.Frame(self, padding=10)
        footer.pack(fill="x")
        self.status = tk.StringVar(value=f"Datos: {DB_PATH}")
        ttk.Label(footer, textvariable=self.status).pack(side="left")
        ttk.Button(footer, text="Exportar CSV", command=self.export_csv).pack(side="right")
        ttk.Label(footer, text="Made by INTECSA", font=("Segoe UI", 9, "bold")).pack(
            side="right", padx=16
        )

    def refresh_ports(self):
        ports = [port.device for port in list_ports.comports()]
        self.port["values"] = ports
        if ports and self.port.get() not in ports:
            self.port.set(ports[0])

    def toggle_connection(self):
        if self.worker and self.worker.is_alive():
            self.disconnect()
            return
        if not self.port.get():
            messagebox.showwarning("Puerto COM", "Selecciona un puerto COM.")
            return
        try:
            address = self.address.get()
            interval = self.interval.get()
            retry_interval = self.retry_interval.get()
            if interval < 1 or retry_interval < 1:
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showwarning(
                "Configuracion", "Direccion e intervalos deben contener valores validos."
            )
            return
        self.stop_event.clear()
        self.worker = PzemWorker(
            self.port.get(), address, interval, retry_interval, self.messages, self.stop_event
        )
        self.worker.start()
        self.connect_button.configure(text="DETENER")
        self.alert.set("Monitoreando")
        self.alert_label.configure(fg="#16733b")
        LOGGER.info(
            "Monitoreo iniciado en %s; medicion=%ss; reconexion=%ss",
            self.port.get(), interval, retry_interval
        )

    def disconnect(self, ask=True):
        if ask and not messagebox.askyesno(
            "Detener monitoreo",
            "Se dejara de registrar mientras el monitoreo este detenido.\n\n"
            "Las lecturas, eventos y graficas ya guardados NO se perderan.\n\n"
            "¿Deseas continuar?",
        ):
            return
        self.stop_event.set()
        self.connect_button.configure(text="CONECTAR")
        self.status.set(f"Monitoreo detenido. Datos conservados en {DB_PATH}")
        self.alert.set("Monitoreo detenido")
        self.alert_label.configure(fg="#555555")
        LOGGER.info("Monitoreo detenido por el usuario")

    def process_messages(self):
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "reading":
                    self.store.add(payload)
                    LOGGER.info(
                        "LECTURA | V=%.2f | A=%.3f | W=%.2f | kWh=%.4f | Hz=%.2f | FP=%.2f",
                        payload["voltage"], payload["current"], payload["power"],
                        payload["energy"], payload["frequency"], payload["power_factor"]
                    )
                    for key, (variable, unit) in self.values.items():
                        decimals = 4 if key == "energy" else 3 if key == "current" else 2
                        variable.set(f"{payload[key]:.{decimals}f} {unit}")
                    self.status.set(f"Ultima lectura: {payload['timestamp']}")
                    self.draw_graph()
                elif kind == "outage":
                    self.store.add_event(payload)
                    LOGGER.warning("SIN_RESPUESTA | %s", payload["detail"])
                    for variable, unit in self.values.values():
                        variable.set(f"SIN RESPUESTA {unit}")
                    self.status.set(
                        f"Sin respuesta; nuevo intento en {self.retry_interval.get():g} s"
                    )
                    if not self.outage_active:
                        self.outage_active = True
                        self.bell()
                    self.alert.set("⚠ PZEM SIN RESPUESTA — reintentando automaticamente")
                    self.alert_label.configure(fg="#b42318")
                elif kind == "recovered":
                    self.store.add_event(payload)
                    self.outage_active = False
                    LOGGER.info("RECUPERADO | %s", payload["detail"])
                    self.alert.set("Conexion recuperada — monitoreando")
                    self.alert_label.configure(fg="#16733b")
                    self.status.set(
                        f"Conexion recuperada; corte de {payload['duration']} segundos"
                    )
        except queue.Empty:
            pass
        self.after(100, self.process_messages)

    def draw_graph(self):
        self.canvas.delete("all")
        rows = self.store.recent()
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        margin = 35
        if len(rows) < 2 or width <= 2 * margin:
            self.canvas.create_text(width / 2, height / 2, text="Esperando lecturas...")
            return
        powers = [row[1] for row in rows]
        maximum = max(max(powers), 1)
        points = []
        for index, power in enumerate(powers):
            x = margin + index * (width - 2 * margin) / (len(powers) - 1)
            y = height - margin - power * (height - 2 * margin) / maximum
            points.extend((x, y))
        self.canvas.create_line(margin, margin, margin, height - margin, fill="#777")
        self.canvas.create_line(margin, height - margin, width - margin, height - margin, fill="#777")
        self.canvas.create_line(*points, fill="#1677c8", width=2)
        self.canvas.create_text(margin, margin - 8, text=f"{maximum:.1f} W", anchor="w")

    def export_csv(self):
        name = f"lecturas_{datetime.now():%Y%m%d_%H%M%S}.csv"
        destination = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=name,
                                                   filetypes=(("CSV", "*.csv"),))
        if destination:
            self.store.export_csv(destination)
            messagebox.showinfo("Exportacion", "Archivo CSV creado correctamente.")

    def close(self):
        if self.worker and self.worker.is_alive() and not messagebox.askyesno(
            "Cerrar aplicacion",
            "Al cerrar se dejara de registrar hasta volver a abrir la aplicacion.\n\n"
            "Todo el historial ya guardado se conservara. ¿Deseas cerrar?",
        ):
            return
        self.stop_event.set()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=2)
        LOGGER.info("Aplicacion cerrada")
        self.store.connection.close()
        self.destroy()


if __name__ == "__main__":
    MonitorApp().mainloop()
