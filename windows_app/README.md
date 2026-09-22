# PZEM Monitor para Windows

Esta aplicación lee un PZEM-004T v3.0 conectado mediante un adaptador USB-TTL,
guarda todas las lecturas en SQLite, muestra una gráfica de potencia y exporta CSV.
No requiere ESP32, Wi-Fi ni MQTT.

## Comportamiento ante un corte

Si el PZEM pierde energía o comunicación, el programa no se cierra ni borra datos:

1. Muestra la alerta **PZEM SIN RESPUESTA** y emite un sonido.
2. Registra cada intento fallido como `SIN_RESPUESTA`.
3. Reintenta automáticamente con el intervalo elegido.
4. Al recibir nuevamente datos, registra `RECUPERADO` y la duración del corte.
5. Continúa agregando mediciones y reconstruye la gráfica desde la base existente.

Los campos eléctricos quedan vacíos durante un evento, porque una falta de
respuesta no demuestra que el voltaje sea cero. La computadora debe seguir
encendida para registrar el periodo; utiliza batería o UPS si es necesario.

## Configuración y almacenamiento

- **Medición (s):** intervalo entre lecturas normales.
- **Reconexión (s):** intervalo entre intentos durante una falla.
- **Dirección:** normalmente `1` para un PZEM-004T nuevo.

Los datos se confirman en disco después de cada lectura o evento:

| Ruta | Uso |
| --- | --- |
| `C:\Users\<usuario>\PZEM Monitor\lecturas.db` | Historial completo SQLite |
| `C:\Users\<usuario>\PZEM Monitor\pzem_monitor.log` | Lecturas, errores y recuperaciones |

El botón **Exportar CSV** genera un reporte para Excel que incluye las columnas
`estado` y `detalle`. Antes de detener o cerrar, la interfaz advierte que dejará de
registrar; todo lo guardado anteriormente se conserva.

## Ejecutar desde el código

1. Instala Python 3 para Windows y activa **Add Python to PATH**.
2. Abre PowerShell en esta carpeta.
3. Ejecuta:

```powershell
py -m pip install pyserial
py pzem_monitor.py
```

Selecciona el puerto (por ejemplo `COM9`), conserva dirección `1` y pulsa
**CONECTAR**. Ningun otro programa puede tener abierto el mismo puerto.

## Generar el EXE

```powershell
PowerShell -ExecutionPolicy Bypass -File .\build.ps1
```

El resultado queda en `dist\PZEM-Monitor.exe`. El ejecutable puede copiarse a otra
computadora Windows sin instalar Python.

El adaptador debe usar lógica TTL de 3.3/5 V compatible con el PZEM. No conectes
los pines seriales directamente a tensiones de red y desconecta la alimentación
antes de modificar el cableado.
