import csv
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
            energy REAL, frequency REAL, power_factor REAL)"""
        )
        self.connection.commit()

    def add(self, reading: dict):
        self.connection.execute(
            "INSERT INTO readings VALUES (?, ?, ?, ?, ?, ?, ?)",
            (reading["timestamp"], reading["voltage"], reading["current"],
             reading["power"], reading["energy"], reading["frequency"],
             reading["power_factor"]),
        )
        self.connection.commit()

    def recent(self, limit=120):
        return self.connection.execute(
            "SELECT timestamp, power FROM readings ORDER BY rowid DESC LIMIT ?", (limit,)
        ).fetchall()[::-1]

    def export_csv(self, destination: str):
        rows = self.connection.execute("SELECT * FROM readings ORDER BY timestamp").fetchall()
        with open(destination, "w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("fecha", "voltaje_V", "corriente_A", "potencia_W",
                             "energia_kWh", "frecuencia_Hz", "factor_potencia"))
            writer.writerows(rows)


class PzemWorker(threading.Thread):
    def __init__(self, port, address, interval, messages, stop_event):
        super().__init__(daemon=True)
        self.port = port
        self.address = address
        self.interval = interval
        self.messages = messages
        self.stop_event = stop_event

    def run(self):
        try:
            with serial.Serial(self.port, 9600, bytesize=8, parity="N", stopbits=1,
                               timeout=1) as connection:
                self.messages.put(("status", f"Conectado a {self.port}"))
                while not self.stop_event.is_set():
                    connection.reset_input_buffer()
                    connection.write(build_request(self.address))
                    reading = parse_response(connection.read(25), self.address)
                    reading["timestamp"] = datetime.now().isoformat(sep=" ", timespec="seconds")
                    self.messages.put(("reading", reading))
                    self.stop_event.wait(self.interval)
        except Exception as error:
            self.messages.put(("error", str(error)))


class MonitorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PZEM-004T Monitor")
        self.geometry("850x610")
        self.minsize(760, 540)
        self.store = ReadingStore(DB_PATH)
        self.messages = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.values = {}
        self._build_ui()
        self.refresh_ports()
        self.draw_graph()
        self.after(100, self.process_messages)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self):
        controls = ttk.Frame(self, padding=10)
        controls.pack(fill="x")
        ttk.Label(controls, text="Puerto COM:").pack(side="left")
        self.port = ttk.Combobox(controls, width=15, state="readonly")
        self.port.pack(side="left", padx=5)
        ttk.Button(controls, text="Actualizar", command=self.refresh_ports).pack(side="left")
        ttk.Label(controls, text="Direccion:").pack(side="left", padx=(15, 2))
        self.address = tk.IntVar(value=1)
        ttk.Spinbox(controls, from_=1, to=247, textvariable=self.address, width=5).pack(side="left")
        ttk.Label(controls, text="Intervalo (s):").pack(side="left", padx=(15, 2))
        self.interval = tk.DoubleVar(value=3)
        ttk.Spinbox(controls, from_=1, to=3600, textvariable=self.interval, width=6).pack(side="left")
        self.connect_button = ttk.Button(controls, text="CONECTAR", command=self.toggle_connection)
        self.connect_button.pack(side="right")

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
        self.stop_event.clear()
        self.worker = PzemWorker(self.port.get(), self.address.get(), self.interval.get(),
                                 self.messages, self.stop_event)
        self.worker.start()
        self.connect_button.configure(text="DETENER")

    def disconnect(self):
        self.stop_event.set()
        self.connect_button.configure(text="CONECTAR")
        self.status.set("Desconectado")

    def process_messages(self):
        try:
            while True:
                kind, payload = self.messages.get_nowait()
                if kind == "reading":
                    self.store.add(payload)
                    for key, (variable, unit) in self.values.items():
                        decimals = 4 if key == "energy" else 3 if key == "current" else 2
                        variable.set(f"{payload[key]:.{decimals}f} {unit}")
                    self.status.set(f"Ultima lectura: {payload['timestamp']}")
                    self.draw_graph()
                elif kind == "status":
                    self.status.set(payload)
                else:
                    self.disconnect()
                    messagebox.showerror("Error de comunicacion", payload)
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
        self.stop_event.set()
        self.destroy()


if __name__ == "__main__":
    MonitorApp().mainloop()
