# Especificacion iFIX OPC DA - Estructura de Tags

## Estructura Jerarquica

El servidor OPC DA de iFIX (`Intellution.OPCiFIX.1`) expone los tags con la siguiente estructura:

```
NODO.TAG.CAMPO
```

Donde:
- **NODO**: Nombre del nodo SCADA (ej: `MYNODE`)
- **TAG**: Nombre del bloque/tag en iFIX (ej: `AAEXAMPLE01`)
- **CAMPO**: Atributo del tag (ej: `F_CV`, `A_CV`, `A_DESC`, etc.)

## Navegacion del Browser

El browser OPC tiene estructura jerarquica (`Organization: 1`):

```
RAIZ
  └── NODO (ej: MYNODE)
        ├── AA (Analog Alarm)
        ├── AI (Analog Input)
        ├── AO (Analog Output)
        ├── AR (Analog Register)
        ├── BL (Boolean)
        ├── CA (Calculator)
        ├── DA (Data Archiver)
        ├── DI (Digital Input)
        ├── DO (Digital Output)
        ├── DR (Digital Register)
        ├── EV (Event Action)
        ├── HS (Histogram)
        ├── ML (Multi-state Input)
        ├── PA (PID Auto/Manual)
        ├── PD (PID with Deadband)
        ├── PG (Program)
        ├── PI (PID)
        ├── RM (Ramp)
        ├── SC (Scan Alarm)
        ├── SQ (Sequencer)
        ├── SS (Signal Select)
        ├── ST (Statistical)
        ├── TM (Timer)
        ├── TR (Text Receiver)
        ├── TT (Totalizer)
        └── TX (Text)
              └── TAG1
                    ├── A_CV (valor texto)
                    ├── A_DESC
                    └── ... otros campos
```

## Tipos de Bloques y Campos de Valor

### Bloques de Texto (TX)
- Rama: `TX`
- Campo de valor: `.A_CV` (ASCII Current Value)
- Tipo de dato: `string`

### Todos los demas bloques
- Ramas: `AA`, `AI`, `AO`, `AR`, `BL`, `CA`, `DA`, `DI`, `DO`, `DR`, `EV`, `HS`, `ML`, `PA`, `PD`, `PG`, `PI`, `RM`, `SC`, `SQ`, `SS`, `ST`, `TM`, `TR`, `TT`
- Campo de valor: `.F_CV` (Float Current Value)
- Tipo de dato: `float` o `int`

## Campos Comunes

| Campo | Descripcion |
|-------|-------------|
| `F_CV` | Float Current Value - Valor actual numerico |
| `A_CV` | ASCII Current Value - Valor actual texto |
| `A_DESC` | Descripcion del tag |
| `F_HI` | Limite alto |
| `F_LO` | Limite bajo |
| `F_HIHI` | Limite muy alto (alarma) |
| `F_LOLO` | Limite muy bajo (alarma) |
| `A_ALMACK` | Estado de reconocimiento de alarma |
| `F_ENAB` | Habilitado |
| `A_SCAN` | Estado de escaneo |

## Algoritmo de Descubrimiento Optimizado

1. Conectar al servidor OPC DA
2. Crear browser y navegar a la raiz
3. Obtener el NODO (primera rama)
4. Para cada TIPO (rama dentro del nodo):
   - Si TIPO == "TX": usar sufijo `.A_CV`
   - Si TIPO != "TX": usar sufijo `.F_CV`
5. Para cada TAG dentro del TIPO:
   - Construir ItemID: `NODO.TAG.SUFIJO`
   - Agregar al listado de tags a suscribir

## Ejemplo de Tags

```
MYNODE.AAEXAMPLE01.F_CV    <- Rama AA, valor float
MYNODE.AI_TEMP_001.F_CV      <- Rama AI, valor float
MYNODE.TX_MESSAGE_01.A_CV    <- Rama TX, valor texto
MYNODE.DI_PUMP_RUN.F_CV      <- Rama DI, valor float (0/1)
```

## Notas de Implementacion

- El metodo `ilist(flat=True)` retorna 0 tags en iFIX
- Usar `ilist(flat=False, recursive=True)` para enumerar todos los campos
- Para descubrimiento rapido, navegar con browser y asumir sufijos segun rama
- El refresco periodico debe re-enumerar para detectar tags nuevos/eliminados
