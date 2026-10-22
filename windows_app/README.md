# PZEM Monitor para Windows

Esta aplicación lee uno o varios PZEM-004T v3.0 conectados mediante adaptadores
USB-TTL, guarda todas las lecturas en SQLite, muestra un dashboard de widgets y
genera CSV.
No requiere ESP32, Wi-Fi ni MQTT.

## Dashboard de widgets

La interfaz principal muestra una tarjeta independiente por cada conexión. Cada
tarjeta presenta simultáneamente:

| Widget | Dato |
| --- | --- |
| Voltaje | V |
| Corriente | A |
| Potencia | W |
| Energía | kWh |
| Frecuencia | Hz |
| Factor de potencia | |
| Voltaje reciente | Gráfica de voltaje independiente para cada conexión |

Las tarjetas de todos los puertos permanecen visibles y cuentan con desplazamiento
cuando ya no caben en la pantalla. Cada tarjeta incluye su propia gráfica de
**Voltaje reciente**, por lo que las gráficas de todos los puertos se observan al
mismo tiempo. Las conexiones alternan colores de gráfica —amarillo y rojo— para
distinguirlas rápidamente. La selección de la barra lateral sólo se usa para
detener un puerto o identificar su estado; no oculta las demás conexiones.

## Monitorear varios puertos

Pulsa **+ AGREGAR PUERTO**, elige el COM y asigna una etiqueta descriptiva, por
ejemplo `Tablero norte`. Cada puerto tiene su propio hilo de lectura, reintentos,
alertas de comunicación y tarjeta de métricas. Selecciona un puerto en la barra
lateral para ver su gráfica o detenerlo sin afectar los demás.

No se permite abrir dos veces el mismo COM ni repetir una etiqueta mientras esa
escucha esté configurada.

Si ya existen datos para la misma etiqueta y puerto, la aplicación pregunta si
se desea **retomar** la última sesión, **comenzar una nueva** o cancelar. Esta
pregunta no aparece cuando el puerto ya está activo, porque no es posible abrir
dos veces el mismo COM.

El botón **HISTORIAL** muestra las sesiones anteriores con sus fechas y cantidad
de registros. Desde ahí se puede exportar una sola sesión o eliminarla. Una
sesión activa nunca puede eliminarse. Compilar o instalar una versión nueva no
borra el historial automáticamente.

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
| `C:\Users\<usuario>\PZEM Monitor\csv\lecturas_<etiqueta>_sesion_<id>.csv` | Copia CSV automática por sesión |

Cada lectura y error se confirma inmediatamente en SQLite y se agrega al CSV
automático correspondiente. Desde **HISTORIAL** se puede exportar una sesión para
Excel con las columnas `etiqueta`, `puerto`, `estado` y `detalle`. Antes de detener
o cerrar, la interfaz advierte que dejará de registrar; todo lo guardado
anteriormente se conserva aunque el equipo se apague después.

## Ejecutar desde el código

1. Instala Python 3 para Windows y activa **Add Python to PATH**.
2. Abre PowerShell en esta carpeta.
3. Ejecuta:

```powershell
py -m pip install pyserial
py pzem_monitor.py
```

Selecciona el puerto (por ejemplo `COM9`), conserva dirección `1`, ajusta los
intervalos y pulsa **INICIAR ESCUCHA**. Ningun otro programa puede tener abierto
el mismo puerto.

## Generar el EXE

```powershell
PowerShell -ExecutionPolicy Bypass -File .\build.ps1
```

El resultado queda en `dist\PZEM-Monitor.exe`. El ejecutable puede copiarse a otra
computadora Windows sin instalar Python.

## Raspberry Pi OS / Linux

La aplicación funciona en Raspberry Pi con escritorio gráfico. Los puertos se
mostrarán normalmente como `/dev/ttyUSB0` o `/dev/ttyACM0`. Instala primero las
dependencias del sistema:

```bash
sudo apt update
sudo apt install python3 python3-venv python3-tk
sudo usermod -aG dialout "$USER"
```

Cierra la sesión y vuelve a entrar después de agregar el usuario a `dialout`.
Para ejecutarla directamente desde el código:

```bash
cd windows_app
python3 -m venv .venv-linux
.venv-linux/bin/pip install -r requirements.txt
.venv-linux/bin/python pzem_monitor.py
```

Para generar un único ejecutable ARM en la propia Raspberry:

```bash
cd windows_app
./build_linux.sh
./dist/PZEM-Monitor
```

El resultado queda en `windows_app/dist/PZEM-Monitor`. No se puede reutilizar el
EXE de Windows ni compilar en una PC x86 y copiarlo a ARM; genera el binario en la
misma Raspberry, o en otro Linux con la misma arquitectura y versión compatible.
Los datos seguirán guardándose en `~/PZEM Monitor/`.

El adaptador debe usar lógica TTL de 3.3/5 V compatible con el PZEM. No conectes
los pines seriales directamente a tensiones de red y desconecta la alimentación
antes de modificar el cableado.
