"""
Modos de scoring: Flexible / Standard / Exigente.

Cada modo define un set completo de parametros que afecta:
- Pesos del Predictive Index
- Pisos minimos por dimension
- Tolerancia ortografica
- Mezcla empatia (pilares vs NPS)
- Curva de ART (cortes en segundos)
- Umbrales de categoria (Elite/Alto/Desarrollo)
- Umbrales de recomendacion
- Hint para el prompt de la IA evaluadora

El SuperAdmin puede personalizar estos valores via la tabla
ScoringModeOverride. Si no hay override, se usan los defaults de
fabrica definidos abajo.

Las sesiones LEGACY (sin scoring_mode asignado) se evaluan con
'standard' por compatibilidad pero se etiquetan como 'legacy' en la UI.
"""

MODE_NAMES = ('flexible', 'standard', 'exigente')

# ============================================================
#  Defaults de fabrica
# ============================================================

DEFAULT_MODES = {
    'flexible': {
        'label': 'Flexible',
        'icon': '🟢',
        'color': '#2e7d32',
        'when_to_use': 'Para nuevos ingresos, capacitacion inicial o procesos de seleccion. El sistema penaliza menos errores y reconoce avances pequeños.',
        'ai_hint': 'Estas evaluando a un asesor en formacion. Se generoso con NPS y empatia: cualquier intento razonable de personalizacion o calidez vale. Solo marcar errores ortograficos si son muy evidentes y afectan severamente la comprension. Premia el esfuerzo y el potencial.',
        # Pesos PI (suman 1.0)
        'pi_weights': {
            'empathy': 0.25, 'resolution': 0.20, 'communication': 0.20,
            'speed': 0.15, 'adaptability': 0.10, 'compliance': 0.10
        },
        # Pisos por dimension (raw 0-100)
        'floors': {
            'communication': 35, 'resolution': 35, 'adaptability': 35,
            'compliance': 35, 'empathy': 0, 'speed_no_data': 75
        },
        'spelling_multiplier': 35,        # Saturacion al 2.85% de errores
        'empathy_pillars_weight': 0.6,    # 60% pilares + 40% NPS
        'art_curve': {                    # ART en segundos -> puntaje
            'excellent_max': 180,         # ART <= 180s -> 100
            'healthy_max': 240,           # 180-240 -> 100->80
            'acceptable_max': 360,        # 240-360 -> 80->50
            'slow_max': 600,              # 360-600 -> 50->20
            'no_data_score': 75
        },
        'thresholds': {
            'elite_overall': 8.0, 'elite_min_dim': 6,
            'alto_overall': 5.5, 'alto_min_dim': 3,
            'desarrollo_overall': 3.5
        },
        'recommendation': {'recomendado': 55, 'observaciones': 35}
    },
    'standard': {
        'label': 'Standard',
        'icon': '🔵',
        'color': '#0277bd',
        'when_to_use': 'Modo recomendado para asesores activos en produccion. Refleja la operacion real y los criterios de calidad esperados.',
        'ai_hint': 'Aplica los criterios estandar de calidad. NPS 7+ requiere atencion empatica clara; ortografia: solo errores que afectan la comprension cuentan.',
        'pi_weights': {
            'empathy': 0.25, 'resolution': 0.22, 'communication': 0.18,
            'speed': 0.15, 'adaptability': 0.10, 'compliance': 0.10
        },
        'floors': {
            'communication': 30, 'resolution': 25, 'adaptability': 30,
            'compliance': 25, 'empathy': 0, 'speed_no_data': 65
        },
        'spelling_multiplier': 25,
        'empathy_pillars_weight': 0.7,
        'art_curve': {
            'excellent_max': 120, 'healthy_max': 180,
            'acceptable_max': 300, 'slow_max': 600,
            'no_data_score': 65
        },
        'thresholds': {
            'elite_overall': 8.5, 'elite_min_dim': 7,
            'alto_overall': 6.5, 'alto_min_dim': 4,
            'desarrollo_overall': 4.5
        },
        'recommendation': {'recomendado': 65, 'observaciones': 45}
    },
    'exigente': {
        'label': 'Exigente',
        'icon': '🔴',
        'color': '#c62828',
        'when_to_use': 'Solo para asesores expertos, calibracion o pruebas de excelencia. Un perfil "Recomendado" en este modo indica nivel referente.',
        'ai_hint': 'Aplica criterios estrictos. Solo NPS 9-10 si la atencion fue verdaderamente excepcional. Cualquier respuesta robotica o tipo speech reduce empatia. Cuenta errores ortograficos con rigor profesional. Premia solo la excelencia real, no el esfuerzo.',
        'pi_weights': {
            'empathy': 0.25, 'resolution': 0.25, 'communication': 0.18,
            'speed': 0.15, 'adaptability': 0.07, 'compliance': 0.10
        },
        'floors': {
            'communication': 20, 'resolution': 15, 'adaptability': 20,
            'compliance': 15, 'empathy': 0, 'speed_no_data': 50
        },
        'spelling_multiplier': 15,        # Saturacion al 6.6% (mas estricto)
        'empathy_pillars_weight': 0.8,
        'art_curve': {
            'excellent_max': 60, 'healthy_max': 120,
            'acceptable_max': 240, 'slow_max': 480,
            'no_data_score': 50
        },
        'thresholds': {
            'elite_overall': 9.0, 'elite_min_dim': 8,
            'alto_overall': 7.5, 'alto_min_dim': 6,
            'desarrollo_overall': 5.5
        },
        'recommendation': {'recomendado': 75, 'observaciones': 55}
    }
}

# ============================================================
#  Que mide cada cosa (guia pedagogica visible al admin)
# ============================================================

PEDAGOGICAL_GUIDE = {
    'pi_weights': {
        'title': 'Pesos del Predictive Index',
        'desc': 'Cuanto pesa cada una de las 6 dimensiones en el indice final que decide si un asesor es Recomendado / Con Observaciones / No Recomendado.',
        'why': 'Subir empatia hace que el sistema priorice trato humano sobre velocidad. Subir resolucion prioriza eficacia operativa.'
    },
    'floors': {
        'title': 'Pisos minimos por dimension',
        'desc': 'Puntaje base que recibe cada dimension antes de aplicar penalizaciones. Un piso alto significa "ningun asesor parte de cero, todos tienen un baseline".',
        'why': 'Pisos altos = mas indulgente con sesiones puntualmente bajas. Pisos bajos = un mal dia se nota mas en el perfil.'
    },
    'spelling_multiplier': {
        'title': 'Tolerancia ortografica',
        'desc': 'Cuantos errores hace falta para saturar la penalizacion. Multiplicador alto = mas tolerante (necesita pocos errores para penalizar 0%). Multiplicador bajo = mas estricto.',
        'why': 'En operaciones formales (banca, legal) conviene multiplier bajo. En atencion casual, multiplier alto.'
    },
    'empathy_pillars_weight': {
        'title': 'Mezcla empatia: pilares vs NPS',
        'desc': 'Que tanto pesa la rubrica explicita (Nombre/Contexto/Calidez/Resolucion) frente al NPS holistico de la IA.',
        'why': 'Mas peso a pilares = empatia mas auditable y entrenable. Mas peso a NPS = empatia mas intuitiva.'
    },
    'art_curve': {
        'title': 'Curva de tiempo de respuesta (ART)',
        'desc': 'En que punto el ART deja de ser excelente / saludable / aceptable / lento. Define cuanto puede tardar el asesor en responderle al cliente antes de bajar puntaje.',
        'why': 'Operaciones con multiples chats simultaneos pueden tener ART mas alto. Atencion 1-a-1 espera respuestas mas rapidas.'
    },
    'thresholds': {
        'title': 'Umbrales de categoria',
        'desc': 'Cuanto overall y por dimension hace falta para entrar en cada categoria (Elite / Alto / Desarrollo / Refuerzo).',
        'why': 'Subir umbrales hace mas dificil llegar a Elite o Alto. Bajar umbrales hace que la mayoria caiga en Desarrollo o mejor.'
    },
    'recommendation': {
        'title': 'Umbrales de recomendacion',
        'desc': 'Cuanto Predictive Index hace falta para ser Recomendado / Con Observaciones. Es la pregunta directa: "este asesor esta listo para la operativa?".',
        'why': 'Modo Flexible: bajar umbrales para que mas gente pase. Modo Exigente: subir para que solo los muy buenos pasen.'
    }
}

# Sintesis muy corta (una linea por dimension) para mostrar al admin
# que NO debe ver los numeros internos.
ADMIN_SUMMARY = {
    'flexible': [
        'Penaliza menos errores ortograficos (saturacion al 2.85%).',
        'ART meta hasta 180-240s (mas relajado).',
        'Para ser Recomendado basta 55% del Predictive Index.',
        'Pisos altos: una sesion mala no hunde el perfil.',
        'IA evaluadora: generosa, premia esfuerzo y potencial.'
    ],
    'standard': [
        'Tolerancia ortografica balanceada (saturacion al 4%).',
        'ART meta 120-180s (saludable para multitarea).',
        'Para ser Recomendado: 65% del Predictive Index.',
        'Refleja los criterios reales de produccion.',
        'IA evaluadora: criterios estandar de calidad.'
    ],
    'exigente': [
        'Ortografia estricta (saturacion al 6.6% de errores).',
        'ART meta 60-120s (rapido, sin que el cliente quede en visto).',
        'Para ser Recomendado: 75% del Predictive Index.',
        'Pisos bajos: cualquier debilidad se ve clara.',
        'IA evaluadora: solo premia la excelencia real.'
    ]
}


# ============================================================
#  Helpers
# ============================================================

def get_mode_config(mode_name):
    """
    Devuelve el dict de configuracion de un modo, prefiriendo overrides
    de SuperAdmin si existen. Para modos legacy o nulos -> Standard.
    """
    if not mode_name or mode_name == 'legacy':
        mode_name = 'standard'
    if mode_name not in DEFAULT_MODES:
        mode_name = 'standard'

    # Lazy import para evitar ciclos
    try:
        from models import ScoringModeOverride
        override = ScoringModeOverride.query.filter_by(mode=mode_name).first()
        if override and override.config_json:
            import json
            try:
                custom = json.loads(override.config_json)
                # Merge superficial sobre el default
                merged = {**DEFAULT_MODES[mode_name], **custom}
                return merged
            except (json.JSONDecodeError, TypeError):
                pass
    except Exception:
        # Si la tabla no existe (pre-migracion) o hay error, usamos default
        pass

    return DEFAULT_MODES[mode_name]


def get_effective_mode(mode_name):
    """
    Igual que get_mode_config pero devuelve tambien el nombre normalizado
    y un flag is_legacy.
    """
    is_legacy = (not mode_name) or mode_name == 'legacy'
    effective = 'standard' if is_legacy else mode_name
    if effective not in DEFAULT_MODES:
        effective = 'standard'
        is_legacy = True
    return effective, is_legacy, get_mode_config(effective)


def list_modes():
    """Devuelve los 3 modos con metadata para selectors UI."""
    return [
        {
            'key': name,
            'label': cfg['label'],
            'icon': cfg['icon'],
            'color': cfg['color'],
            'when_to_use': cfg['when_to_use'],
            'summary': ADMIN_SUMMARY.get(name, [])
        }
        for name, cfg in [(n, get_mode_config(n)) for n in MODE_NAMES]
    ]
