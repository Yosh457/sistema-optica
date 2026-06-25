# blueprints/clinica.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import or_
from datetime import datetime

from models import db, Paciente, RecetaOftalmica
from utils import registrar_log_sistema

clinica_bp = Blueprint('clinica', __name__, template_folder='../templates', url_prefix='/clinica')

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
                Paciente.nombre_completo.ilike(f'%{busqueda}%'))
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
    if request.method == 'POST':
        rut = request.form.get('rut', '').strip().upper() # Estandarizamos mayúsculas
        nombre_completo = request.form.get('nombre_completo', '').strip()
        telefono = request.form.get('telefono', '').strip()
        direccion = request.form.get('direccion', '').strip()

        if Paciente.query.filter_by(rut=rut).first():
            flash(f'Error: El RUT {rut} ya está registrado.', 'danger')
            return render_template('clinica/crear_paciente.html', datos_previos=request.form)

        nuevo_paciente = Paciente(
            rut=rut,
            nombre_completo=nombre_completo,
            telefono=telefono,
            direccion=direccion,
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

    return render_template('clinica/crear_paciente.html', datos_previos=None)

@clinica_bp.route('/pacientes/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_paciente(id):
    paciente = Paciente.query.get_or_404(id)

    if request.method == 'POST':
        rut_nuevo = request.form.get('rut', '').strip().upper()
        
        existente = Paciente.query.filter_by(rut=rut_nuevo).first()
        if existente and existente.id != id:
            flash('Error: Ese RUT ya está asignado a otro paciente.', 'danger')
            return render_template('clinica/editar_paciente.html', paciente=paciente)

        paciente.rut = rut_nuevo
        paciente.nombre_completo = request.form.get('nombre_completo', '').strip()
        paciente.telefono = request.form.get('telefono', '').strip()
        paciente.direccion = request.form.get('direccion', '').strip()

        try:
            db.session.commit()
            registrar_log_sistema("Edición Paciente", f"Se actualizó al paciente ID {id} ({paciente.rut})")
            flash('Datos del paciente actualizados.', 'success')
            return redirect(url_for('clinica.ficha_paciente', id=paciente.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error de BD: {str(e)}', 'danger')

    return render_template('clinica/editar_paciente.html', paciente=paciente)

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
# FICHA CLÍNICA Y RECETAS
# ==============================================================================

@clinica_bp.route('/pacientes/<int:id>/ficha')
@login_required
def ficha_paciente(id):
    paciente = Paciente.query.get_or_404(id)
    # Obtenemos recetas ordenadas de la más reciente a la más antigua
    recetas = RecetaOftalmica.query.filter_by(paciente_id=id).order_by(RecetaOftalmica.fecha_receta.desc(), RecetaOftalmica.id.desc()).all()
    
    return render_template('clinica/ficha_paciente.html', paciente=paciente, recetas=recetas)

@clinica_bp.route('/pacientes/<int:id>/receta/crear', methods=['GET', 'POST'])
@login_required
def crear_receta(id):
    paciente = Paciente.query.get_or_404(id)

    if request.method == 'POST':
        fecha_str = request.form.get('fecha_receta')
        
        try:
            fecha_receta = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Error: Formato de fecha inválido.', 'danger')
            return render_template('clinica/crear_receta.html', paciente=paciente, hoy=datetime.today().strftime('%Y-%m-%d'))

        nueva_receta = RecetaOftalmica(
            fecha_receta=fecha_receta,
            od_esfera=request.form.get('od_esfera', '').strip(),
            od_cilindro=request.form.get('od_cilindro', '').strip(),
            od_eje=request.form.get('od_eje', '').strip(),
            oi_esfera=request.form.get('oi_esfera', '').strip(),
            oi_cilindro=request.form.get('oi_cilindro', '').strip(),
            oi_eje=request.form.get('oi_eje', '').strip(),
            distancia_pupilar=request.form.get('distancia_pupilar', '').strip(),
            adicion=request.form.get('adicion', '').strip(),
            observaciones=request.form.get('observaciones', '').strip(),
            paciente_id=paciente.id,
            usuario_id=current_user.id
        )

        try:
            db.session.add(nueva_receta)
            db.session.commit()
            registrar_log_sistema("Ingreso Receta", f"Nueva receta para paciente {paciente.rut}")
            flash('Receta oftalmológica ingresada correctamente.', 'success')
            return redirect(url_for('clinica.ficha_paciente', id=paciente.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al guardar receta: {str(e)}', 'danger')

    hoy = datetime.today().strftime('%Y-%m-%d')
    return render_template('clinica/crear_receta.html', paciente=paciente, hoy=hoy)