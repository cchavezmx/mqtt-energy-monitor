import csv
import logging
import os
import queue
import re
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
CSV_DIR = APP_DIR / "csv"
LOGGER = logging.getLogger("pzem_monitor")

COLORS = {
    "background": "#111827",
    "panel": "#1f2937",
    "field": "#0f172a",
    "foreground": "#e5e7eb",
    "muted": "#9ca3af",
    "accent": "#22c55e",
    "accent_active": "#16a34a",
    "selection": "#1d4ed8",
    "grid": "#64748b",
    "graph": "#38bdf8",
}


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
        self.csv_dir = path.parent / "csv"
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS readings (
            timestamp TEXT NOT NULL, voltage REAL, current REAL, power REAL,
            energy REAL, frequency REAL, power_factor REAL,
            status TEXT NOT NULL DEFAULT 'OK', detail TEXT,
            source_tag TEXT NOT NULL DEFAULT 'Principal', port TEXT)"""
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(readings)")}
        if "status" not in columns:
            self.connection.execute(
                "ALTER TABLE readings ADD COLUMN status TEXT NOT NULL DEFAULT 'OK'"
            )
        if "detail" not in columns:
            self.connection.execute("ALTER TABLE readings ADD COLUMN detail TEXT")
        if "source_tag" not in columns:
            self.connection.execute(
                "ALTER TABLE readings ADD COLUMN source_tag TEXT NOT NULL DEFAULT 'Principal'"
            )
        if "port" not in columns:
            self.connection.execute("ALTER TABLE readings ADD COLUMN port TEXT")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.commit()

    def _csv_path(self, source_tag: str) -> Path:
        safe_tag = re.sub(r"[^A-Za-z0-9._-]+", "_", source_tag).strip("._") or "puerto"
        return self.csv_dir / f"lecturas_{safe_tag}.csv"

    def _append_csv(self, row: tuple, source_tag: str):
        path = self._csv_path(source_tag)
        path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not path.exists() or path.stat().st_size == 0
        with open(path, "a", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            if needs_header:
                writer.writerow(("fecha", "etiqueta", "puerto", "voltaje_V", "corriente_A",
                                 "potencia_W", "energia_kWh", "frecuencia_Hz",
                                 "factor_potencia", "estado", "detalle"))
            writer.writerow(row)
            output.flush()
            os.fsync(output.fileno())

    def add(self, reading: dict):
        self.connection.execute(
            """INSERT INTO readings
            (timestamp, voltage, current, power, energy, frequency, power_factor,
             status, source_tag, port)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OK', ?, ?)""",
            (reading["timestamp"], reading["voltage"], reading["current"],
             reading["power"], reading["energy"], reading["frequency"],
             reading["power_factor"], reading.get("source_tag", "Principal"),
             reading.get("port")),
        )
        self.connection.commit()
        self._append_csv((reading["timestamp"], reading.get("source_tag", "Principal"),
                          reading.get("port"), reading["voltage"], reading["current"],
                          reading["power"], reading["energy"], reading["frequency"],
                          reading["power_factor"], "OK", ""),
                         reading.get("source_tag", "Principal"))

    def add_event(self, event: dict):
        self.connection.execute(
            """INSERT INTO readings (timestamp, status, detail, source_tag, port)
            VALUES (?, ?, ?, ?, ?)""",
            (event["timestamp"], event["status"], event["detail"],
             event.get("source_tag", "Principal"), event.get("port")),
        )
        self.connection.commit()
        self._append_csv((event["timestamp"], event.get("source_tag", "Principal"),
                          event.get("port"), "", "", "", "", "", "",
                          event["status"], event["detail"]),
                         event.get("source_tag", "Principal"))

    def recent(self, source_tag=None, limit=120):
        if source_tag is None:
            condition, parameters = "", (limit,)
        else:
            condition, parameters = "AND source_tag = ?", (source_tag, limit)
        return self.connection.execute(
            """SELECT timestamp, power FROM readings
            WHERE status = 'OK' AND power IS NOT NULL """ + condition +
            " ORDER BY rowid DESC LIMIT ?", parameters
        ).fetchall()[::-1]

    def export_csv(self, destination: str):
        rows = self.connection.execute(
            """SELECT timestamp, source_tag, port, voltage, current, power, energy,
            frequency, power_factor, status, detail FROM readings ORDER BY rowid"""
        ).fetchall()
        with open(destination, "w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("fecha", "etiqueta", "puerto", "voltaje_V", "corriente_A",
                             "potencia_W", "energia_kWh", "frecuencia_Hz",
                             "factor_potencia", "estado", "detalle"))
            writer.writerows(rows)


class PzemWorker(threading.Thread):
    def __init__(self, monitor_id, source_tag, port, address, interval, retry_interval,
                 messages, stop_event):
        super().__init__(daemon=True)
        self.monitor_id = monitor_id
        self.source_tag = source_tag
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
                        reading.update(source_tag=self.source_tag, port=self.port,
                                       monitor_id=self.monitor_id)
                        if outage_started is not None:
                            duration = int(time.monotonic() - outage_started)
                            self.messages.put(("recovered", {
                                "timestamp": reading["timestamp"],
                                "status": "RECUPERADO",
                                "detail": f"Interrupcion de {duration} segundos",
                                "duration": duration,
                                "source_tag": self.source_tag,
                                "port": self.port,
                                "monitor_id": self.monitor_id,
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
                    "source_tag": self.source_tag,
                    "port": self.port,
                    "monitor_id": self.monitor_id,
                }
                self.messages.put(("outage", event))
                self.stop_event.wait(self.retry_interval)


class MonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PZEM-004T Monitor | INTECSA")
        self.geometry("1050x620")
        self.minsize(800, 500)
        self.configure(background=COLORS["background"])
        self._configure_theme()
        configure_logging()
        self.store = ReadingStore(DB_PATH)
        self.messages = queue.Queue()
        self.monitors = {}
        self.next_monitor_id = 1
        self.selected_monitor_id = None
        self.values = {}
        self._build_ui()
        self.draw_graph()
        self.after(100, self.process_messages)
        self.protocol("WM_DELETE_WINDOW", self.close)
        LOGGER.info("Aplicacion iniciada")

    def _configure_theme(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=COLORS["background"],
                        foreground=COLORS["foreground"], fieldbackground=COLORS["field"])
        style.configure("TFrame", background=COLORS["background"])
        style.configure("TLabelframe", background=COLORS["panel"],
                        bordercolor="#374151", relief="solid")
        style.configure("TLabelframe.Label", background=COLORS["panel"],
                        foreground=COLORS["foreground"], font=("Segoe UI", 9, "bold"))
        style.configure("TLabel", background=COLORS["background"],
                        foreground=COLORS["foreground"])
        style.configure("TButton", background="#374151", foreground=COLORS["foreground"],
                        bordercolor="#4b5563", padding=6)
        style.map("TButton", background=[("active", "#4b5563"), ("pressed", "#334155")])
        style.configure("TEntry", fieldbackground=COLORS["field"],
                        foreground=COLORS["foreground"], insertcolor=COLORS["foreground"])
        style.configure("TCombobox", fieldbackground=COLORS["field"],
                        foreground=COLORS["foreground"], arrowcolor=COLORS["foreground"])
        style.map("TCombobox", fieldbackground=[("readonly", COLORS["field"])],
                  foreground=[("readonly", COLORS["foreground"])])
        style.configure("Treeview", background=COLORS["field"],
                        fieldbackground=COLORS["field"], foreground=COLORS["foreground"],
                        rowheight=27, bordercolor="#374151")
        style.configure("Treeview.Heading", background="#374151",
                        foreground=COLORS["foreground"], relief="flat")
        style.map("Treeview", background=[("selected", COLORS["selection"])],
                  foreground=[("selected", "#ffffff")])
        style.map("Treeview.Heading", background=[("active", "#4b5563")])

    def _build_ui(self):
        controls = ttk.LabelFrame(self, text="Puertos monitoreados", padding=10)
        controls.pack(fill="x", padx=12, pady=(10, 0))
        self.monitor_list = ttk.Treeview(
            controls, columns=("tag", "port", "status", "last"), show="headings", height=4
        )
        for column, title, width in (("tag", "Etiqueta", 180), ("port", "Puerto", 100),
                                     ("status", "Estado", 210),
                                     ("last", "Ultima lectura", 180)):
            self.monitor_list.heading(column, text=title)
            self.monitor_list.column(column, width=width, anchor="center")
        self.monitor_list.pack(side="left", fill="x", expand=True)
        self.monitor_list.bind("<<TreeviewSelect>>", self.select_monitor)
        actions = ttk.Frame(controls)
        actions.pack(side="right", fill="y", padx=(10, 0))
        ttk.Button(actions, text="+ AGREGAR PUERTO", command=self.show_add_port).pack(fill="x")
        ttk.Button(actions, text="DETENER SELECCIONADO",
                   command=self.stop_selected).pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="EXPORTAR CSV",
                   command=self.export_csv).pack(fill="x", pady=(8, 0))

        self.alert = tk.StringVar(value="Agrega un puerto para comenzar")
        self.alert_label = tk.Label(
            self, textvariable=self.alert, fg=COLORS["muted"], bg=COLORS["background"],
            font=("Segoe UI", 10, "bold")
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
        self.canvas = tk.Canvas(graph_box, background=COLORS["field"], height=170,
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.draw_graph())

        footer = ttk.Frame(self, padding=10)
        footer.pack(fill="x")
        self.status = tk.StringVar(value=f"Datos locales: {DB_PATH} | CSV: {CSV_DIR}")
        ttk.Label(footer, textvariable=self.status).pack(side="left")
        ttk.Label(footer, text="Made by INTECSA", font=("Segoe UI", 9, "bold")).pack(
            side="right", padx=16
        )

    def show_add_port(self):
        dialog = tk.Toplevel(self)
        dialog.title("Agregar puerto")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack()
        fields = {}
        ports = [item.device for item in list_ports.comports()]
        specs = (("tag", "Etiqueta:", "Entrada principal"),
                 ("port", "Puerto COM:", ports[0] if ports else ""),
                 ("address", "Direccion Modbus:", "1"),
                 ("interval", "Medicion (s):", "3"),
                 ("retry", "Reconexion (s):", "5"))
        for row, (key, label, default) in enumerate(specs):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="e", padx=5, pady=5)
            if key == "port":
                widget = ttk.Combobox(frame, values=ports, width=24)
            else:
                widget = ttk.Entry(frame, width=27)
            widget.insert(0, default)
            widget.grid(row=row, column=1, padx=5, pady=5)
            fields[key] = widget

        def submit():
            if self.start_monitor(fields["tag"].get(), fields["port"].get(),
                                  fields["address"].get(), fields["interval"].get(),
                                  fields["retry"].get()):
                dialog.destroy()

        ttk.Button(frame, text="INICIAR ESCUCHA", command=submit).grid(
            row=len(specs), column=0, columnspan=2, pady=(12, 0)
        )
        fields["tag"].focus_set()

    def start_monitor(self, source_tag, port, address, interval, retry_interval):
        source_tag, port = source_tag.strip(), port.strip()
        try:
            address, interval, retry_interval = int(address), float(interval), float(retry_interval)
            if not source_tag or not port or not 1 <= address <= 247:
                raise ValueError
            if interval < 1 or retry_interval < 1:
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "Configuracion", "Indica etiqueta, puerto, direccion (1-247) e intervalos validos."
            )
            return False
        if any(item["port"].casefold() == port.casefold() for item in self.monitors.values()):
            messagebox.showwarning("Puerto en uso", f"{port} ya se esta monitoreando.")
            return False
        if any(item["tag"].casefold() == source_tag.casefold()
               for item in self.monitors.values()):
            messagebox.showwarning("Etiqueta repetida", "Usa una etiqueta distinta para cada puerto.")
            return False
        monitor_id = str(self.next_monitor_id)
        self.next_monitor_id += 1
        stop_event = threading.Event()
        worker = PzemWorker(
            monitor_id, source_tag, port, address, interval, retry_interval,
            self.messages, stop_event
        )
        self.monitors[monitor_id] = {"tag": source_tag, "port": port, "worker": worker,
                                     "stop_event": stop_event, "retry": retry_interval,
                                     "outage": False}
        self.monitor_list.insert("", "end", iid=monitor_id,
                                 values=(source_tag, port, "Conectando", "--"))
        self.monitor_list.selection_set(monitor_id)
        self.selected_monitor_id = monitor_id
        worker.start()
        self.alert.set(f"{source_tag} ({port}): monitoreando")
        self.alert_label.configure(fg=COLORS["accent"])
        LOGGER.info(
            "Monitoreo iniciado | %s | %s | medicion=%ss | reconexion=%ss",
            source_tag, port, interval, retry_interval
        )
        return True

    def stop_selected(self):
        monitor_id = self.selected_monitor_id
        if not monitor_id or monitor_id not in self.monitors:
            messagebox.showinfo("Puertos", "Selecciona un puerto activo.")
            return
        monitor = self.monitors[monitor_id]
        if not messagebox.askyesno(
            "Detener monitoreo",
            f"Se dejara de registrar {monitor['tag']} ({monitor['port']}).\n\n"
            "Las lecturas, eventos y graficas ya guardados NO se perderan.\n\n"
            "¿Deseas continuar?",
        ):
            return
        monitor["stop_event"].set()
        self.monitor_list.item(monitor_id, values=(monitor["tag"], monitor["port"],
                                                    "Detenido", "--"))
        self.alert.set(f"{monitor['tag']}: monitoreo detenido")
        self.alert_label.configure(fg=COLORS["muted"])
        LOGGER.info("Monitoreo detenido | %s | %s", monitor["tag"], monitor["port"])

    def select_monitor(self, _event=None):
        selected = self.monitor_list.selection()
        if selected:
            self.selected_monitor_id = selected[0]
            self.draw_graph()

    def process_messages(self):
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                monitor_id = payload["monitor_id"]
                monitor = self.monitors.get(monitor_id)
                if monitor is None:
                    continue
                if kind == "reading":
                    self.store.add(payload)
                    LOGGER.info(
                        "LECTURA | %s | %s | V=%.2f | A=%.3f | W=%.2f | kWh=%.4f | Hz=%.2f | FP=%.2f",
                        payload["source_tag"], payload["port"], payload["voltage"], payload["current"], payload["power"],
                        payload["energy"], payload["frequency"], payload["power_factor"]
                    )
                    monitor["outage"] = False
                    self.monitor_list.item(monitor_id, values=(monitor["tag"], monitor["port"],
                                                               "Monitoreando", payload["timestamp"]))
                    if monitor_id == self.selected_monitor_id:
                        for key, (variable, unit) in self.values.items():
                            decimals = 4 if key == "energy" else 3 if key == "current" else 2
                            variable.set(f"{payload[key]:.{decimals}f} {unit}")
                        self.status.set(f"{monitor['tag']} | Ultima lectura: {payload['timestamp']}")
                        self.draw_graph()
                elif kind == "outage":
                    self.store.add_event(payload)
                    LOGGER.warning("SIN_RESPUESTA | %s | %s | %s", payload["source_tag"],
                                   payload["port"], payload["detail"])
                    self.monitor_list.item(monitor_id, values=(monitor["tag"], monitor["port"],
                                                               "SIN RESPUESTA", payload["timestamp"]))
                    if monitor_id == self.selected_monitor_id:
                        for variable, unit in self.values.values():
                            variable.set(f"SIN RESPUESTA {unit}")
                        self.status.set(f"Sin respuesta; nuevo intento en {monitor['retry']:g} s")
                    if not monitor["outage"]:
                        monitor["outage"] = True
                        self.bell()
                    self.alert.set(f"⚠ {monitor['tag']} SIN RESPUESTA — reintentando")
                    self.alert_label.configure(fg="#f87171")
                elif kind == "recovered":
                    self.store.add_event(payload)
                    monitor["outage"] = False
                    LOGGER.info("RECUPERADO | %s | %s | %s", payload["source_tag"],
                                payload["port"], payload["detail"])
                    self.alert.set(f"{monitor['tag']}: conexion recuperada — monitoreando")
                    self.alert_label.configure(fg=COLORS["accent"])
                    if monitor_id == self.selected_monitor_id:
                        self.status.set(f"Conexion recuperada; corte de {payload['duration']} segundos")
        except queue.Empty:
            pass
        self.after(100, self.process_messages)

    def draw_graph(self):
        self.canvas.delete("all")
        tag = None
        if self.selected_monitor_id in self.monitors:
            tag = self.monitors[self.selected_monitor_id]["tag"]
        rows = self.store.recent(tag)
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        margin = 35
        if len(rows) < 2 or width <= 2 * margin:
            self.canvas.create_text(width / 2, height / 2, text="Esperando lecturas...",
                                    fill=COLORS["muted"])
            return
        powers = [row[1] for row in rows]
        maximum = max(max(powers), 1)
        points = []
        for index, power in enumerate(powers):
            x = margin + index * (width - 2 * margin) / (len(powers) - 1)
            y = height - margin - power * (height - 2 * margin) / maximum
            points.extend((x, y))
        self.canvas.create_line(margin, margin, margin, height - margin, fill=COLORS["grid"])
        self.canvas.create_line(margin, height - margin, width - margin, height - margin,
                                fill=COLORS["grid"])
        self.canvas.create_line(*points, fill=COLORS["graph"], width=2)
        self.canvas.create_text(margin, margin - 8, text=f"{maximum:.1f} W", anchor="w",
                                fill=COLORS["foreground"])

    def export_csv(self):
        name = f"lecturas_{datetime.now():%Y%m%d_%H%M%S}.csv"
        destination = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=name,
                                                   filetypes=(("CSV", "*.csv"),))
        if destination:
            self.store.export_csv(destination)
            messagebox.showinfo("Exportacion", "Archivo CSV creado correctamente.")

    def close(self):
        active = [item for item in self.monitors.values() if item["worker"].is_alive()]
        if active and not messagebox.askyesno(
            "Cerrar aplicacion",
            f"Hay {len(active)} puerto(s) activo(s). Al cerrar se dejara de registrar.\n\n"
            "Todo el historial ya guardado se conservara. ¿Deseas cerrar?",
        ):
            return
        for monitor in self.monitors.values():
            monitor["stop_event"].set()
        for monitor in self.monitors.values():
            if monitor["worker"].is_alive():
                monitor["worker"].join(timeout=1)
        LOGGER.info("Aplicacion cerrada")
        self.store.connection.close()
        self.destroy()


if __name__ == "__main__":
    MonitorApp().mainloop()
