# blueprints/admin.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import or_

# Modelos
from models import db, Usuario, RolAplicacion, LogSistema
# Utilidades
from utils import registrar_log_sistema, admin_required, enviar_credenciales_nuevo_usuario

admin_bp = Blueprint('admin', __name__, template_folder='../templates', url_prefix='/admin')

# --- PROTECCIÓN GLOBAL DEL BLUEPRINT ---
@admin_bp.before_request
@login_required
@admin_required
def before_request():
    """Protección global: Solo admins autenticados pueden acceder aquí."""
    pass

# --- RUTAS DE ADMINISTRACIÓN ---

@admin_bp.route('/panel')
def panel():
    """
    Vista principal del Panel de Administración.
    Muestra estadísticas rápidas y la tabla de usuarios con paginación y filtros.
    """
    # --- Filtros ---
    page = request.args.get('page', 1, type=int)
    busqueda = request.args.get('busqueda', '')
    rol_filtro = request.args.get('rol_filtro', '')
    
    query = Usuario.query

    # Filtro por texto (Nombre o Email)
    if busqueda:
        query = query.filter(
            or_(Usuario.nombre_completo.ilike(f'%{busqueda}%'),
                Usuario.email.ilike(f'%{busqueda}%'))
        )
    
    # Filtro por Rol de Aplicación
    if rol_filtro:
        query = query.filter(Usuario.rol_id == rol_filtro)
    
    # Paginación de usuarios (10 usuarios por página)
    pagination = query.order_by(Usuario.id).paginate(page=page, per_page=10, error_out=False)
    roles_para_filtro = RolAplicacion.query.order_by(RolAplicacion.nombre).all()
    
    # Estadísticas
    stats = {
        'total_usuarios': Usuario.query.count(),
        'usuarios_activos': Usuario.query.filter_by(activo=True).count()
    }

    return render_template('admin/panel.html', 
                           pagination=pagination,
                           roles_para_filtro=roles_para_filtro,
                           busqueda=busqueda,
                           rol_filtro=rol_filtro,
                           stats=stats)

@admin_bp.route('/crear_usuario', methods=['GET', 'POST'])
def crear_usuario():
    """Formulario para registrar nuevos usuarios."""
    roles = RolAplicacion.query.order_by(RolAplicacion.nombre).all()

    if request.method == 'POST':
        nombre = request.form.get('nombre_completo')
        email = request.form.get('email')
        password = request.form.get('password')
        rol_id = request.form.get('rol_id')
        forzar_cambio = request.form.get('forzar_cambio_clave') == '1'

        # 1. Validación de duplicidad
        if Usuario.query.filter_by(email=email).first():
            flash('Error: El correo electrónico ya está registrado.', 'danger')
            return render_template('admin/crear_usuario.html', roles=roles, datos_previos=request.form)

        # 2. Construcción del nuevo objeto Usuario
        nuevo_usuario = Usuario(
            nombre_completo=nombre, 
            email=email, 
            rol_id=rol_id,
            cambio_clave_requerido=forzar_cambio, 
            activo=True
        )
        nuevo_usuario.set_password(password)
        
        try:
            db.session.add(nuevo_usuario)
            db.session.commit()

            # 3. Auditoría y Notificación (Log + Envío de Credenciales)
            registrar_log_sistema("Creación Usuario", f"Admin creó a {nombre} ({email}) con rol ID {rol_id}.")
            
            if enviar_credenciales_nuevo_usuario(nuevo_usuario, password):
                flash(f'Usuario creado con éxito. Credenciales enviadas a {email}.', 'success')
            else:
                # Si falla el correo, avisamos al admin para que entregue la clave manualmente
                flash(f'Usuario creado, pero FALLÓ el envío del correo. Por favor, entregue la clave temporal manualmente: {password}', 'warning')
            
            return redirect(url_for('admin.panel'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error de base de datos al crear usuario: {str(e)}', 'danger')

    return render_template('admin/crear_usuario.html', roles=roles, datos_previos=request.form)

@admin_bp.route('/editar_usuario/<int:id>', methods=['GET', 'POST'])
def editar_usuario(id):
    """Permite modificar los datos básicos, perfil y permisos granulares de un usuario."""
    usuario = Usuario.query.get_or_404(id)
    roles = RolAplicacion.query.order_by(RolAplicacion.nombre).all()

    if request.method == 'POST':
        email_nuevo = request.form.get('email')
        
        # 1. Validación de duplicidad excluyendo al usuario actual
        usuario_existente = Usuario.query.filter_by(email=email_nuevo).first()
        if usuario_existente and usuario_existente.id != id:
            flash('Error: Ese correo ya pertenece a otro usuario.', 'danger')
            return render_template('admin/editar_usuario.html', usuario=usuario, roles=roles)

        # 2. Actualización de campos escalares
        usuario.nombre_completo = request.form.get('nombre_completo')
        usuario.email = email_nuevo
        usuario.rol_id = request.form.get('rol_id')
        usuario.cambio_clave_requerido = request.form.get('forzar_cambio_clave') == '1'

        password = request.form.get('password')
        if password and password.strip():
            usuario.set_password(password)
            flash('Contraseña actualizada correctamente.', 'info')

        try:
            db.session.commit()
            registrar_log_sistema("Edición Usuario", f"Admin editó perfil de {usuario.nombre_completo}")
            flash('Usuario actualizado con éxito.', 'success')
            return redirect(url_for('admin.panel'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar la base de datos: {str(e)}', 'danger')

    return render_template('admin/editar_usuario.html', usuario=usuario, roles=roles)

@admin_bp.route('/toggle_activo/<int:id>', methods=['POST'])
def toggle_activo(id):
    """
    Habilita o deshabilita a un usuario. 
    Protege al usuario actual de desactivarse a sí mismo.
    """
    usuario = Usuario.query.get_or_404(id)
    if usuario.id == current_user.id:
        flash('Medida de seguridad: No puedes desactivar tu propia cuenta de administrador.', 'danger')
        return redirect(url_for('admin.panel'))
        
    usuario.activo = not usuario.activo
    db.session.commit()
    
    estado = "activado" if usuario.activo else "desactivado"
    registrar_log_sistema("Cambio Estado", f"El usuario {usuario.nombre_completo} fue {estado}.")
    flash(f'Usuario {usuario.nombre_completo} {estado} correctamente.', 'success')
    
    return redirect(url_for('admin.panel'))

@admin_bp.route('/ver_logs_sistema')
def ver_logs_sistema():
    """Muestra el historial de auditoría administrativa y de sesión de los usuarios."""
    page = request.args.get('page', 1, type=int)
    usuario_filtro = request.args.get('usuario_id')
    accion_filtro = request.args.get('accion')

    query = LogSistema.query.order_by(LogSistema.timestamp.desc())

    # Aplicamos filtros si existen
    if usuario_filtro and usuario_filtro.isdigit():
        query = query.filter(LogSistema.usuario_id == int(usuario_filtro))
    if accion_filtro:
        query = query.filter(LogSistema.accion == accion_filtro)

    pagination = query.paginate(page=page, per_page=15, error_out=False)
    todos_los_usuarios = Usuario.query.order_by(Usuario.nombre_completo).all()
    # Catálogo de acciones únicas registradas en el sistema hasta ahora
    acciones_unicas = [r[0] for r in db.session.query(LogSistema.accion).distinct().all()]

    return render_template('admin/ver_logs.html', 
                           pagination=pagination,
                           todos_los_usuarios=todos_los_usuarios,
                           acciones_posibles=acciones_unicas,
                           filtros={'usuario_id': usuario_filtro, 'accion': accion_filtro})