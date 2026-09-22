#include <WiFi.h>
#include <PubSubClient.h>
#include <PZEM004Tv30.h>
#include <SPI.h>
#include <SD.h>
#include <time.h>

#define PZEM_RX 16
#define PZEM_TX 17
#define SD_CS 5  // SPI: SCK 18, MISO 19, MOSI 23

const char* ssid = "MEGACABLE-2.4G-2AA6";
const char* password = "password";
const char* mqtt_server = "192.168.100.91";
const int mqtt_port = 1883;
const char* mqtt_user = "admin";
const char* mqtt_pass = "public";

const unsigned long READ_INTERVAL_MS = 5000;
const char* CSV_PATH = "/lecturas.csv";

PZEM004Tv30 pzem(Serial2, PZEM_RX, PZEM_TX);
WiFiClient espClient;
PubSubClient client(espClient);
bool sdReady = false;
unsigned long lastReading = 0;

void publishDiscovery() {
  const char* configs[][2] = {
    {"voltaje", "{\"name\":\"Voltaje\",\"uniq_id\":\"esp32_pzem_voltage\",\"stat_t\":\"energia/voltaje\",\"dev_cla\":\"voltage\",\"unit_of_meas\":\"V\",\"stat_cla\":\"measurement\"}"},
    {"corriente", "{\"name\":\"Corriente\",\"uniq_id\":\"esp32_pzem_current\",\"stat_t\":\"energia/corriente\",\"dev_cla\":\"current\",\"unit_of_meas\":\"A\",\"stat_cla\":\"measurement\"}"},
    {"potencia", "{\"name\":\"Potencia\",\"uniq_id\":\"esp32_pzem_power\",\"stat_t\":\"energia/potencia\",\"dev_cla\":\"power\",\"unit_of_meas\":\"W\",\"stat_cla\":\"measurement\"}"},
    {"energia", "{\"name\":\"Energia\",\"uniq_id\":\"esp32_pzem_energy\",\"stat_t\":\"energia/kwh\",\"dev_cla\":\"energy\",\"unit_of_meas\":\"kWh\",\"stat_cla\":\"total_increasing\"}"}
  };

  char topic[80];
  for (const auto& config : configs) {
    snprintf(topic, sizeof(topic), "homeassistant/sensor/esp32_pzem/%s/config", config[0]);
    client.publish(topic, config[1], true);
  }
}

void reconnectMQTT() {
  while (!client.connected() && WiFi.status() == WL_CONNECTED) {
    if (client.connect("ESP32PZEM", mqtt_user, mqtt_pass)) {
      Serial.println("MQTT conectado.");
      publishDiscovery();
    } else {
      Serial.printf("Fallo MQTT: %d\n", client.state());
      delay(2000);
    }
  }
}

void initializeSD() {
  sdReady = SD.begin(SD_CS);
  if (!sdReady) {
    Serial.println("No se pudo iniciar la microSD.");
    return;
  }
  if (!SD.exists(CSV_PATH)) {
    File file = SD.open(CSV_PATH, FILE_WRITE);
    if (file) {
      file.println("fecha,voltaje_V,corriente_A,potencia_W,energia_kWh");
      file.close();
    }
  }
  Serial.println("microSD lista: /lecturas.csv");
}

void getTimestamp(char* output, size_t size) {
  struct tm timeInfo;
  if (getLocalTime(&timeInfo, 100)) {
    strftime(output, size, "%Y-%m-%d %H:%M:%S", &timeInfo);
  } else {
    snprintf(output, size, "sin_hora_%lu", millis());
  }
}

void saveReading(float voltage, float current, float power, float energy) {
  if (!sdReady) return;
  File file = SD.open(CSV_PATH, FILE_APPEND);
  if (!file) {
    Serial.println("Error abriendo lecturas.csv.");
    return;
  }
  char timestamp[24];
  getTimestamp(timestamp, sizeof(timestamp));
  file.printf("%s,%.2f,%.3f,%.2f,%.4f\n", timestamp, voltage, current, power, energy);
  file.close();
  Serial.println("Lectura guardada en microSD.");
}

void publishValue(const char* topic, float value, int decimals) {
  char payload[20];
  snprintf(payload, sizeof(payload), "%.*f", decimals, value);
  client.publish(topic, payload, true);
}

void setup() {
  Serial.begin(9600);
  initializeSD();

  WiFi.begin(ssid, password);
  Serial.print("Conectando a WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi conectado.");

  configTime(-6 * 3600, 0, "pool.ntp.org", "time.nist.gov");
  client.setServer(mqtt_server, mqtt_port);
  client.setBufferSize(512);
  Serial.printf("Direccion PZEM detectada: 0x%02X\n", pzem.readAddress());
}

void loop() {
  if (!client.connected()) reconnectMQTT();
  client.loop();

  if (millis() - lastReading < READ_INTERVAL_MS) return;
  lastReading = millis();

  float voltage = pzem.voltage();
  float current = pzem.current();
  float power = pzem.power();
  float energy = pzem.energy();

  if (isnan(voltage) || isnan(current) || isnan(power) || isnan(energy)) {
    Serial.println("Error: lectura incompleta del PZEM; no se guardo.");
    return;
  }

  Serial.printf("V: %.2f V | I: %.3f A | P: %.2f W | E: %.4f kWh\n",
                voltage, current, power, energy);
  if (client.connected()) {
    publishValue("energia/voltaje", voltage, 2);
    publishValue("energia/corriente", current, 3);
    publishValue("energia/potencia", power, 2);
    publishValue("energia/kwh", energy, 4);
  }
  saveReading(voltage, current, power, energy);
}
