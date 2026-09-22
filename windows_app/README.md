# PZEM Monitor para Windows

Esta aplicacion lee un PZEM-004T v3.0 conectado mediante un adaptador USB-TTL,
guarda todas las lecturas en SQLite, muestra una grafica de potencia y exporta CSV.
No requiere ESP32, Wi-Fi ni MQTT.

## Ejecutar desde el codigo

1. Instala Python 3 para Windows y activa **Add Python to PATH**.
2. Abre PowerShell en esta carpeta.
3. Ejecuta:

```powershell
py -m pip install pyserial
py pzem_monitor.py
```

Selecciona el puerto (por ejemplo `COM9`), conserva direccion `1` y pulsa
**CONECTAR**. Ningun otro programa puede tener abierto el mismo puerto.

## Generar el EXE

```powershell
PowerShell -ExecutionPolicy Bypass -File .\build.ps1
```

El resultado queda en `dist\PZEM-Monitor.exe`. La base de datos se guarda en
`C:\Users\<usuario>\PZEM Monitor\lecturas.db`; el boton **Exportar CSV** permite
crear reportes que pueden abrirse en Excel.

El adaptador debe usar logica TTL de 3.3/5 V compatible con el PZEM. No conectes
los pines seriales directamente a tensiones de red y desconecta la alimentacion
antes de modificar el cableado.
