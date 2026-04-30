# Vex People Predictive

Plataforma empresarial multi-tenant de entrenamientos, evaluacion predictiva del talento y ayuda en linea. Desarrollado por **VEX I+D** con branding **Voicenter**.

## Descripcion

Vex People Predictive permite a las organizaciones:

- **Gestionar contenido** de ayuda en linea y knowledge base por operativa
- **Entrenar agentes** con simulaciones de clientes impulsadas por IA (GPT-5.4 mini)
- **Evaluar competencias** con el sistema Vex People Skill Predictive (6 dimensiones + indice predictivo). Ver [scoring.md](scoring.md) para el detalle de formulas, pesos y umbrales.
- **Asistente VEX AI** - chatbot inteligente que responde en base al contenido cargado
- **Analytics** - dashboards de uso, insights y recomendaciones automaticas
- **Multi-tenancy** - cada Operativa tiene sus propios usuarios, contenido, escenarios y branding personalizado

## Stack Tecnologico

| Componente | Tecnologia |
|---|---|
| Backend | Flask 3.1 + SQLAlchemy |
| Base de datos | PostgreSQL (Render) |
| Frontend | Jinja2 + Vanilla JS |
| Editor CMS | Quill.js |
| Graficos | Chart.js |
| IA | OpenAI GPT-5.4 mini |
| Auth | Flask-Login (session-based) |
| Server | Gunicorn |
| Deploy | Docker en Render.com |

## Jerarquia de Roles

```
SuperAdmin (administra toda la plataforma)
  └── Operativa / Cuenta (tenant)
       └── Coordinador / Gerente / SubGerente
            ├── Supervisor (puede solicitar revisiones)
            └── Operador (acceso a contenido y entrenamientos)
```

## Variables de Entorno

Crear un archivo `.env` en la raiz del proyecto:

```env
DATABASE_URL=postgresql://user:password@host:5432/dbname
SECRET_KEY=tu-clave-secreta-produccion
SUPERADMIN_EMAIL=admin@tudominio.com
SUPERADMIN_PASSWORD=contrasena-segura
OPENAI_API_KEY=sk-tu-api-key-de-openai
```

| Variable | Requerida | Descripcion |
|---|---|---|
| `DATABASE_URL` | Si | URI de conexion a PostgreSQL |
| `SECRET_KEY` | Si | Clave secreta para sesiones Flask |
| `SUPERADMIN_EMAIL` | Si | Email del administrador principal |
| `SUPERADMIN_PASSWORD` | Si | Contrasena del administrador principal |
| `OPENAI_API_KEY` | Si | API key de OpenAI para VEX AI y evaluaciones |
| `UPLOAD_DIR` | No | Directorio de uploads (default: `static/imagenes/`) |

## Deploy en Render.com

### 1. Crear base de datos PostgreSQL

1. En Render Dashboard, click **New > PostgreSQL**
2. Nombre: `vexpredictive`
3. Region: Oregon (o la mas cercana)
4. Plan: Free o Starter
5. Copiar la **Internal Database URL** generada

### 2. Crear Web Service

1. Click **New > Web Service**
2. Conectar el repositorio de GitHub
3. Configuracion:
   - **Name:** `vexpredictive`
   - **Region:** Misma que la base de datos
   - **Runtime:** Docker
   - **Plan:** Free o Starter

4. **Variables de entorno** (Settings > Environment):
   - `DATABASE_URL` = Internal Database URL del paso 1
   - `SECRET_KEY` = una cadena aleatoria larga
   - `SUPERADMIN_EMAIL` = email del admin
   - `SUPERADMIN_PASSWORD` = contrasena segura
   - `OPENAI_API_KEY` = tu API key de OpenAI

5. (Opcional) Agregar **Persistent Disk**:
   - Mount Path: `/persistent`
   - Agregar variable: `UPLOAD_DIR=/persistent`

### 3. Deploy automatico

Render detecta el `Dockerfile` y ejecuta:
1. Instala dependencias de `requirements.txt`
2. Ejecuta migraciones (`migrate_v2.py`, `migrate_v3.py`, `migrate_v4.py`, `migrate_v5.py`)
3. Inicia Gunicorn en puerto 10000

### 4. Post-deploy

1. Acceder a la URL generada por Render
2. Login con las credenciales de SuperAdmin configuradas
3. Crear la primera Operativa desde **Admin > Operativas**
4. Crear un Coordinador asignado a la Operativa
5. El Coordinador puede crear usuarios, contenido y escenarios

## Desarrollo Local

```bash
# Clonar repositorio
git clone https://github.com/alemart87/vexpredictive.git
cd vexpredictive

# Crear entorno virtual
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env  # Editar con tus valores

# Ejecutar migraciones
python migrate_v2.py
python migrate_v3.py
python migrate_v4.py
python migrate_v5.py

# Iniciar servidor de desarrollo
python app.py
```

La aplicacion estara disponible en `http://localhost:5000`

## Estructura del Proyecto

```
vexpredictive/
├── app.py                  # Flask app, rutas principales, auth
├── models.py               # Modelos SQLAlchemy (User, Operativa, Content, Training...)
├── admin.py                # Blueprint admin (CRUD contenido, usuarios, operativas)
├── training.py             # Blueprint training (escenarios, sesiones, VEX profiles)
├── chat.py                 # Blueprint chat (VEX AI asistente)
├── analytics.py            # Blueprint analytics (tracking pageviews, clicks)
├── decorators.py           # Decoradores de autorizacion
├── scoring.md              # Documentacion del sistema de scoring (formulas, pesos, ART)
├── migrate_v2.py           # Migracion: tablas de training
├── migrate_v3.py           # Migracion: multi-tenant (operativas)
├── migrate_v4.py           # Migracion: profile_photo en users
├── migrate_v5.py           # Migracion: avg_response_time (ART) en training_sessions
├── Dockerfile              # Build y deploy
├── requirements.txt        # Dependencias Python
├── static/
│   ├── css/                # style.css, chat.css, training.css
│   ├── js/                 # chat.js, training.js, tracking.js
│   ├── img/                # Logo Voicenter, favicon
│   └── imagenes/           # Uploads de usuarios
└── templates/
    ├── base.html           # Template base (header, nav, footer, chat widget)
    ├── login.html
    ├── index.html
    ├── admin/              # Templates de administracion
    └── training/           # Templates de entrenamiento
```

## Sistema de Scoring (resumen)

Cada sesion de entrenamiento se evalua con IA y produce: NPS (0-10), correctitud, errores ortograficos relevantes y un breakdown de empatia (Nombre / Contexto / Calidez / Resolucion). El perfil VEX agrega los datos en 6 dimensiones:

| Dimension | Peso en Predictive Index |
|---|---|
| Empatia (rubrica jerarquica de 4 pilares + NPS) | 25% |
| Resolucion | 22% |
| Comunicacion | 18% |
| Velocidad (basada en ART, no duracion total) | 15% |
| Adaptabilidad | 10% |
| Compliance | 10% |

**ART (Average Response Time):** mide el tiempo medio entre el mensaje del cliente y la respuesta del asesor. Meta saludable con multiples chats simultaneos: **120-180s**. No castiga la lentitud del cliente ni la duracion total del chat.

**Categorias:** Elite (overall >=8.5 y todas >=7) / Alto (>=6.5 y todas >=4) / Desarrollo (>=4.5) / Refuerzo (<4.5).
**Recomendacion:** Recomendado (PI >=65%) / Observaciones (45-65%) / No Recomendado (<45%).

Detalle completo de formulas, pisos minimos y reglas de ortografia leniente en [scoring.md](scoring.md).

## Licencia

Propiedad de VEX I+D. Todos los derechos reservados.
