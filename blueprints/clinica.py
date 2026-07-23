# blueprints/clinica.py
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, send_from_directory
from flask_login import login_required, current_user
from sqlalchemy import or_
from datetime import datetime

from models import db, Paciente, RecetaOftalmica, Establecimiento, HistorialEstado, EstadoReceta
from utils import registrar_log_sistema

clinica_bp = Blueprint('clinica', __name__, template_folder='../templates', url_prefix='/clinica')

# CONSTANTE DE NEGOCIO (Evita IDs Mágicos)
ESTADO_RECETA_PENDIENTE = "Pendiente Laboratorio"

# Extensiones permitidas para recetas
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ==============================================================================
# GESTIÓN DE PACIENTES
# ==============================================================================

@clinica_bp.route('/pacientes')
@login_required
def listar_pacientes():
    page = request.args.get('page', 1, type=int)
    busqueda = request.args.get('busqueda', '').strip()

    query = Paciente.query

    if busqueda:
        query = query.filter(
            or_(Paciente.rut.ilike(f'%{busqueda}%'),
                Paciente.nombre_completo.ilike(f'%{busqueda}%'),
                Paciente.telefono.ilike(f'%{busqueda}%'))
        )
    
    pagination = query.order_by(Paciente.nombre_completo).paginate(page=page, per_page=10, error_out=False)
    
    stats = {
        'total_pacientes': Paciente.query.count(),
        'pacientes_activos': Paciente.query.filter_by(activo=True).count()
    }

    return render_template('clinica/pacientes.html', pagination=pagination, busqueda=busqueda, stats=stats)

@clinica_bp.route('/pacientes/crear', methods=['GET', 'POST'])
@login_required
def crear_paciente():
    establecimientos = Establecimiento.query.filter_by(activo=True).order_by(Establecimiento.nombre).all()

    if request.method == 'POST':
        rut = request.form.get('rut', '').strip().upper()
        nombre_completo = request.form.get('nombre_completo', '').strip()
        telefono = request.form.get('telefono', '').strip()
        direccion = request.form.get('direccion', '').strip()
        establecimiento_id = request.form.get('establecimiento_id')

        if not establecimiento_id:
            flash('Error: Debe seleccionar un Recinto Inscrito.', 'danger')
            return render_template('clinica/crear_paciente.html', establecimientos=establecimientos, datos_previos=request.form)

        if Paciente.query.filter_by(rut=rut).first():
            flash(f'Error: El RUT {rut} ya está registrado.', 'danger')
            return render_template('clinica/crear_paciente.html', establecimientos=establecimientos, datos_previos=request.form)

        nuevo_paciente = Paciente(
            rut=rut,
            nombre_completo=nombre_completo,
            telefono=telefono,
            direccion=direccion,
            establecimiento_id=establecimiento_id,
            activo=True
        )

        try:
            db.session.add(nuevo_paciente)
            db.session.commit()
            registrar_log_sistema("Creación Paciente", f"Se registró al paciente {rut} - {nombre_completo}")
            flash('Paciente registrado con éxito.', 'success')
            return redirect(url_for('clinica.ficha_paciente', id=nuevo_paciente.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error de BD: {str(e)}', 'danger')

    return render_template('clinica/crear_paciente.html', establecimientos=establecimientos, datos_previos=None)

@clinica_bp.route('/pacientes/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_paciente(id):
    paciente = Paciente.query.get_or_404(id)
    establecimientos = Establecimiento.query.filter_by(activo=True).order_by(Establecimiento.nombre).all()

    if request.method == 'POST':
        rut_nuevo = request.form.get('rut', '').strip().upper()
        establecimiento_id = request.form.get('establecimiento_id')
        
        existente = Paciente.query.filter_by(rut=rut_nuevo).first()
        if existente and existente.id != id:
            flash('Error: Ese RUT ya está asignado a otro paciente.', 'danger')
            return render_template('clinica/editar_paciente.html', paciente=paciente, establecimientos=establecimientos)

        paciente.rut = rut_nuevo
        paciente.nombre_completo = request.form.get('nombre_completo', '').strip()
        paciente.telefono = request.form.get('telefono', '').strip()
        paciente.direccion = request.form.get('direccion', '').strip()
        paciente.establecimiento_id = establecimiento_id

        try:
            db.session.commit()
            registrar_log_sistema("Edición Paciente", f"Se actualizó al paciente ID {id} ({paciente.rut})")
            flash('Datos del paciente actualizados.', 'success')
            return redirect(url_for('clinica.ficha_paciente', id=paciente.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error de BD: {str(e)}', 'danger')

    return render_template('clinica/editar_paciente.html', paciente=paciente, establecimientos=establecimientos)

@clinica_bp.route('/pacientes/toggle/<int:id>', methods=['POST'])
@login_required
def toggle_paciente(id):
    paciente = Paciente.query.get_or_404(id)
    paciente.activo = not paciente.activo
    db.session.commit()
    
    estado = "activado" if paciente.activo else "desactivado"
    registrar_log_sistema("Cambio Estado Paciente", f"Paciente {paciente.rut} {estado}.")
    flash(f"Paciente {paciente.rut} {estado}.", "success")
    return redirect(url_for('clinica.listar_pacientes'))

# ==============================================================================
# FICHA CLÍNICA Y RECETAS DIGITALIZADAS
# ==============================================================================

@clinica_bp.route('/pacientes/<int:id>/ficha')
@login_required
def ficha_paciente(id):
    paciente = Paciente.query.get_or_404(id)
    
    # Recetas ordenadas
    recetas = RecetaOftalmica.query.filter_by(paciente_id=id).order_by(RecetaOftalmica.fecha_registro.desc()).all()
    
    # Pre-cálculo de estadísticas para la UI
    stats = {
        'total': len(recetas),
        'vigentes': sum(1 for r in recetas if r.activa),
        'cerradas': sum(1 for r in recetas if not r.activa),
        'ultima_fecha': recetas[0].fecha_registro.strftime('%d-%m-%Y') if recetas else 'N/A'
    }

    # Cargar la línea de tiempo de estados para cada receta
    historial_por_receta = {}
    for receta in recetas:
        historial = HistorialEstado.query.filter_by(
            tipo_entidad=HistorialEstado.TIPO_RECETA,
            entidad_id=receta.id
        ).order_by(HistorialEstado.fecha.desc()).all()
        historial_por_receta[receta.id] = historial
        
    # Diccionario estático para garantizar meses en español siempre
    meses_espanol = {
        1: 'enero', 2: 'febrero', 3: 'marzo', 4: 'abril',
        5: 'mayo', 6: 'junio', 7: 'julio', 8: 'agosto',
        9: 'septiembre', 10: 'octubre', 11: 'noviembre', 12: 'diciembre'
    }

    return render_template('clinica/ficha_paciente.html', 
                           paciente=paciente, 
                           recetas=recetas, 
                           stats=stats,
                           historial_por_receta=historial_por_receta,
                           meses=meses_espanol)

@clinica_bp.route('/pacientes/<int:id>/receta/crear', methods=['GET', 'POST'])
@login_required
def crear_receta(id):
    paciente = Paciente.query.get_or_404(id)
    
    # Contar recetas existentes para mostrarlas en la UI
    total_recetas_previas = RecetaOftalmica.query.filter_by(paciente_id=paciente.id).count()

    if request.method == 'POST':
        # 1. Validación del archivo
        if 'archivo_receta' not in request.files:
            flash('No se seleccionó ningún archivo.', 'danger')
            return redirect(request.url)
            
        file = request.files['archivo_receta']
        if file.filename == '':
            flash('El nombre del archivo está vacío.', 'danger')
            return redirect(request.url)

        if not (file and allowed_file(file.filename)):
            flash('Extensión de archivo no permitida. Use PDF, JPG o PNG.', 'danger')
            return redirect(request.url)

        observaciones = request.form.get('observaciones', '').strip()
        
        estado_pendiente = EstadoReceta.query.filter_by(nombre=ESTADO_RECETA_PENDIENTE).first()
        if not estado_pendiente:
            flash('Error de configuración: El estado inicial de la receta no existe.', 'danger')
            return redirect(request.url)

        try:
            # 2. Guardado físico del archivo (Ruta segura en la raíz del proyecto)
            upload_folder = os.path.join(current_app.root_path, 'uploads', 'recetas')
            os.makedirs(upload_folder, exist_ok=True)
            
            # Nombre de archivo limpio con RUT y timestamp para evitar sobreescritura
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            extension = os.path.splitext(file.filename)[1].lower()
            safe_filename = f"{paciente.rut}_{timestamp}{extension}"
            
            file_path = os.path.join(upload_folder, safe_filename)
            file.save(file_path)

            # 3. Lógica de Base de Datos (SIN auto-desactivación de recetas anteriores)
            nueva_receta = RecetaOftalmica(
                archivo_receta=safe_filename,
                observaciones=observaciones,
                activa=True,
                estado_id=estado_pendiente.id, 
                paciente_id=paciente.id,
                usuario_id=current_user.id
            )
            db.session.add(nueva_receta)
            db.session.flush()

            historial = HistorialEstado(
                tipo_entidad=HistorialEstado.TIPO_RECETA,
                entidad_id=nueva_receta.id,
                estado_anterior_id=None,
                estado_nuevo_id=estado_pendiente.id,
                usuario_id=current_user.id,
                observacion="Carga inicial de receta digitalizada."
            )
            db.session.add(historial)
            
            db.session.commit()
            registrar_log_sistema("Ingreso Receta", f"Receta digitalizada cargada para paciente {paciente.rut}")
            flash('Documento subido y registrado con éxito.', 'success')
            return redirect(url_for('clinica.ficha_paciente', id=paciente.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error crítico al guardar: {str(e)}', 'danger')

    return render_template('clinica/crear_receta.html', paciente=paciente, total_recetas_previas=total_recetas_previas)

@clinica_bp.route('/receta/<int:id>/documento')
@login_required
def ver_documento_receta(id):
    """Ruta protegida para servir el archivo digitalizado sin exponer la carpeta en la web."""
    receta = RecetaOftalmica.query.get_or_404(id)
    upload_folder = os.path.join(current_app.root_path, 'uploads', 'recetas')
    return send_from_directory(upload_folder, receta.archivo_receta)