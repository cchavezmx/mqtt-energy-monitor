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
    "background": "#0b1121",
    "panel": "#151e32",
    "card": "#1e293b",
    "field": "#0f172a",
    "foreground": "#e2e8f0",
    "muted": "#94a3b8",
    "accent": "#22c55e",
    "accent_active": "#16a34a",
    "warning": "#f87171",
    "selection": "#2563eb",
    "grid": "#475569",
    "graph": "#38bdf8",
    "voltage": "#f59e0b",
    "current": "#ef4444",
    "power": "#22c55e",
    "energy": "#3b82f6",
    "frequency": "#a855f7",
    "power_factor": "#ec4899",
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
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS readings (
            timestamp TEXT NOT NULL, voltage REAL, current REAL, power REAL,
            energy REAL, frequency REAL, power_factor REAL,
            status TEXT NOT NULL DEFAULT 'OK', detail TEXT,
            source_tag TEXT NOT NULL DEFAULT 'Principal', port TEXT, session_id INTEGER)"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_tag TEXT NOT NULL,
            port TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT)"""
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
        if "session_id" not in columns:
            self.connection.execute("ALTER TABLE readings ADD COLUMN session_id INTEGER")
        legacy_groups = self.connection.execute(
            """SELECT source_tag, port, MIN(timestamp), MAX(timestamp) FROM readings
            WHERE session_id IS NULL GROUP BY source_tag, port"""
        ).fetchall()
        for source_tag, port, started_at, ended_at in legacy_groups:
            cursor = self.connection.execute(
                """INSERT INTO sessions (source_tag, port, started_at, ended_at)
                VALUES (?, ?, ?, ?)""",
                (source_tag or "Principal", port or "Desconocido", started_at, ended_at),
            )
            self.connection.execute(
                """UPDATE readings SET session_id = ?
                WHERE session_id IS NULL AND source_tag = ? AND port IS ?""",
                (cursor.lastrowid, source_tag, port),
            )
        self.connection.commit()

    def _csv_path(self, source_tag: str, session_id=None) -> Path:
        safe_tag = re.sub(r"[^A-Za-z0-9._-]+", "_", source_tag).strip("._") or "puerto"
        suffix = f"_sesion_{session_id}" if session_id else ""
        return self.csv_dir / f"lecturas_{safe_tag}{suffix}.csv"

    def _append_csv(self, row: tuple, source_tag: str, session_id=None):
        path = self._csv_path(source_tag, session_id)
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
             status, source_tag, port, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'OK', ?, ?, ?)""",
            (reading["timestamp"], reading["voltage"], reading["current"],
             reading["power"], reading["energy"], reading["frequency"],
             reading["power_factor"], reading.get("source_tag", "Principal"),
             reading.get("port"), reading.get("session_id")),
        )
        self.connection.commit()
        self._append_csv((reading["timestamp"], reading.get("source_tag", "Principal"),
                          reading.get("port"), reading["voltage"], reading["current"],
                          reading["power"], reading["energy"], reading["frequency"],
                          reading["power_factor"], "OK", ""),
                         reading.get("source_tag", "Principal"), reading.get("session_id"))

    def add_event(self, event: dict):
        self.connection.execute(
            """INSERT INTO readings (timestamp, status, detail, source_tag, port, session_id)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (event["timestamp"], event["status"], event["detail"],
             event.get("source_tag", "Principal"), event.get("port"), event.get("session_id")),
        )
        self.connection.commit()
        self._append_csv((event["timestamp"], event.get("source_tag", "Principal"),
                          event.get("port"), "", "", "", "", "", "",
                          event["status"], event["detail"]),
                         event.get("source_tag", "Principal"), event.get("session_id"))

    def create_session(self, source_tag, port):
        started_at = datetime.now().isoformat(sep=" ", timespec="seconds")
        cursor = self.connection.execute(
            "INSERT INTO sessions (source_tag, port, started_at) VALUES (?, ?, ?)",
            (source_tag, port, started_at),
        )
        self.connection.commit()
        return cursor.lastrowid

    def latest_session(self, source_tag, port):
        return self.connection.execute(
            """SELECT id, started_at, ended_at FROM sessions
            WHERE lower(source_tag) = lower(?) AND lower(port) = lower(?)
            ORDER BY id DESC LIMIT 1""", (source_tag, port)
        ).fetchone()

    def close_session(self, session_id):
        self.connection.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ?",
            (datetime.now().isoformat(sep=" ", timespec="seconds"), session_id),
        )
        self.connection.commit()

    def resume_session(self, session_id):
        self.connection.execute("UPDATE sessions SET ended_at = NULL WHERE id = ?", (session_id,))
        self.connection.commit()

    def session_history(self):
        return self.connection.execute(
            """SELECT s.id, s.source_tag, s.port, s.started_at, s.ended_at,
            COUNT(r.rowid) FROM sessions s LEFT JOIN readings r ON r.session_id = s.id
            GROUP BY s.id ORDER BY s.id DESC"""
        ).fetchall()

    def export_session_csv(self, session_id, destination):
        rows = self.connection.execute(
            """SELECT timestamp, source_tag, port, voltage, current, power, energy,
            frequency, power_factor, status, detail FROM readings
            WHERE session_id = ? ORDER BY rowid""", (session_id,)
        ).fetchall()
        self._write_export(destination, rows)

    def delete_session(self, session_id):
        row = self.connection.execute(
            "SELECT source_tag FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        self.connection.execute("DELETE FROM readings WHERE session_id = ?", (session_id,))
        self.connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        self.connection.commit()
        if row:
            path = self._csv_path(row[0], session_id)
            if path.exists():
                path.unlink()

    def recent(self, source_tag=None, limit=120, session_id=None):
        if session_id is not None:
            condition, parameters = "AND session_id = ?", (session_id, limit)
        elif source_tag is None:
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
        self._write_export(destination, rows)

    @staticmethod
    def _write_export(destination, rows):
        with open(destination, "w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("fecha", "etiqueta", "puerto", "voltaje_V", "corriente_A",
                             "potencia_W", "energia_kWh", "frecuencia_Hz",
                             "factor_potencia", "estado", "detalle"))
            writer.writerows(rows)


class PzemWorker(threading.Thread):
    def __init__(self, monitor_id, session_id, source_tag, port, address, interval, retry_interval,
                 messages, stop_event):
        super().__init__(daemon=True)
        self.monitor_id = monitor_id
        self.session_id = session_id
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
                                       monitor_id=self.monitor_id, session_id=self.session_id)
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
                                "session_id": self.session_id,
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
                    "session_id": self.session_id,
                }
                self.messages.put(("outage", event))
                self.stop_event.wait(self.retry_interval)


class MetricCard(tk.Frame):
    def __init__(self, parent, title, unit, color_key, **kwargs):
        super().__init__(parent, bg=COLORS["card"], highlightbackground=COLORS["panel"],
                         highlightthickness=1, **kwargs)
        self.unit = unit
        self.color = COLORS.get(color_key, COLORS["accent"])

        self.header = tk.Label(self, text=title.upper(), bg=COLORS["card"],
                             fg=COLORS["muted"], font=("Segoe UI", 9, "bold"))
        self.header.pack(anchor="w", padx=12, pady=(10, 0))

        self.value_label = tk.Label(self, text=f"-- {unit}", bg=COLORS["card"],
                                    fg=self.color, font=("Segoe UI", 28, "bold"))
        self.value_label.pack(anchor="w", padx=12, pady=(4, 0))

        self.spark = tk.Canvas(self, bg=COLORS["card"], height=50,
                               highlightthickness=0)
        self.spark.pack(fill="x", padx=10, pady=(8, 10))
        self.spark_data = []

    def update_value(self, value, decimals=2):
        self.spark_data.append(value)
        if len(self.spark_data) > 30:
            self.spark_data.pop(0)
        self.value_label.config(text=f"{value:.{decimals}f} {self.unit}")
        self._draw_spark()

    def set_na(self):
        self.value_label.config(text=f"-- {self.unit}")

    def _draw_spark(self):
        self.spark.delete("all")
        width = self.spark.winfo_width()
        height = self.spark.winfo_height()
        if len(self.spark_data) < 2 or width < 20 or height < 10:
            return
        data = self.spark_data[:]
        maximum = max(max(data), 1)
        points = []
        for index, value in enumerate(data):
            x = 2 + index * (width - 4) / (len(data) - 1)
            y = height - 4 - value * (height - 8) / maximum
            points.extend((x, y))
        self.spark.create_line(*points, fill=self.color, width=2, smooth=True)


class GraphCard(tk.Frame):
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg=COLORS["card"], highlightbackground=COLORS["panel"],
                         highlightthickness=1, **kwargs)
        header = tk.Label(self, text="POTENCIA RECIENTE", bg=COLORS["card"],
                          fg=COLORS["muted"], font=("Segoe UI", 9, "bold"))
        header.pack(anchor="w", padx=12, pady=(10, 0))
        self.canvas = tk.Canvas(self, bg=COLORS["field"], height=160,
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=10, pady=(8, 10))
        self.canvas.bind("<Configure>", lambda _event: self.draw_graph())
        self.rows = []

    def set_data(self, rows):
        self.rows = rows
        self.draw_graph()

    def draw_graph(self):
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        margin = 35
        if len(self.rows) < 2 or width <= 2 * margin or height <= 2 * margin:
            self.canvas.create_text(width / 2, height / 2, text="Esperando lecturas...",
                                    fill=COLORS["muted"])
            return
        powers = [row[1] for row in self.rows]
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


class ConnectionWidget(tk.Frame):
    FIELDS = (
        ("voltage", "Voltaje", "V", 2),
        ("current", "Corriente", "A", 3),
        ("power", "Potencia", "W", 2),
        ("energy", "Energia", "kWh", 4),
        ("frequency", "Frecuencia", "Hz", 2),
        ("power_factor", "Factor potencia", "", 2),
    )

    def __init__(self, parent, tag, port, **kwargs):
        super().__init__(parent, bg=COLORS["card"], highlightbackground=COLORS["panel"],
                         highlightthickness=1, **kwargs)
        header = tk.Frame(self, bg=COLORS["card"])
        header.pack(fill="x", padx=12, pady=(9, 5))
        tk.Label(header, text=tag, bg=COLORS["card"], fg=COLORS["foreground"],
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Label(header, text=port, bg=COLORS["card"], fg=COLORS["muted"],
                 font=("Segoe UI", 9)).pack(side="left", padx=(8, 0))
        self.state_label = tk.Label(header, text="CONECTANDO", bg=COLORS["card"],
                                    fg=COLORS["muted"], font=("Segoe UI", 8, "bold"))
        self.state_label.pack(side="right")

        metrics = tk.Frame(self, bg=COLORS["card"])
        metrics.pack(fill="both", expand=True, padx=8, pady=(0, 9))
        for column in range(3):
            metrics.columnconfigure(column, weight=1)
        self.value_labels = {}
        for index, (key, title, unit, _decimals) in enumerate(self.FIELDS):
            tile = tk.Frame(metrics, bg=COLORS["field"])
            tile.grid(row=index // 3, column=index % 3, sticky="nsew", padx=3, pady=3)
            tk.Label(tile, text=title.upper(), bg=COLORS["field"], fg=COLORS["muted"],
                     font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=8, pady=(6, 0))
            label = tk.Label(tile, text=f"-- {unit}", bg=COLORS["field"],
                             fg=COLORS.get(key, COLORS["foreground"]),
                             font=("Segoe UI", 14, "bold"))
            label.pack(anchor="w", padx=8, pady=(1, 6))
            self.value_labels[key] = label

    def show_reading(self, reading):
        for key, _title, unit, decimals in self.FIELDS:
            self.value_labels[key].configure(text=f"{reading[key]:.{decimals}f} {unit}")
        self.state_label.configure(text="MONITOREANDO", fg=COLORS["accent"])

    def show_state(self, state):
        color = COLORS["warning"] if state == "SIN RESPUESTA" else COLORS["muted"]
        self.state_label.configure(text=state.upper(), fg=color)
        if state in ("SIN RESPUESTA", "Conectando"):
            for key, _title, unit, _decimals in self.FIELDS:
                text = f"SIN RESP. {unit}" if state == "SIN RESPUESTA" else f"-- {unit}"
                self.value_labels[key].configure(text=text)


class PortList(tk.Frame):
    def __init__(self, parent, on_select, **kwargs):
        super().__init__(parent, bg=COLORS["panel"], **kwargs)
        self.selected_id = None
        self._id_by_index = {}

        header = tk.Label(self, text="PUERTOS", bg=COLORS["panel"],
                          fg=COLORS["muted"], font=("Segoe UI", 10, "bold"))
        header.pack(anchor="w", padx=10, pady=(10, 6))

        self.listbox = tk.Listbox(self, bg=COLORS["field"], fg=COLORS["foreground"],
                                  selectbackground=COLORS["selection"],
                                  selectforeground="#ffffff", bd=0, font=("Segoe UI", 10),
                                  highlightthickness=0, activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.listbox.bind("<<ListboxSelect>>", on_select)

    def update_ports(self, monitors, selected_id):
        items = []
        for monitor_id, monitor in monitors.items():
            status = monitor.get("state", "Conectando")
            text = f"{monitor['tag']} [{monitor['port']}] - {status}"
            items.append((monitor_id, text))
        items.sort(key=lambda item: item[1])
        current_texts = tuple(self.listbox.get(idx) for idx in range(self.listbox.size()))
        wanted_texts = tuple(text for _, text in items)
        if current_texts == wanted_texts and str(selected_id) == str(self.selected_id):
            return
        self.listbox.delete(0, "end")
        self._id_by_index.clear()
        for index, (monitor_id, text) in enumerate(items):
            self.listbox.insert("end", text)
            self._id_by_index[index] = monitor_id
        self.selected_id = selected_id
        for index, monitor_id in self._id_by_index.items():
            if str(monitor_id) == str(selected_id):
                self.listbox.selection_set(index)
                break

    def selected_monitor_id(self):
        selection = self.listbox.curselection()
        if not selection:
            return self.selected_id
        return self._id_by_index.get(selection[0])

    def monitor_id_at_text(self, text):
        for index, monitor_id in self._id_by_index.items():
            if self.listbox.get(index) == text:
                return monitor_id
        return None


class MonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PZEM-004T Monitor | INTECSA")
        self.geometry("1200x720")
        self.minsize(900, 600)
        self.configure(background=COLORS["background"])
        self._configure_theme()
        configure_logging()
        self.store = ReadingStore(DB_PATH)
        self.messages = queue.Queue()
        self.monitors = {}
        self.next_monitor_id = 1
        self.selected_monitor_id = None
        self.connection_widgets = {}
        self._build_ui()
        self.after(100, self.process_messages)
        self.protocol("WM_DELETE_WINDOW", self.close)
        LOGGER.info("Aplicacion iniciada")

    def _configure_theme(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=COLORS["background"],
                        foreground=COLORS["foreground"], fieldbackground=COLORS["field"])
        style.configure("TFrame", background=COLORS["background"])
        style.configure("TButton", background="#374151", foreground=COLORS["foreground"],
                        bordercolor="#4b5563", padding=6)
        style.map("TButton", background=[("active", "#4b5563"), ("pressed", "#334155")])

    def _build_ui(self):
        self.top_bar = tk.Frame(self, bg=COLORS["panel"], height=46)
        self.top_bar.pack(fill="x")
        self.top_bar.pack_propagate(False)
        self.alert = tk.StringVar(value="Agrega un puerto para comenzar")
        self.alert_label = tk.Label(self.top_bar, textvariable=self.alert, bg=COLORS["panel"],
                                    fg=COLORS["accent"], font=("Segoe UI", 11, "bold"))
        self.alert_label.pack(side="left", padx=16)
        tk.Label(self.top_bar, text="Made by INTECSA", bg=COLORS["panel"],
                 fg=COLORS["muted"], font=("Segoe UI", 9, "bold")).pack(side="right", padx=16)

        self.sidebar = tk.Frame(self, bg=COLORS["panel"], width=260)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self.port_list = PortList(self.sidebar, self.select_monitor)
        self.port_list.pack(fill="both", expand=True)

        button_box = tk.Frame(self.sidebar, bg=COLORS["panel"])
        button_box.pack(fill="x", padx=10, pady=10)
        tk.Button(button_box, text="+ AGREGAR PUERTO", bg="#374151", fg=COLORS["foreground"],
                  activebackground="#4b5563", bd=0, font=("Segoe UI", 9, "bold"),
                  command=self.show_add_port).pack(fill="x", pady=(0, 6), ipady=6)
        tk.Button(button_box, text="DETENER SELECCIONADO", bg="#374151", fg=COLORS["foreground"],
                  activebackground="#4b5563", bd=0, font=("Segoe UI", 9, "bold"),
                  command=self.stop_selected).pack(fill="x", pady=(0, 6), ipady=6)
        tk.Button(button_box, text="HISTORIAL", bg="#374151", fg=COLORS["foreground"],
                  activebackground="#4b5563", bd=0, font=("Segoe UI", 9, "bold"),
                  command=self.show_history).pack(fill="x", pady=(0, 6), ipady=6)
        self.main_area = tk.Frame(self, bg=COLORS["background"])
        self.main_area.pack(side="left", fill="both", expand=True, padx=16, pady=16)

        tk.Label(self.main_area, text="METRICAS POR CONEXION", bg=COLORS["background"],
                 fg=COLORS["muted"], font=("Segoe UI", 9, "bold")).pack(anchor="w")
        metrics_box = tk.Frame(self.main_area, bg=COLORS["background"])
        metrics_box.pack(fill="x", pady=(6, 0))
        self.metrics_canvas = tk.Canvas(metrics_box, bg=COLORS["background"], height=250,
                                        highlightthickness=0)
        metrics_scroll = ttk.Scrollbar(metrics_box, orient="vertical",
                                       command=self.metrics_canvas.yview)
        self.metrics_canvas.configure(yscrollcommand=metrics_scroll.set)
        metrics_scroll.pack(side="right", fill="y")
        self.metrics_canvas.pack(side="left", fill="x", expand=True)
        self.metrics_grid = tk.Frame(self.metrics_canvas, bg=COLORS["background"])
        self.metrics_window = self.metrics_canvas.create_window(
            (0, 0), window=self.metrics_grid, anchor="nw"
        )
        self.metrics_grid.bind(
            "<Configure>",
            lambda _event: self.metrics_canvas.configure(
                scrollregion=self.metrics_canvas.bbox("all")
            ),
        )
        self.metrics_canvas.bind(
            "<Configure>",
            lambda event: self.metrics_canvas.itemconfigure(self.metrics_window, width=event.width),
        )
        self.metrics_grid.columnconfigure((0, 1), weight=1)

        self.graph_card = GraphCard(self.main_area)
        self.graph_card.pack(fill="both", expand=True, pady=(10, 0))

        self.status = tk.StringVar(value=f"Datos locales: {DB_PATH} | CSV: {CSV_DIR}")
        tk.Label(self.main_area, textvariable=self.status, bg=COLORS["background"],
                 fg=COLORS["muted"], font=("Segoe UI", 9)).pack(anchor="w", pady=(10, 0))

    def show_add_port(self):
        dialog = tk.Toplevel(self)
        dialog.title("Agregar puerto")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg=COLORS["background"])
        frame = tk.Frame(dialog, bg=COLORS["background"], padx=16, pady=16)
        frame.pack()
        fields = {}
        ports = [item.device for item in list_ports.comports()]
        specs = (("tag", "Etiqueta:", "Entrada principal"),
                 ("port", "Puerto COM:", ports[0] if ports else ""),
                 ("address", "Direccion Modbus:", "1"),
                 ("interval", "Medicion (s):", "3"),
                 ("retry", "Reconexion (s):", "5"))
        for row, (key, label, default) in enumerate(specs):
            tk.Label(frame, text=label, bg=COLORS["background"],
                     fg=COLORS["foreground"], font=("Segoe UI", 9)).grid(
                         row=row, column=0, sticky="e", padx=5, pady=5)
            if key == "port":
                widget = ttk.Combobox(frame, values=ports, width=24)
            else:
                widget = tk.Entry(frame, width=27, bg=COLORS["field"], fg=COLORS["foreground"],
                                  bd=0, insertbackground=COLORS["foreground"])
            widget.insert(0, default)
            widget.grid(row=row, column=1, padx=5, pady=5)
            fields[key] = widget

        def submit():
            if self.start_monitor(fields["tag"].get(), fields["port"].get(),
                                  fields["address"].get(), fields["interval"].get(),
                                  fields["retry"].get()):
                dialog.destroy()

        tk.Button(frame, text="INICIAR ESCUCHA", bg=COLORS["accent_active"],
                  fg="#ffffff", activebackground=COLORS["accent"], bd=0,
                  font=("Segoe UI", 9, "bold"), command=submit).grid(
                      row=len(specs), column=0, columnspan=2, pady=(16, 0), ipady=6, sticky="ew")
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
        if any(item["worker"].is_alive() and item["port"].casefold() == port.casefold()
               for item in self.monitors.values()):
            messagebox.showwarning("Puerto en uso", f"{port} ya se esta monitoreando.")
            return False
        if any(item["worker"].is_alive() and item["tag"].casefold() == source_tag.casefold()
               for item in self.monitors.values()):
            messagebox.showwarning("Etiqueta repetida", "Usa una etiqueta distinta para cada puerto.")
            return False
        previous = self.store.latest_session(source_tag, port)
        if previous:
            resume = messagebox.askyesnocancel(
                "Historial encontrado",
                f"Hay datos anteriores de {source_tag} ({port}), iniciados el {previous[1]}.\n\n"
                "Si: retomar esa sesion.\nNo: comenzar una sesion nueva.\n"
                "Cancelar: no iniciar la escucha.",
            )
            if resume is None:
                return False
            if resume:
                session_id = previous[0]
                self.store.resume_session(session_id)
            else:
                session_id = self.store.create_session(source_tag, port)
        else:
            session_id = self.store.create_session(source_tag, port)
        monitor_id = str(self.next_monitor_id)
        self.next_monitor_id += 1
        stop_event = threading.Event()
        worker = PzemWorker(
            monitor_id, session_id, source_tag, port, address, interval, retry_interval,
            self.messages, stop_event
        )
        self.monitors[monitor_id] = {"tag": source_tag, "port": port, "worker": worker,
                                     "stop_event": stop_event, "retry": retry_interval,
                                     "outage": False, "session_id": session_id,
                                     "last_reading": None, "state": "Conectando"}
        widget_index = len(self.connection_widgets)
        connection_widget = ConnectionWidget(self.metrics_grid, source_tag, port)
        connection_widget.grid(row=widget_index // 2, column=widget_index % 2,
                               sticky="nsew", padx=5, pady=5)
        self.connection_widgets[monitor_id] = connection_widget
        if self.selected_monitor_id is None:
            self.selected_monitor_id = monitor_id
        self.port_list.update_ports(self.monitors, self.selected_monitor_id)
        self.show_selected_values()
        worker.start()
        self.alert.set(f"{source_tag} ({port}): monitoreando")
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
        self.store.close_session(monitor["session_id"])
        monitor["state"] = "Detenido"
        self.connection_widgets[monitor_id].show_state("Detenido")
        self.port_list.update_ports(self.monitors, self.selected_monitor_id)
        self.alert.set(f"{monitor['tag']}: monitoreo detenido")
        self.alert_label.configure(fg=COLORS["muted"])
        self.show_selected_values()
        LOGGER.info("Monitoreo detenido | %s | %s", monitor["tag"], monitor["port"])

    def select_monitor(self, _event=None):
        monitor_id = self.port_list.selected_monitor_id()
        if monitor_id is None:
            return
        self.selected_monitor_id = monitor_id
        self.port_list.selected_id = monitor_id
        self.show_selected_values()

    def show_selected_values(self):
        monitor = self.monitors.get(self.selected_monitor_id)
        if monitor is None:
            self.graph_card.set_data([])
            return
        reading = monitor.get("last_reading")
        if reading is not None and monitor["state"] == "Monitoreando":
            tag = monitor["tag"]
            self.status.set(f"{tag} | Ultima lectura: {reading['timestamp']}")
            rows = self.store.recent(tag, session_id=monitor["session_id"])
            self.graph_card.set_data(rows)
        elif monitor["state"] == "SIN RESPUESTA":
            self.status.set(f"{monitor['tag']} | Sin respuesta; reintentando")
            self.graph_card.set_data([])
        else:
            self.status.set(f"{monitor['tag']} ({monitor['port']}) | {monitor['state']}")
            self.graph_card.set_data([])

    def show_history(self):
        dialog = tk.Toplevel(self)
        dialog.title("Historial de sesiones")
        dialog.geometry("850x420")
        dialog.configure(background=COLORS["background"])
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        history = ttk.Treeview(
            frame, columns=("tag", "port", "start", "end", "records"),
            show="headings", height=12
        )
        for column, title, width in (("tag", "Etiqueta", 150), ("port", "Puerto", 80),
                                     ("start", "Inicio", 150), ("end", "Fin", 150),
                                     ("records", "Registros", 80)):
            history.heading(column, text=title)
            history.column(column, width=width, anchor="center")
        history.pack(fill="both", expand=True)

        def reload_history():
            history.delete(*history.get_children())
            active_sessions = {item["session_id"] for item in self.monitors.values()
                               if item["worker"].is_alive()}
            for session_id, tag, port, started, ended, records in self.store.session_history():
                end_text = "ACTIVA" if session_id in active_sessions else (ended or "Sin cierre")
                history.insert("", "end", iid=str(session_id),
                               values=(tag, port, started, end_text, records))

        def selected_session():
            selected = history.selection()
            if not selected:
                messagebox.showinfo("Historial", "Selecciona una sesion.", parent=dialog)
                return None
            return int(selected[0])

        def export_selected():
            session_id = selected_session()
            if session_id is None:
                return
            destination = filedialog.asksaveasfilename(
                parent=dialog, defaultextension=".csv",
                initialfile=f"sesion_{session_id}.csv", filetypes=(("CSV", "*.csv"),)
            )
            if destination:
                self.store.export_session_csv(session_id, destination)
                messagebox.showinfo("Historial", "Sesion exportada correctamente.", parent=dialog)

        def delete_selected():
            session_id = selected_session()
            if session_id is None:
                return
            if any(item["session_id"] == session_id and item["worker"].is_alive()
                   for item in self.monitors.values()):
                messagebox.showwarning("Historial", "No se puede eliminar una sesion activa.",
                                       parent=dialog)
                return
            if messagebox.askyesno(
                "Eliminar sesion",
                "Se eliminaran permanentemente sus registros de SQLite y su CSV automatico.\n\n"
                "¿Deseas continuar?", parent=dialog
            ):
                self.store.delete_session(session_id)
                reload_history()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="EXPORTAR SELECCIONADA",
                   command=export_selected).pack(side="left")
        ttk.Button(buttons, text="ELIMINAR SELECCIONADA",
                   command=delete_selected).pack(side="right")
        reload_history()

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
                    monitor["last_reading"] = payload
                    monitor["state"] = "Monitoreando"
                    self.connection_widgets[monitor_id].show_reading(payload)
                    self.port_list.update_ports(self.monitors, self.selected_monitor_id)
                    if monitor_id == self.selected_monitor_id:
                        self.show_selected_values()
                    self.alert.set(f"{monitor['tag']}: conexion activa")
                    self.alert_label.configure(fg=COLORS["accent"])
                elif kind == "outage":
                    self.store.add_event(payload)
                    LOGGER.warning("SIN_RESPUESTA | %s | %s | %s", payload["source_tag"],
                                   payload["port"], payload["detail"])
                    monitor["state"] = "SIN RESPUESTA"
                    self.connection_widgets[monitor_id].show_state("SIN RESPUESTA")
                    self.port_list.update_ports(self.monitors, self.selected_monitor_id)
                    if monitor_id == self.selected_monitor_id:
                        self.show_selected_values()
                    if not monitor["outage"]:
                        monitor["outage"] = True
                        self.bell()
                    self.alert.set(f"⚠ {monitor['tag']} SIN RESPUESTA — reintentando")
                    self.alert_label.configure(fg=COLORS["warning"])
                elif kind == "recovered":
                    self.store.add_event(payload)
                    monitor["outage"] = False
                    monitor["state"] = "Monitoreando"
                    if monitor["last_reading"] is not None:
                        self.connection_widgets[monitor_id].show_reading(monitor["last_reading"])
                    LOGGER.info("RECUPERADO | %s | %s | %s", payload["source_tag"],
                                payload["port"], payload["detail"])
                    self.alert.set(f"{monitor['tag']}: conexion recuperada")
                    self.alert_label.configure(fg=COLORS["accent"])
                    if monitor_id == self.selected_monitor_id:
                        self.status.set(f"Conexion recuperada; corte de {payload['duration']} segundos")
        except queue.Empty:
            pass
        self.after(100, self.process_messages)

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
            if monitor["worker"].is_alive():
                monitor["stop_event"].set()
                self.store.close_session(monitor["session_id"])
        for monitor in self.monitors.values():
            if monitor["worker"].is_alive():
                monitor["worker"].join(timeout=1)
        LOGGER.info("Aplicacion cerrada")
        self.store.connection.close()
        self.destroy()


if __name__ == "__main__":
    MonitorApp().mainloop()
