# utils/helpers.py
from datetime import datetime
import pytz
from flask import request
from flask_login import current_user

def obtener_hora_chile():
    """Retorna la fecha y hora actual en Santiago de Chile."""
    cl_tz = pytz.timezone('America/Santiago')
    return datetime.now(cl_tz)

def obtener_ip_cliente():
    """
    Obtiene la IP real del cliente considerando proxies.

    Prioridad:
    1. X-Forwarded-For → usado por proxies (puede traer múltiples IPs)
    2. X-Real-IP → usado por algunos proxies simples
    3. remote_addr → conexión directa
    """
    try:
        # Caso 1: Proxy (puede traer varias IPs separadas por coma)
        if request.headers.get('X-Forwarded-For'):
            return request.headers.get('X-Forwarded-For').split(',')[0].strip()

        # Caso 2: Proxy simple
        if request.headers.get('X-Real-IP'):
            return request.headers.get('X-Real-IP')

        # Caso 3: Directo
        return request.remote_addr

    except Exception:
        # En caso de que request falle por algún motivo (tests, contextos raros, etc.)
        return None

def registrar_log_sistema(accion, detalles, usuario=None):
    """
    Registra un evento en la tabla 'logs' del sistema.
    Usa Lazy Import para evitar ciclos con models.py
    """
    from models import db, LogSistema  # Importación diferida para evitar ciclos

    try:
        user_id = None
        user_nombre = "Sistema/Anónimo"

        # Si pasamos un usuario explícito (ej: login exitoso)
        if usuario:
            user_id = usuario.id
            user_nombre = usuario.nombre_completo
        # Si no, intentamos sacar del current_user
        elif current_user and current_user.is_authenticated:
            user_id = current_user.id
            user_nombre = current_user.nombre_completo
            
        # Obtener IP del cliente
        ip = obtener_ip_cliente()

        nuevo_log = LogSistema(
            usuario_id=user_id,
            usuario_nombre=user_nombre,
            accion=accion,
            detalles=detalles,
            ip_origen=ip,
            timestamp=obtener_hora_chile()
        )
        db.session.add(nuevo_log)
        db.session.commit()
    except Exception as e:
        # En caso de error de DB, lo imprimimos en consola para no romper el flujo
        print(f"Error al registrar log: {e}")
        # Limpia la sesión para evitar que quede en estado inválido
        try:
            db.session.rollback()
        except Exception:
            pass