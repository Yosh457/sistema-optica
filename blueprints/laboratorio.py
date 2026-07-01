# blueprints/laboratorio.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from models import db, RecetaOftalmica, EstadoReceta, HistorialEstado
from utils import registrar_log_sistema

laboratorio_bp = Blueprint('laboratorio', __name__, template_folder='../templates', url_prefix='/laboratorio')

@laboratorio_bp.route('/bandeja')
@login_required
def bandeja_trabajo():
    """
    Vista tipo Kanban del laboratorio.
    Muestra las recetas agrupadas por sus estados operativos.
    """
    # 1: Pendiente Laboratorio, 2: Esperando Stock
    recetas_pendientes = RecetaOftalmica.query.filter(RecetaOftalmica.estado_id.in_([1, 2])).order_by(RecetaOftalmica.fecha_receta.asc()).all()
    
    # 3: En Fabricación
    recetas_fabricacion = RecetaOftalmica.query.filter_by(estado_id=3).order_by(RecetaOftalmica.fecha_modificacion.desc()).all()
    
    # 4: Lista (Esperando que caja facture y entregue)
    recetas_listas = RecetaOftalmica.query.filter_by(estado_id=4).order_by(RecetaOftalmica.fecha_modificacion.desc()).all()

    # Traemos los estados para los selects de cambio de estado rápido
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
    """
    receta = RecetaOftalmica.query.get_or_404(id)
    nuevo_estado_id = int(request.form.get('nuevo_estado_id'))
    observacion = request.form.get('observacion', '').strip()
    
    estado_anterior = receta.estado_id
    
    if estado_anterior != nuevo_estado_id:
        receta.estado_id = nuevo_estado_id
        receta.modificado_por = current_user.id
        
        # Registro Inmutable de Auditoría
        historial = HistorialEstado(
            tipo_entidad=HistorialEstado.TIPO_RECETA,
            entidad_id=receta.id,
            estado_anterior_id=estado_anterior,
            estado_nuevo_id=nuevo_estado_id,
            usuario_id=current_user.id,
            observacion=observacion if observacion else None
        )
        db.session.add(historial)
        
        try:
            db.session.commit()
            estado_obj = EstadoReceta.query.get(nuevo_estado_id)
            registrar_log_sistema("Taller Laboratorio", f"Receta {receta.id} movida a {estado_obj.nombre}")
            flash(f'Receta movida exitosamente a: {estado_obj.nombre}.', 'success')
        except Exception as e:
            db.session.rollback()
            flash(f'Error al cambiar de estado: {str(e)}', 'danger')
        
    return redirect(url_for('laboratorio.bandeja_trabajo'))