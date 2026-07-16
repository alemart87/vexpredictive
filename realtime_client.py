"""
Cliente minimo de la OpenAI Realtime API (GA) para el modulo de voz.

Solo hace UNA cosa del lado del servidor: acunar el client secret efimero
(ek_...) con el que el navegador se conecta DIRECTO a OpenAI via WebRTC.
El audio nunca pasa por Flask. Mismo estilo que chat.py: urllib, sin SDK.
"""
import os
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

# Modelo realtime en un solo lugar (si OpenAI depreca el snapshot, se cambia aca)
REALTIME_MODEL = 'gpt-realtime-2.1-mini'
TRANSCRIPTION_MODEL = 'gpt-4o-mini-transcribe'

# Precios USD por millon de tokens de audio (para estimated_cost_usd)
AUDIO_INPUT_PRICE_PER_M = 10.0
AUDIO_OUTPUT_PRICE_PER_M = 20.0

# TTL del client secret: solo necesita durar hasta que el navegador
# establece el WebRTC; la sesion ya iniciada continua aunque expire.
CLIENT_SECRET_TTL_SECONDS = 120

# Duracion maxima de una llamada (el frontend corta; esto es el tope informativo)
MAX_CALL_SECONDS = 600

# Voces disponibles en los modelos gpt-realtime (GA).
# marin y cedar son las nuevas, recomendadas por OpenAI para produccion.
VOICES = [
    {'id': 'marin',   'label': 'Marin',   'desc': 'Femenina clara y brillante (recomendada)'},
    {'id': 'cedar',   'label': 'Cedar',   'desc': 'Masculina calida de rango medio (recomendada)'},
    {'id': 'alloy',   'label': 'Alloy',   'desc': 'Neutra y versatil'},
    {'id': 'ash',     'label': 'Ash',     'desc': 'Masculina serena'},
    {'id': 'ballad',  'label': 'Ballad',  'desc': 'Suave y expresiva'},
    {'id': 'coral',   'label': 'Coral',   'desc': 'Femenina energica'},
    {'id': 'echo',    'label': 'Echo',    'desc': 'Masculina firme'},
    {'id': 'sage',    'label': 'Sage',    'desc': 'Femenina tranquila'},
    {'id': 'shimmer', 'label': 'Shimmer', 'desc': 'Femenina calida'},
    {'id': 'verse',   'label': 'Verse',   'desc': 'Masculina expresiva'},
]
VOICE_IDS = {v['id'] for v in VOICES}
DEFAULT_VOICE = 'marin'


def valid_voice(name):
    """Devuelve la voz si es valida, o la default."""
    return name if name in VOICE_IDS else DEFAULT_VOICE


def mint_client_secret(instructions, voice=DEFAULT_VOICE, max_output_tokens=800):
    """Crea un client secret efimero para una sesion Realtime.

    Devuelve (data, error): data = {'client_secret', 'expires_at', 'session_id',
    'model'} o None; error = mensaje en espanol o None.
    """
    if not OPENAI_API_KEY:
        return None, 'El servicio de voz no esta configurado (falta OPENAI_API_KEY).'

    payload = {
        'expires_after': {'anchor': 'created_at', 'seconds': CLIENT_SECRET_TTL_SECONDS},
        'session': {
            'type': 'realtime',
            'model': REALTIME_MODEL,
            'instructions': instructions,
            'output_modalities': ['audio'],
            'audio': {
                'input': {
                    'transcription': {'model': TRANSCRIPTION_MODEL, 'language': 'es'},
                    'turn_detection': {'type': 'semantic_vad'},
                },
                'output': {'voice': valid_voice(voice)},
            },
            'max_output_tokens': max_output_tokens,
        },
    }

    req = Request(
        'https://api.openai.com/v1/realtime/client_secrets',
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {OPENAI_API_KEY}',
        },
        method='POST',
    )

    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        session_obj = data.get('session') or {}
        return {
            'client_secret': data.get('value', ''),
            'expires_at': data.get('expires_at'),
            'session_id': session_obj.get('id'),
            'model': REALTIME_MODEL,
        }, None
    except HTTPError as e:
        try:
            detail = e.read().decode('utf-8')[:500]
        except Exception:
            detail = str(e)
        print(f'[VOICE] client_secrets HTTP {e.code}: {detail}')
        return None, 'No se pudo iniciar la sesion de voz. Intenta de nuevo en unos segundos.'
    except (URLError, TimeoutError) as e:
        print(f'[VOICE] client_secrets network error: {e}')
        return None, 'No se pudo conectar con el servicio de voz. Verifica tu conexion.'
    except Exception as e:
        print(f'[VOICE] client_secrets unexpected: {e}')
        return None, 'Error inesperado al iniciar la sesion de voz.'


def estimate_cost_usd(input_tokens, output_tokens):
    """Estimacion de costo del audio realtime (no incluye la evaluacion posterior)."""
    return round(
        (input_tokens / 1_000_000.0) * AUDIO_INPUT_PRICE_PER_M
        + (output_tokens / 1_000_000.0) * AUDIO_OUTPUT_PRICE_PER_M, 6)
