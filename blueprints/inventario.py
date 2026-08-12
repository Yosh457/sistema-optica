# blueprints/inventario.py
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import or_

from models import db, Producto, CategoriaProducto, Proveedor, MovimientoInventario
from utils.helpers import registrar_log_sistema
from services.inventario_service import InventarioService

# Instanciamos el blueprint
inventario_bp = Blueprint('inventario', __name__, template_folder='../templates', url_prefix='/inventario')

# ==============================================================================
# SEGURIDAD (Decorador de Roles)
# ==============================================================================
def admin_required(f):
    """Decorador para bloquear rutas críticas a usuarios que no sean Administradores."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.rol.nombre != 'Admin':
            registrar_log_sistema("Acceso Denegado", "Intento de acceso a ruta de administración de inventario.")
            flash('Acceso denegado. Se requieren privilegios de Administrador para esta acción.', 'danger')
            return redirect(url_for('inventario.listar_productos'))
        return f(*args, **kwargs)
    return decorated_function

# ==============================================================================
# SECCIÓN A: DIRECTORIO Y FICHA DEL PRODUCTO (Lectura General)
# ==============================================================================

@inventario_bp.route('/productos')
@login_required
def listar_productos():
    """Directorio principal. Incorpora filtros por estado y stock bajo."""
    page = request.args.get('page', 1, type=int)
    busqueda = request.args.get('busqueda', '').strip()
    categoria_filtro = request.args.get('categoria_id', '')
    estado_filtro = request.args.get('estado', '1') # 1 = Activos, 0 = Inactivos, '' = Todos
    stock_bajo = request.args.get('stock_bajo', '0') # 1 = Solo stock crítico

    query = Producto.query

    # Filtro de búsqueda por texto
    if busqueda:
        query = query.filter(
            or_(Producto.codigo.ilike(f'%{busqueda}%'),
                Producto.nombre.ilike(f'%{busqueda}%'))
        )
    
    # Filtro por categoría
    if categoria_filtro and categoria_filtro.isdigit():
        query = query.filter(Producto.categoria_id == int(categoria_filtro))
    
    # Filtro por estado    
    if estado_filtro in ['0', '1']:
        query = query.filter(Producto.activo == (estado_filtro == '1'))
    
    # Filtro por stock crítico    
    if stock_bajo == '1':
        query = query.filter(Producto.stock <= Producto.stock_minimo)

    # Paginación (15 productos por página)
    pagination = query.order_by(Producto.nombre).paginate(page=page, per_page=15, error_out=False)
    
    # Catálogo de categorías activas para el select del filtro
    categorias = CategoriaProducto.query.filter_by(activo=True).order_by(CategoriaProducto.nombre).all()

    # Estadísticas rápidas para los KPI del inventario
    stats = {
        'total_items': Producto.query.count(),
        'stock_critico': Producto.query.filter(Producto.stock <= Producto.stock_minimo, Producto.activo == True).count()
    }

    return render_template('inventario/productos.html', 
                           pagination=pagination, 
                           categorias=categorias, 
                           busqueda=busqueda, 
                           categoria_filtro=categoria_filtro,
                           estado_filtro=estado_filtro,
                           stock_bajo=stock_bajo,
                           stats=stats)

@inventario_bp.route('/productos/<int:id>/ficha')
@login_required
def ficha_producto(id):
    """Muestra el expediente del producto y su Kardex inmutable."""
    producto = Producto.query.get_or_404(id)
    
    # Obtenemos el historial ordenado por fecha descendente (más reciente primero)
    kardex = MovimientoInventario.query.filter_by(producto_id=producto.id).order_by(MovimientoInventario.fecha.desc()).all()
    
    # Pasamos proveedores activos para el modal de Ingreso de Mercadería
    proveedores = Proveedor.query.filter_by(activo=True).order_by(Proveedor.nombre).all()
    
    return render_template('inventario/ficha_producto.html', producto=producto, kardex=kardex, proveedores=proveedores)

# ==============================================================================
# SECCIÓN B: ADMINISTRACIÓN DEL PRODUCTO (Solo Admin)
# ==============================================================================

@inventario_bp.route('/productos/crear', methods=['GET', 'POST'])
@login_required
@admin_required
def crear_producto():
    """Crea un producto nuevo. Nace obligatoriamente con stock 0."""
    categorias = CategoriaProducto.query.filter_by(activo=True).order_by(CategoriaProducto.nombre).all()
    proveedores = Proveedor.query.filter_by(activo=True).order_by(Proveedor.nombre).all()

    if request.method == 'POST':
        codigo_raw = request.form.get('codigo', '').strip()
        codigo = codigo_raw if codigo_raw else None
        
        # Validación de duplicidad
        if codigo and Producto.query.filter_by(codigo=codigo).first():
            flash(f'Error: El código "{codigo}" ya está asignado a otro producto.', 'danger')
            return render_template('inventario/crear_producto.html', categorias=categorias, proveedores=proveedores, datos_previos=request.form)

        try:
            nuevo_producto = Producto(
                codigo=codigo,
                nombre=request.form.get('nombre', '').strip(),
                precio=float(request.form.get('precio', 0)),
                precio_compra=float(request.form.get('precio_compra', 0)),
                stock=0, # REGLA DE NEGOCIO: Nace sin stock
                stock_minimo=int(request.form.get('stock_minimo', 5)),
                categoria_id=request.form.get('categoria_id'),
                proveedor_id=request.form.get('proveedor_id') or None,
                activo=True
            )
            
            db.session.add(nuevo_producto)
            db.session.commit()
            registrar_log_sistema("Creación Producto", f"Se registró el producto: {codigo or 'S/C'} - {nuevo_producto.nombre}")
            flash('Producto creado exitosamente. Recuerde registrar un ingreso para añadir stock.', 'success')
            return redirect(url_for('inventario.ficha_producto', id=nuevo_producto.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al guardar en la base de datos: {str(e)}', 'danger')

    return render_template('inventario/crear_producto.html', categorias=categorias, proveedores=proveedores, datos_previos=None)

@inventario_bp.route('/productos/editar/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def editar_producto(id):
    """Modifica datos comerciales. No modifica Stock ni Precio de Compra histórico."""
    producto = Producto.query.get_or_404(id)
    categorias = CategoriaProducto.query.filter_by(activo=True).order_by(CategoriaProducto.nombre).all()
    proveedores = Proveedor.query.filter_by(activo=True).order_by(Proveedor.nombre).all()

    if request.method == 'POST':
        codigo_raw = request.form.get('codigo', '').strip()
        codigo_nuevo = codigo_raw if codigo_raw else None
        
        # Validación de duplicidad excluyendo el registro actual (solo si hay código)
        if codigo_nuevo:
            existente = Producto.query.filter_by(codigo=codigo_nuevo).first()
            if existente and existente.id != id:
                flash('Error: Ese código de barras ya pertenece a otro artículo.', 'danger')
                return render_template('inventario/editar_producto.html', producto=producto, categorias=categorias, proveedores=proveedores)
            
        try:
            producto.codigo = codigo_nuevo
            producto.nombre = request.form.get('nombre', '').strip()
            producto.precio = float(request.form.get('precio', 0))
            producto.stock_minimo = int(request.form.get('stock_minimo', 5))
            producto.categoria_id = request.form.get('categoria_id')
            producto.proveedor_id = request.form.get('proveedor_id') or None

            db.session.commit()
            registrar_log_sistema("Edición Producto", f"Se actualizó el producto ID {id} ({producto.nombre})")
            flash('Producto actualizado correctamente.', 'success')
            return redirect(url_for('inventario.ficha_producto', id=producto.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar: {str(e)}', 'danger')

    return render_template('inventario/editar_producto.html', producto=producto, categorias=categorias, proveedores=proveedores)

@inventario_bp.route('/productos/toggle/<int:id>', methods=['POST'])
@login_required
@admin_required
def toggle_producto(id):
    """Activa o Desactiva un producto comercialmente."""
    producto = Producto.query.get_or_404(id)
    producto.activo = not producto.activo
    db.session.commit()
    
    estado = "Reactivado" if producto.activo else "Desactivado"
    registrar_log_sistema("Cambio Estado Producto", f"El producto {producto.nombre} fue {estado}.")
    flash(f"Producto {producto.nombre} {estado.lower()} correctamente.", "success")
    return redirect(url_for('inventario.ficha_producto', id=id))

# ==============================================================================
# SECCIÓN C: OPERACIONES DE INVENTARIO (Llamadas al Service)
# ==============================================================================

@inventario_bp.route('/productos/<int:id>/ingreso', methods=['POST'])
@login_required
@admin_required
def registrar_ingreso(id):
    """Procesa el formulario modal de Ingreso por Factura/Guía."""
    producto = Producto.query.get_or_404(id)
    
    if not producto.activo:
        flash('No se puede ingresar stock a un producto inactivo. Reactívelo primero.', 'warning')
        return redirect(url_for('inventario.ficha_producto', id=id))
        
    try:
        # 1. Recolección y conversión de datos
        cantidad = int(request.form.get('cantidad', 0))
        costo_unitario = float(request.form.get('costo_unitario', 0.0))
        proveedor_id = request.form.get('proveedor_id')
        documento_tipo = request.form.get('documento_tipo', '').strip()
        documento_numero = request.form.get('documento_numero', '').strip()
        
        # 2. Llamada a la capa de negocio
        InventarioService.registrar_ingreso_compra(
            producto_id=producto.id,
            cantidad=cantidad,
            costo_unitario=costo_unitario,
            proveedor_id=proveedor_id,
            documento_tipo=documento_tipo,
            documento_numero=documento_numero,
            usuario_id=current_user.id
        )
        
        # 3. Confirmación de Transacción
        db.session.commit()
        registrar_log_sistema("Ingreso Inventario", f"Ingreso de {cantidad} uds. al producto ID {producto.id}")
        flash('Ingreso de mercadería registrado exitosamente en el Kardex.', 'success')
        
    except ValueError as e:
        # Errores de lógica de negocio (lanzados por el Servicio)
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:
        # Errores de BD o conversión de tipos
        db.session.rollback()
        flash('Ocurrió un error inesperado al procesar el ingreso. Revise los datos.', 'danger')

    return redirect(url_for('inventario.ficha_producto', id=id))

@inventario_bp.route('/productos/<int:id>/ajuste', methods=['POST'])
@login_required
@admin_required
def registrar_ajuste(id):
    """Procesa el formulario modal de Ajuste de Inventario."""
    producto = Producto.query.get_or_404(id)
    
    if not producto.activo:
        flash('No se puede ajustar el stock de un producto inactivo.', 'warning')
        return redirect(url_for('inventario.ficha_producto', id=id))
        
    try:
        cantidad = int(request.form.get('cantidad', 0))
        es_positivo = request.form.get('tipo_ajuste') == 'positivo'
        motivo = request.form.get('motivo', '').strip()
        
        InventarioService.registrar_ajuste(
            producto_id=producto.id,
            cantidad=cantidad,
            es_positivo=es_positivo,
            motivo=motivo,
            usuario_id=current_user.id
        )
        
        db.session.commit()
        tipo_texto = "Positivo" if es_positivo else "Negativo"
        registrar_log_sistema("Ajuste Inventario", f"Ajuste {tipo_texto} de {cantidad} uds. en producto ID {producto.id}")
        flash('Ajuste de inventario aplicado exitosamente.', 'success')
        
    except ValueError as e:
        db.session.rollback()
        flash(str(e), 'danger')
    except Exception as e:
        db.session.rollback()
        flash('Error al procesar el ajuste.', 'danger')

    return redirect(url_for('inventario.ficha_producto', id=id))

# ==============================================================================
# SECCIÓN D: CATÁLOGOS BASE (Solo Admin)
# ==============================================================================

@inventario_bp.route('/categorias', methods=['GET', 'POST'])
@login_required
@admin_required
def gestionar_categorias():
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        if CategoriaProducto.query.filter_by(nombre=nombre).first():
            flash('Error: Ya existe una categoría con ese nombre.', 'danger')
        else:
            db.session.add(CategoriaProducto(nombre=nombre, activo=True))
            db.session.commit()
            registrar_log_sistema("Creación Categoría", f"Se creó la categoría: {nombre}")
            flash('Nueva categoría registrada con éxito.', 'success')
            return redirect(url_for('inventario.gestionar_categorias'))

    page = request.args.get('page', 1, type=int)
    pagination = CategoriaProducto.query.order_by(CategoriaProducto.nombre).paginate(page=page, per_page=10, error_out=False)
    return render_template('inventario/categorias.html', pagination=pagination)

@inventario_bp.route('/categorias/toggle/<int:id>', methods=['POST'])
@login_required
@admin_required
def toggle_categoria(id):
    categoria = CategoriaProducto.query.get_or_404(id)
    categoria.activo = not categoria.activo
    db.session.commit()
    flash(f"Categoría {'activada' if categoria.activo else 'desactivada'} con éxito.", "success")
    return redirect(url_for('inventario.gestionar_categorias'))

@inventario_bp.route('/proveedores', methods=['GET', 'POST'])
@login_required
@admin_required
def gestionar_proveedores():
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        if Proveedor.query.filter_by(nombre=nombre).first():
            flash('Error: Ya existe un proveedor con ese nombre.', 'danger')
        else:
            db.session.add(Proveedor(nombre=nombre, activo=True))
            db.session.commit()
            registrar_log_sistema("Creación Proveedor", f"Se creó el proveedor: {nombre}")
            flash('Nuevo proveedor registrado con éxito.', 'success')
            return redirect(url_for('inventario.gestionar_proveedores'))

    page = request.args.get('page', 1, type=int)
    pagination = Proveedor.query.order_by(Proveedor.nombre).paginate(page=page, per_page=10, error_out=False)
    return render_template('inventario/proveedores.html', pagination=pagination)

@inventario_bp.route('/proveedores/toggle/<int:id>', methods=['POST'])
@login_required
@admin_required
def toggle_proveedor(id):
    proveedor = Proveedor.query.get_or_404(id)
    proveedor.activo = not proveedor.activo
    db.session.commit()
    flash(f"Proveedor {'activado' if proveedor.activo else 'desactivado'} con éxito.", "success")
    return redirect(url_for('inventario.gestionar_proveedores'))