import struct
import unittest

from pzem_monitor import build_request, modbus_crc, parse_response


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


if __name__ == "__main__":
    unittest.main()
