# blueprints/clinica.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import or_
from datetime import datetime

from models import db, Paciente, RecetaOftalmica, Producto, RecetaProducto, HistorialEstado, EstadoReceta
from utils import registrar_log_sistema

clinica_bp = Blueprint('clinica', __name__, template_folder='../templates', url_prefix='/clinica')

# CONSTANTE DE NEGOCIO (Evita IDs Mágicos)
ESTADO_RECETA_PENDIENTE = "Pendiente Laboratorio"

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
    # Traemos todos los productos activos para el selector dinámico de la cotización
    productos_disponibles = Producto.query.filter_by(activo=True).order_by(Producto.nombre).all()
    hoy = datetime.today().strftime('%Y-%m-%d')

    if request.method == 'POST':
        fecha_str = request.form.get('fecha_receta')
        
        try:
            fecha_receta = datetime.strptime(fecha_str, '%Y-%m-%d').date()
        except ValueError:
            flash('Error: Formato de fecha inválido.', 'danger')
            return render_template('clinica/crear_receta.html', paciente=paciente, productos=productos_disponibles, hoy=hoy, datos_previos=request.form)

        # Listas dinámicas del formulario de productos asociados
        productos_ids = request.form.getlist('producto_id[]')
        cantidades = request.form.getlist('cantidad[]')
        observaciones_prod = request.form.getlist('observaciones_prod[]')

        # ======================================================================
        # PRE-VALIDACIONES (Integridad del Carrito de la Receta)
        # ======================================================================
        carrito_validado = []
        for p_id, cant_str, obs in zip(productos_ids, cantidades, observaciones_prod):
            if not p_id or not p_id.strip():
                continue
            
            try:
                cantidad = int(cant_str)
            except (ValueError, TypeError):
                flash('Error: La cantidad de los insumos debe ser un número entero.', 'danger')
                return render_template('clinica/crear_receta.html', paciente=paciente, productos=productos_disponibles, hoy=hoy, datos_previos=request.form)

            if cantidad <= 0:
                flash('Error: La cantidad de cada producto debe ser mayor a 0.', 'danger')
                return render_template('clinica/crear_receta.html', paciente=paciente, productos=productos_disponibles, hoy=hoy, datos_previos=request.form)
            
            prod = db.session.get(Producto, int(p_id))
            if not prod:
                flash('Error de seguridad: Se detectó un producto inválido en la solicitud.', 'danger')
                return render_template('clinica/crear_receta.html', paciente=paciente, productos=productos_disponibles, hoy=hoy, datos_previos=request.form)
            
            carrito_validado.append({
                'producto': prod,
                'cantidad': cantidad,
                'observacion': obs.strip() if obs else None
            })

        if not carrito_validado:
            flash('Error Crítico: La receta clínica no puede estar vacía. Debe recetar al menos un componente (ej. marco o cristal).', 'danger')
            return render_template('clinica/crear_receta.html', paciente=paciente, productos=productos_disponibles, hoy=hoy, datos_previos=request.form)

        # Buscamos el ID dinámico del estado inicial
        estado_pendiente = EstadoReceta.query.filter_by(nombre=ESTADO_RECETA_PENDIENTE).first()
        if not estado_pendiente:
            flash('Error de configuración: El estado inicial de la receta no existe en el sistema.', 'danger')
            return render_template('clinica/crear_receta.html', paciente=paciente, productos=productos_disponibles, hoy=hoy, datos_previos=request.form)
        
        # ======================================================================
        # INSERCIÓN EN LA BASE DE DATOS
        # ======================================================================
        try:
            # Desactivar a todas las recetas anteriores del paciente
            RecetaOftalmica.query.filter_by(paciente_id=paciente.id).update({RecetaOftalmica.activa: False})

            # Creamos la cabecera de la receta (Sin ID Mágico)
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
                activa=True,
                estado_id=estado_pendiente.id, 
                paciente_id=paciente.id,
                usuario_id=current_user.id
            )
            db.session.add(nueva_receta)
            db.session.flush()

            # Insertamos el carrito validado
            for item in carrito_validado:
                subtotal = item['producto'].precio * item['cantidad']
                asociacion = RecetaProducto(
                    receta_id=nueva_receta.id,
                    producto_id=item['producto'].id,
                    cantidad=item['cantidad'],
                    precio_unitario=item['producto'].precio,
                    subtotal=subtotal,
                    observaciones=item['observacion']
                )
                db.session.add(asociacion)

            # Trazabilidad
            historial = HistorialEstado(
                tipo_entidad=HistorialEstado.TIPO_RECETA,
                entidad_id=nueva_receta.id,
                estado_anterior_id=None,
                estado_nuevo_id=estado_pendiente.id,
                usuario_id=current_user.id,
                observacion="Apertura automática de ficha técnica clínico-operativa."
            )
            db.session.add(historial)
            
            db.session.commit()
            registrar_log_sistema("Ingreso Receta", f"Nueva receta e insumos guardados para paciente {paciente.rut}")
            flash('Receta oftalmológica e insumos asociados registrados con éxito.', 'success')
            return redirect(url_for('clinica.ficha_paciente', id=paciente.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error crítico al guardar la receta: {str(e)}', 'danger')

    # Al cargar por primera vez (GET), datos_previos es None
    return render_template('clinica/crear_receta.html', paciente=paciente, productos=productos_disponibles, hoy=hoy, datos_previos=None)