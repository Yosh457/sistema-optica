# blueprints/laboratorio.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, RecetaOftalmica, EstadoReceta, HistorialEstado
from utils import registrar_log_sistema

laboratorio_bp = Blueprint('laboratorio', __name__, template_folder='../templates', url_prefix='/laboratorio')

# ==============================================================================
# CONSTANTES DE NEGOCIO (Cero IDs Mágicos)
# ==============================================================================
ESTADO_RECETA_PENDIENTE = "Pendiente Laboratorio"
ESTADO_RECETA_ESPERANDO = "Esperando Stock"
ESTADO_RECETA_FABRICACION = "En Fabricación"
ESTADO_RECETA_LISTA = "Lista"
ESTADO_RECETA_ANULADA = "Anulada"

@laboratorio_bp.route('/bandeja')
@login_required
def bandeja_trabajo():
    """
    Vista tipo Kanban del laboratorio.
    Adaptación temporal: Solo lee la tabla de recetas y ordena por fecha_registro.
    """
    # 1. Pendientes y Esperando Stock
    recetas_pendientes = RecetaOftalmica.query.join(EstadoReceta).filter(
        EstadoReceta.nombre.in_([ESTADO_RECETA_PENDIENTE, ESTADO_RECETA_ESPERANDO]),
        RecetaOftalmica.activa.is_(True)
    ).order_by(RecetaOftalmica.fecha_registro.asc()).all()
    
    # 2. En Fabricación
    recetas_fabricacion = RecetaOftalmica.query.join(EstadoReceta).filter(
        EstadoReceta.nombre == ESTADO_RECETA_FABRICACION,
        RecetaOftalmica.activa.is_(True)
    ).order_by(RecetaOftalmica.fecha_modificacion.desc()).all()
    
    # 3. Listas (Esperando a ser facturadas)
    recetas_listas = RecetaOftalmica.query.join(EstadoReceta).filter(
        EstadoReceta.nombre == ESTADO_RECETA_LISTA,
        RecetaOftalmica.activa.is_(True)
    ).order_by(RecetaOftalmica.fecha_modificacion.desc()).all()
    
    # 4. Estados disponibles para el dropdown de cambio de estado
    estados_disponibles = EstadoReceta.query.order_by(EstadoReceta.orden).all()

    return render_template('laboratorio/bandeja.html', 
                           pendientes=recetas_pendientes, 
                           fabricacion=recetas_fabricacion, 
                           listas=recetas_listas,
                           estados=estados_disponibles)

@laboratorio_bp.route('/cambiar_estado/<int:id>', methods=['POST'])
@login_required
def cambiar_estado(id):
    """
    Procesa la transición de estado de una receta y registra la auditoría.
    Protegido contra inyecciones, valores vacíos y concurrencia.
    """
    receta = RecetaOftalmica.query.get_or_404(id)
    
    # ESCUDO 1: Evitar que modifiquen una receta que ya fue procesada por caja (inactiva)
    if not receta.activa:
        flash('Acción denegada: La receta seleccionada ya fue cerrada, entregada o anulada previamente.', 'warning')
        return redirect(url_for('laboratorio.bandeja_trabajo'))

    # ESCUDO 2: Validar que el request envíe el dato
    nuevo_estado_raw = request.form.get('nuevo_estado_id')
    if not nuevo_estado_raw:
        flash('Error: No se envió ningún estado válido.', 'danger')
        return redirect(url_for('laboratorio.bandeja_trabajo'))
    
    # ESCUDO 3: Validar que sea un número entero
    try:
        nuevo_estado_id = int(nuevo_estado_raw)
    except ValueError:
        flash('Error de seguridad: Formato de estado inválido.', 'danger')
        return redirect(url_for('laboratorio.bandeja_trabajo'))

    # ESCUDO 4: Validar que el estado realmente exista en la Base de Datos
    estado_obj = db.session.get(EstadoReceta, nuevo_estado_id)
    if not estado_obj:
        flash('Error: El estado seleccionado no existe en los parámetros del sistema.', 'danger')
        return redirect(url_for('laboratorio.bandeja_trabajo'))

    observacion = request.form.get('observacion', '').strip()
    estado_anterior = receta.estado_id
    
    if estado_anterior != estado_obj.id:
        receta.estado_id = estado_obj.id
        receta.modificado_por = current_user.id
        
        # LÓGICA DE NEGOCIO: Si el taller la anula, la inactivamos para sacarla del flujo sin depender de un ID numérico
        if estado_obj.nombre == ESTADO_RECETA_ANULADA:
            receta.activa = False
            if not observacion:
                observacion = "Anulada directamente desde Taller/Laboratorio."
        
        # Registro Inmutable de Auditoría
        historial = HistorialEstado(
            tipo_entidad=HistorialEstado.TIPO_RECETA,
            entidad_id=receta.id,
            estado_anterior_id=estado_anterior,
            estado_nuevo_id=estado_obj.id,
            usuario_id=current_user.id,
            observacion=observacion if observacion else None
        )
        db.session.add(historial)
        
        try:
            db.session.commit()
            registrar_log_sistema("Taller Laboratorio", f"Receta {receta.id} movida a {estado_obj.nombre}")
            flash(f'Receta movida exitosamente a: {estado_obj.nombre}.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error de base de datos al cambiar de estado: {str(e)}', 'danger')
        
    return redirect(url_for('laboratorio.bandeja_trabajo'))