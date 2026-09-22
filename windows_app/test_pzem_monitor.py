import struct
import tempfile
import unittest
from pathlib import Path

from pzem_monitor import ReadingStore, build_request, modbus_crc, parse_response


class PzemProtocolTests(unittest.TestCase):
    def test_request_for_default_address(self):
        self.assertEqual(build_request(1), bytes.fromhex("01040000000a700d"))

    def test_parse_measurement(self):
        registers = (1270, 93, 0, 45, 0, 5, 0, 600, 100, 0)
        body = bytes((1, 4, 20)) + struct.pack(">10H", *registers)
        response = body + struct.pack("<H", modbus_crc(body))
        reading = parse_response(response, 1)

        self.assertEqual(reading["voltage"], 127.0)
        self.assertEqual(reading["current"], 0.093)
        self.assertEqual(reading["power"], 4.5)
        self.assertEqual(reading["energy"], 0.005)
        self.assertEqual(reading["frequency"], 60.0)
        self.assertEqual(reading["power_factor"], 1.0)

    def test_store_keeps_readings_and_outage_events(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReadingStore(Path(directory) / "test.db")
            store.add({
                "timestamp": "2026-09-22 12:00:00", "voltage": 127.0,
                "current": 0.093, "power": 4.5, "energy": 0.005,
                "frequency": 60.0, "power_factor": 1.0,
            })
            store.add_event({
                "timestamp": "2026-09-22 12:00:03",
                "status": "SIN_RESPUESTA", "detail": "timeout",
            })

            rows = store.connection.execute(
                "SELECT status, power FROM readings ORDER BY rowid"
            ).fetchall()
            self.assertEqual(rows, [("OK", 4.5), ("SIN_RESPUESTA", None)])

    def test_store_migrates_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            connection = __import__("sqlite3").connect(path)
            connection.execute(
                """CREATE TABLE readings (
                timestamp TEXT NOT NULL, voltage REAL, current REAL, power REAL,
                energy REAL, frequency REAL, power_factor REAL)"""
            )
            connection.execute(
                "INSERT INTO readings VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("2026-09-22 11:00:00", 127, 0.1, 5, 0.1, 60, 1),
            )
            connection.commit()
            connection.close()

            store = ReadingStore(path)
            row = store.connection.execute(
                "SELECT status, detail FROM readings"
            ).fetchone()
            self.assertEqual(row, ("OK", None))


if __name__ == "__main__":
    unittest.main()
