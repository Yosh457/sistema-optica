# blueprints/inventario.py
from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from sqlalchemy import or_

from models import db, Producto, CategoriaProducto
from utils import registrar_log_sistema

# Instanciamos el blueprint con su prefijo correspondiente
inventario_bp = Blueprint('inventario', __name__, template_folder='../templates', url_prefix='/inventario')

@inventario_bp.route('/productos')
@login_required
def listar_productos():
    """
    Muestra la lista de productos con paginación, buscador por código/descripción
    y filtros avanzados por categoría. Incluye lógica de alertas de stock.
    """
    page = request.args.get('page', 1, type=int)
    busqueda = request.args.get('busqueda', '').strip()
    categoria_filtro = request.args.get('categoria_id', '')

    query = Producto.query

    # Filtro de búsqueda por texto
    if busqueda:
        query = query.filter(
            or_(Producto.codigo.ilike(f'%{busqueda}%'),
                Producto.descripcion.ilike(f'%{busqueda}%'))
        )
    
    # Filtro por categoría
    if categoria_filtro and categoria_filtro.isdigit():
        query = query.filter(Producto.categoria_id == int(categoria_filtro))

    # Paginación (10 productos por página)
    pagination = query.order_by(Producto.descripcion).paginate(page=page, per_page=10, error_out=False)
    
    # Catálogo de categorías activas para el select del filtro
    categorias = CategoriaProducto.query.filter_by(activo=True).order_by(CategoriaProducto.nombre).all()

    # Estadísticas rápidas para los KPI del inventario
    stats = {
        'total_items': Producto.query.count(),
        'stock_critico': Producto.query.filter(Producto.stock <= Producto.stock_minimo).count()
    }

    return render_template('inventario/productos.html', 
                           pagination=pagination, 
                           categorias=categorias, 
                           busqueda=busqueda, 
                           categoria_filtro=categoria_filtro,
                           stats=stats)

@inventario_bp.route('/productos/crear', methods=['GET', 'POST'])
@login_required
def crear_producto():
    """Formulario de registro para nuevos artículos oftálmicos o accesorios."""
    categorias = CategoriaProducto.query.filter_by(activo=True).order_by(CategoriaProducto.nombre).all()

    if request.method == 'POST':
        codigo = request.form.get('codigo', '').strip()
        descripcion = request.form.get('descripcion', '').strip()
        precio = request.form.get('precio')
        stock = request.form.get('stock')
        stock_minimo = request.form.get('stock_minimo')
        categoria_id = request.form.get('categoria_id')

        # Validación de código único
        if Producto.query.filter_by(codigo=codigo).first():
            flash(f'Error: El código "{codigo}" ya está asignado a otro producto.', 'danger')
            return render_template('inventario/crear_producto.html', categories=categorias, datos_previos=request.form)

        nuevo_producto = Producto(
            codigo=codigo,
            descripcion=descripcion,
            precio=precio,
            stock=stock,
            stock_minimo=stock_minimo,
            categoria_id=categoria_id,
            activo=True
        )

        try:
            db.session.add(nuevo_producto)
            db.session.commit()
            registrar_log_sistema("Creación Producto", f"Se registró el producto {codigo} - {descripcion}")
            flash('Producto creado exitosamente en el inventario.', 'success')
            return redirect(url_for('inventario.listar_productos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al guardar en la base de datos: {str(e)}', 'danger')

    return render_template('inventario/crear_producto.html', categorias=categorias, datos_previos=None)

@inventario_bp.route('/productos/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_producto(id):
    """Permite actualizar precios, stock y datos básicos de un item."""
    producto = Producto.query.get_or_404(id)
    categorias = CategoriaProducto.query.filter_by(activo=True).order_by(CategoriaProducto.nombre).all()

    if request.method == 'POST':
        codigo_nuevo = request.form.get('codigo', '').strip()
        
        # Validación de duplicidad excluyendo el registro actual
        existente = Producto.query.filter_by(codigo=codigo_nuevo).first()
        if existente and existente.id != id:
            flash('Error: Ese código de barras ya pertenece a otro artículo.', 'danger')
            return render_template('inventario/editar_producto.html', producto=producto, categorias=categorias)

        producto.codigo = codigo_nuevo
        producto.descripcion = request.form.get('descripcion', '').strip()
        producto.precio = request.form.get('precio')
        producto.stock = request.form.get('stock')
        producto.stock_minimo = request.form.get('stock_minimo')
        producto.categoria_id = request.form.get('categoria_id')

        try:
            db.session.commit()
            registrar_log_sistema("Edición Producto", f"Se actualizó el producto ID {id} ({producto.codigo})")
            flash('Producto actualizado correctamente.', 'success')
            return redirect(url_for('inventario.listar_productos'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error al actualizar: {str(e)}', 'danger')

    return render_template('inventario/editar_producto.html', producto=producto, categorias=categorias)

@inventario_bp.route('/productos/toggle/<int:id>', methods=['POST'])
@login_required
def toggle_producto(id):
    """Cambia el estado de activación (Soft Delete) de un producto."""
    producto = Producto.query.get_or_404(id)
    producto.activo = not producto.activo
    db.session.commit()
    
    estado = "activado" if producto.activo else "desactivado"
    registrar_log_sistema("Cambio Estado Producto", f"El producto {producto.codigo} fue {estado}.")
    flash(f"Producto {producto.codigo} {estado} correctamente.", "success")
    return redirect(url_for('inventario.listar_productos'))

# ==============================================================================
# SECCIÓN GESTIÓN DE CATEGORÍAS (NOMENCLATURAS)
# ==============================================================================

@inventario_bp.route('/categorias', methods=['GET', 'POST'])
@login_required
def gestionar_categorias():
    """Muestra e inserta categorías (Nomenclaturas) en una sola vista distribuida."""
    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        
        if CategoriaProducto.query.filter_by(nombre=nombre).first():
            flash('Error: Ya existe una categoría con ese nombre.', 'danger')
        else:
            nueva_cat = CategoriaProducto(nombre=nombre, activo=True)
            db.session.add(nueva_cat)
            db.session.commit()
            registrar_log_sistema("Creación Categoría", f"Se creó la categoría: {nombre}")
            flash('Nueva categoría registrada con éxito.', 'success')
            return redirect(url_for('inventario.gestionar_categorias'))

    todas_categorias = CategoriaProducto.query.order_by(CategoriaProducto.nombre).all()
    return render_template('inventario/categorias.html', categorias=todas_categorias)

@inventario_bp.route('/categorias/toggle/<int:id>', methods=['POST'])
@login_required
def toggle_categoria(id):
    """Desactiva o activa nomenclaturas."""
    categoria = CategoriaProducto.query.get_or_404(id)
    categoria.activo = not categoria.activo
    db.session.commit()
    
    estado = "activada" if categoria.activo else "desactivada"
    registrar_log_sistema("Cambio Estado Categoría", f"La categoría ID {id} fue {estado}.")
    flash(f"Categoría {categoria.nombre} {estado} con éxito.", "success")
    return redirect(url_for('inventario.gestionar_categorias'))