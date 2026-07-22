# blueprints/ordenes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user
from sqlalchemy import or_
import io

# Modelos e Inclusiones
from models import db, OrdenTrabajo, DetalleOrden, Paciente, RecetaOftalmica, Producto, EstadoOrden, EstadoReceta, MetodoPago, HistorialEstado
from utils import registrar_log_sistema

# Configuración ReportLab para el PDF Institucional
from reportlab.lib.pagesizes import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

ordenes_bp = Blueprint('ordenes', __name__, template_folder='../templates', url_prefix='/ordenes')

# ==============================================================================
# CONSTANTES DE NEGOCIO (Evita IDs mágicos)
# ==============================================================================
ESTADO_OT_PENDIENTE = "Pendiente"
ESTADO_OT_ENTREGADA = "Entregada"
ESTADO_OT_ANULADA = "Anulada"

ESTADO_RECETA_LISTA = "Lista"
ESTADO_RECETA_ANULADA = "Anulada"

# ==============================================================================
# ENDPOINTS Y RUTAS DEL FLUJO
# ==============================================================================

@ordenes_bp.route('/listar')
@login_required
def listar_ordenes():
    """Muestra el historial de órdenes generadas con filtros dinámicos por estado parametrizado."""
    page = request.args.get('page', 1, type=int)
    busqueda = request.args.get('busqueda', '').strip()
    estado_filtro = request.args.get('estado_id', '')

    query = OrdenTrabajo.query.join(Paciente)

    if busqueda:
        query = query.filter(
            or_(Paciente.rut.ilike(f'%{busqueda}%'),
                Paciente.nombre_completo.ilike(f'%{busqueda}%'),
                OrdenTrabajo.id == busqueda)
        )
    
    if estado_filtro and estado_filtro.isdigit():
        query = query.filter(OrdenTrabajo.estado_id == int(estado_filtro))

    pagination = query.order_by(OrdenTrabajo.fecha_creacion.desc()).paginate(page=page, per_page=10, error_out=False)
    
    # Cargamos los estados comerciales para el filtro de la UI
    estados_ot = EstadoOrden.query.order_by(EstadoOrden.orden).all()
    # Cargamos los métodos de pago para los modales de entrega rápidos de la lista
    metodos_pago = MetodoPago.query.filter_by(activo=True).order_by(MetodoPago.nombre).all()

    return render_template('ordenes/listar.html', 
                           pagination=pagination, 
                           busqueda=busqueda, 
                           estados_ot=estados_ot,
                           metodos_pago=metodos_pago,
                           estado_filtro=estado_filtro)

@ordenes_bp.route('/crear/buscar-paciente')
@login_required
def buscar_paciente():
    """Paso 1 del flujo: Buscar paciente y desplegar sus recetas y estados."""
    busqueda = request.args.get('busqueda', '').strip()
    paciente = None
    recetas = []

    if busqueda:
        paciente = Paciente.query.filter((Paciente.rut == busqueda) | (Paciente.nombre_completo.ilike(f'%{busqueda}%'))).first()
        if paciente:
            recetas = RecetaOftalmica.query.filter_by(paciente_id=paciente.id).order_by(RecetaOftalmica.fecha_receta.desc()).all()
        else:
            flash('Paciente no encontrado en los registros clínicos.', 'warning')

    return render_template('ordenes/seleccionar_paciente.html', paciente=paciente, recetas=recetas, busqueda=busqueda)

@ordenes_bp.route('/crear/formulario', methods=['GET', 'POST'])
@login_required
def formulario_orden():
    """Paso 2 del flujo: Reúne al paciente, autocompleta la receta y genera la OT en estado Pendiente."""
    paciente_id = request.args.get('paciente_id', type=int)
    receta_id = request.args.get('receta_id', type=int) # Puede ser None (venta directa)

    if not paciente_id:
        flash('Debe seleccionar un paciente para continuar.', 'danger')
        return redirect(url_for('ordenes.buscar_paciente'))

    paciente = Paciente.query.get_or_404(paciente_id)
    receta = RecetaOftalmica.query.get(receta_id) if receta_id else None
    productos_disponibles = Producto.query.filter_by(activo=True).order_by(Producto.nombre).all()

    if request.method == 'POST':
        # Captura de listas enviadas desde el formulario dinámico
        productos_ids = request.form.getlist('producto_id[]')
        cantidades = request.form.getlist('cantidad[]')
        observaciones = request.form.get('observaciones', '').strip()

        if not productos_ids or len(productos_ids) == 0:
            flash('Error: Debe añadir al menos un artículo a la orden.', 'danger')
            return redirect(url_for('ordenes.formulario_orden', paciente_id=paciente_id, receta_id=receta_id))

        # Buscamos dinámicamente el estado inicial 'Pendiente' en la base de datos
        estado_pendiente = EstadoOrden.query.filter_by(nombre=ESTADO_OT_PENDIENTE).first()
        if not estado_pendiente:
            flash('Error de configuración: El estado comercial Pendiente no existe.', 'danger')
            return redirect(url_for('ordenes.buscar_paciente'))

        # Preparamos el registro de la orden
        total_orden = 0
        detalles_a_guardar = []

        # --- VALIDACIÓN DE STOCK EN MEMORIA ANTES DE GUARDAR ---
        # Estructuración y cálculo del carrito (Inmutable/Congelado)
        for p_id, cant_str in zip(productos_ids, cantidades):
            if not p_id or not cant_str: continue
            
            prod = Producto.query.get(int(p_id))
            cantidad = int(cant_str)

            if cantidad <= 0:
                flash(f'Error: La cantidad para {prod.nombre} debe ser mayor a 0.', 'danger')
                return redirect(url_for('ordenes.formulario_orden', paciente_id=paciente_id, receta_id=receta_id))

            subtotal = prod.precio * cantidad
            total_orden += subtotal

            # Preparamos el registro del detalle
            detalle = DetalleOrden(
                producto_id=prod.id,
                cantidad=cantidad,
                precio_unitario=prod.precio,
                subtotal=subtotal
            )
            detalles_a_guardar.append(detalle)

        # Creación de la cabecera (Nace como Pendiente, NO descuenta stock aún)
        nueva_orden = OrdenTrabajo(
            total=total_orden,
            estado_id=estado_pendiente.id,
            observaciones=observaciones,
            usuario_id=current_user.id,
            paciente_id=paciente.id,
            receta_id=receta.id if receta else None
        )

        for d in detalles_a_guardar:
            nueva_orden.detalles.append(d)

        try:
            db.session.add(nueva_orden)
            db.session.flush() # Forzamos obtención del ID comercial de la OT

            # Auditoría en historial_estados
            historial = HistorialEstado(
                tipo_entidad=HistorialEstado.TIPO_ORDEN,
                entidad_id=nueva_orden.id,
                estado_anterior_id=None,
                estado_nuevo_id=estado_pendiente.id,
                usuario_id=current_user.id,
                observacion="Orden de Trabajo pre-generada y documentada en espera del paciente."
            )
            db.session.add(historial)
            
            db.session.commit()
            registrar_log_sistema("Generación Orden", f"OT Nº {nueva_orden.id} creada para paciente RUT {paciente.rut} en estado Pendiente.")
            flash(f'Orden de Trabajo Nº {nueva_orden.id} registrada exitosamente.', 'success')
            return redirect(url_for('ordenes.listar_ordenes'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error crítico al procesar la orden: {str(e)}', 'danger')

    return render_template('ordenes/crear_orden.html', paciente=paciente, receta=receta, productos=productos_disponibles)

@ordenes_bp.route('/entregar/<int:id>', methods=['POST'])
@login_required
def entregar_orden(id):
    """
    ÁREA DE CAJA: Registra el pago del paciente, descuenta el stock 
    físicamente del inventario y realiza la entrega formal del lente.
    ADEMÁS: Cierra el estado de la Receta asociada para limpiar el Kanban.
    """
    orden = OrdenTrabajo.query.get_or_404(id)
    metodo_pago_id = request.form.get('metodo_pago_id')
    observacion = request.form.get('observacion', '').strip()

    estado_pendiente = EstadoOrden.query.filter_by(nombre=ESTADO_OT_PENDIENTE).first()
    estado_entregada = EstadoOrden.query.filter_by(nombre=ESTADO_OT_ENTREGADA).first()

    if orden.estado_id != estado_pendiente.id:
        flash('Error: Solo se pueden entregar órdenes que se encuentren en estado Pendiente.', 'danger')
        return redirect(url_for('ordenes.listar_ordenes'))

    if not metodo_pago_id:
        flash('Error: Debe seleccionar un método de pago válido.', 'danger')
        return redirect(url_for('ordenes.listar_ordenes'))

    # --- VALIDACIÓN DE STOCK EN TIEMPO REAL ANTES DE LA REBAJA DE BODEGA ---
    for d in orden.detalles:
        if d.producto.stock < d.cantidad:
            flash(f'⚠️ IMPOSIBLE ENTREGAR: Stock insuficiente de "{d.producto.nombre}" en bodega (Disponible: {d.producto.stock}). Compras pendientes.', 'danger')
            return redirect(url_for('ordenes.listar_ordenes'))

    # Descuento físico de inventario de óptica
    for d in orden.detalles:
        d.producto.stock -= d.cantidad

    # Transición de estados comerciales y financieros de la OT
    estado_anterior = orden.estado_id
    orden.estado_id = estado_entregada.id
    orden.metodo_pago_id = int(metodo_pago_id)
    orden.modificado_por = current_user.id
    orden.fecha_modificacion = db.func.now()

    # Registro de auditoría polimórfica (Orden)
    historial_ot = HistorialEstado(
        tipo_entidad=HistorialEstado.TIPO_ORDEN,
        entidad_id=orden.id,
        estado_anterior_id=estado_anterior,
        estado_nuevo_id=estado_entregada.id,
        usuario_id=current_user.id,
        observacion=observacion if observacion else "Pago recibido y entrega conforme de lentes realizada."
    )
    db.session.add(historial_ot)
    
    # --- NUEVO: CERRAR LA RECETA CLÍNICA ASOCIADA PARA LIMPIAR EL KANBAN ---
    if orden.receta_id:
        receta = orden.receta
        estado_receta_lista = EstadoReceta.query.filter_by(nombre=ESTADO_RECETA_LISTA).first()
        
        # Como las recetas no tienen un estado "Entregada", simplemente las sacaremos de la vista (haciéndolas inactivas)
        # o las dejaremos así pero indicando que ya están finalizadas. Para esto, en vez de un estado nuevo,
        # simplemente la marcamos como no activa.
        receta.activa = False
        receta.modificado_por = current_user.id
        receta.fecha_modificacion = db.func.now()

        historial_receta = HistorialEstado(
            tipo_entidad=HistorialEstado.TIPO_RECETA,
            entidad_id=receta.id,
            estado_anterior_id=receta.estado_id,
            estado_nuevo_id=receta.estado_id, # El estado es el mismo (Lista) pero deja de estar activa
            usuario_id=current_user.id,
            observacion=f"Receta cerrada y entregada al paciente mediante Orden de Trabajo #{orden.id}."
        )
        db.session.add(historial_receta)

    try:
        db.session.commit()
        registrar_log_sistema("Entrega Orden", f"OT Nº {orden.id} entregada y pagada. Inventario rebajado.")
        flash(f'¡Éxito! Orden Nº {orden.id} marcada como Entregada. Ticket de caja habilitado para impresión.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al procesar la entrega en la base de datos: {str(e)}', 'danger')

    return redirect(url_for('ordenes.listar_ordenes'))

@ordenes_bp.route('/anular/<int:id>', methods=['POST'])
@login_required
def anular_orden(id):
    """Permite anular una orden. Devuelve stock a bodega SOLO si la orden ya había sido entregada."""
    orden = OrdenTrabajo.query.get_or_404(id)
    observacion = request.form.get('observacion', '').strip()

    estado_entregada = EstadoOrden.query.filter_by(nombre=ESTADO_OT_ENTREGADA).first()
    estado_anulada = EstadoOrden.query.filter_by(nombre=ESTADO_OT_ANULADA).first()
    estado_receta_anulada = EstadoReceta.query.filter_by(nombre=ESTADO_RECETA_ANULADA).first()

    if orden.estado_id == estado_anulada.id:
        flash('La orden ya se encuentra anulada.', 'warning')
        return redirect(url_for('ordenes.listar_ordenes'))

    # REGLA DE NEGOCIO: Si ya se había entregado, devolvemos el stock. Si era pendiente, la bodega está intacta.
    if orden.estado_id == estado_entregada.id:
        for d in orden.detalles:
            d.producto.stock += d.cantidad

    estado_anterior = orden.estado_id
    orden.estado_id = estado_anulada.id
    orden.modificado_por = current_user.id
    orden.fecha_modificacion = db.func.now()

    historial_ot = HistorialEstado(
        tipo_entidad=HistorialEstado.TIPO_ORDEN,
        entidad_id=orden.id,
        estado_anterior_id=estado_anterior,
        estado_nuevo_id=estado_anulada.id,
        usuario_id=current_user.id,
        observacion=f"ANULACIÓN: {observacion}" if observacion else "Orden anulada por el funcionario."
    )
    db.session.add(historial_ot)
    
    # --- NUEVO: SI ANULAMOS LA OT, TAMBIÉN ANULAMOS LA RECETA PARA QUE NO ESTORBE ---
    if orden.receta_id and estado_receta_anulada:
        receta = orden.receta
        estado_anterior_receta = receta.estado_id
        
        receta.estado_id = estado_receta_anulada.id
        receta.activa = False
        receta.modificado_por = current_user.id
        receta.fecha_modificacion = db.func.now()
        
        historial_receta = HistorialEstado(
            tipo_entidad=HistorialEstado.TIPO_RECETA,
            entidad_id=receta.id,
            estado_anterior_id=estado_anterior_receta,
            estado_nuevo_id=estado_receta_anulada.id,
            usuario_id=current_user.id,
            observacion=f"Anulada automáticamente al anular la OT #{orden.id}."
        )
        db.session.add(historial_receta)

    try:
        db.session.commit()
        registrar_log_sistema("Anulación Orden", f"OT Nº {orden.id} fue anulada. Historial actualizado.")
        flash(f'Orden de Trabajo Nº {orden.id} anulada con éxito.', 'info')
    except Exception as e:
        db.session.rollback()
        flash(f'Error al procesar la anulación: {str(e)}', 'danger')

    return redirect(url_for('ordenes.listar_ordenes'))

@ordenes_bp.route('/pdf/<int:id>')
@login_required
def generar_pdf(id):
    """
    Genera el ticket de la orden de trabajo optimizado para impresoras térmicas de 80mm.
    Implementa auto-wrap nativo para descripciones largas y divisores vectoriales limpios.
    """
    orden = OrdenTrabajo.query.get_or_404(id)
    
    estado_entregada = EstadoOrden.query.filter_by(nombre=ESTADO_OT_ENTREGADA).first()
    
    # REGLA DE SEGURIDAD ARQUITECTÓNICA: Bloquear la impresión si no está pagada/entregada
    if not estado_entregada or orden.estado_id != estado_entregada.id:
        flash('⚠️ SEGURIDAD: No se puede emitir ni imprimir el comprobante de una Orden de Trabajo que no esté en estado Entregada.', 'danger')
        return redirect(url_for('ordenes.listar_ordenes'))
    
    # --- CREACIÓN DEL LIENZO TÉRMICO ---
    buffer = io.BytesIO()
    
    # --- CONFIGURACIÓN DEL LIENZO TÉRMICO (80mm ancho) ---
    PAGE_WIDTH = 80 * mm
    PAGE_HEIGHT = 260 * mm # Alto de seguridad para el rollo continuo
    
    # Margen de 4mm a cada lado deja un ancho útil exacto de 72mm
    doc = SimpleDocTemplate(
        buffer, 
        pagesize=(PAGE_WIDTH, PAGE_HEIGHT), 
        rightMargin=4*mm, 
        leftMargin=4*mm, 
        topMargin=4*mm, 
        bottomMargin=4*mm
    )
    story = []
    
    # --- ESTILOS TIPOGRÁFICOS DE ALTA DENSIDAD ---
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle('TkTitle', fontName='Helvetica-Bold', fontSize=11, alignment=1, spaceAfter=2)
    subtitle_style = ParagraphStyle('TkSub', fontName='Helvetica', fontSize=8, alignment=1, spaceAfter=4)
    bold_sec_style = ParagraphStyle('TkSec', fontName='Helvetica-Bold', fontSize=8, leading=10, spaceBefore=4, spaceAfter=4)
    
    # Estilos para celdas dinámicas (Obligatorio 'leading' para evitar solapamientos en auto-wrap)
    cell_left = ParagraphStyle('CelLeft', fontName='Helvetica', fontSize=7.5, leading=9, alignment=0)
    cell_center = ParagraphStyle('CelCenter', fontName='Helvetica', fontSize=7.5, leading=9, alignment=1)
    cell_right = ParagraphStyle('CelRight', fontName='Helvetica', fontSize=7.5, leading=9, alignment=2)
    
    # Clones en negrita para encabezados de tablas
    cell_left_b = ParagraphStyle('CelLeftB', parent=cell_left, fontName='Helvetica-Bold')
    cell_center_b = ParagraphStyle('CelCenterB', parent=cell_center, fontName='Helvetica-Bold')
    cell_right_b = ParagraphStyle('CelRightB', parent=cell_right, fontName='Helvetica-Bold')

    # Helper interno para inyectar líneas divisorias vectoriales perfectas de 72mm
    def agregar_linea_divisoria():
        linea = Table([['']], colWidths=[72*mm])
        linea.setStyle(TableStyle([
            ('LINEBELOW', (0,0), (-1,-1), 0.6, colors.black),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 2),
        ]))
        story.append(linea)
        story.append(Spacer(1, 4))

    # ==============================================================================
    # 1. ENCABEZADO INSTITUCIONAL
    # ==============================================================================
    story.append(Paragraph("ÓPTICA MUNICIPAL", title_style))
    story.append(Paragraph("Municipalidad de Alto Hospicio", subtitle_style))
    story.append(Spacer(1, 2))
    story.append(Paragraph(f"ORDEN DE TRABAJO Nº {orden.id}", title_style))
    story.append(Paragraph(f"Fecha: {orden.fecha_creacion.strftime('%d-%m-%Y %H:%M')}", subtitle_style))
    agregar_linea_divisoria()
    
    # ==============================================================================
    # 2. INFORMACIÓN DEL PACIENTE
    # ==============================================================================
    story.append(Paragraph("DATOS DEL PACIENTE", bold_sec_style))
    story.append(Paragraph(f"<b>RUT:</b> {orden.paciente.rut}", cell_left))
    story.append(Paragraph(f"<b>Nombre:</b> {orden.paciente.nombre_completo}", cell_left))
    if orden.paciente.telefono:
        story.append(Paragraph(f"<b>Teléfono:</b> {orden.paciente.telefono}", cell_left))
    agregar_linea_divisoria()
    
    # ==============================================================================
    # 3. FICHA CLÍNICA (RECETA)
    # ==============================================================================
    if orden.receta:
        story.append(Paragraph("RECETA OFTALMOLÓGICA", bold_sec_style))
        
        receta_data = [
            [Paragraph('<b>OJO</b>', cell_center_b), Paragraph('<b>ESF</b>', cell_center_b), Paragraph('<b>CIL</b>', cell_center_b), Paragraph('<b>EJE</b>', cell_center_b)],
            [Paragraph('<b>OD</b>', cell_center_b), Paragraph(orden.receta.od_esfera or '0.00', cell_center), Paragraph(orden.receta.od_cilindro or '0.00', cell_center), Paragraph(orden.receta.od_eje or '0', cell_center)],
            [Paragraph('<b>OI</b>', cell_center_b), Paragraph(orden.receta.oi_esfera or '0.00', cell_center), Paragraph(orden.receta.oi_cilindro or '0.00', cell_center), Paragraph(orden.receta.oi_eje or '0', cell_center)]
        ]
        
        # Distribución exacta: 12mm + 20mm + 20mm + 20mm = 72mm de ancho útil
        t_receta = Table(receta_data, colWidths=[12*mm, 20*mm, 20*mm, 20*mm])
        t_receta.setStyle(TableStyle([
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('GRID', (0,0), (-1,-1), 0.5, colors.black),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('TOPPADDING', (0,0), (-1,-1), 3),
        ]))
        story.append(t_receta)
        
        story.append(Spacer(1, 4))
        dp_add = f"<b>DP:</b> {orden.receta.distancia_pupilar or '-'}  |  <b>ADD:</b> {orden.receta.adicion or '-'}"
        story.append(Paragraph(dp_add, cell_center))
        agregar_linea_divisoria()
    
    # ==============================================================================
    # 4. DETALLE DE PRODUCTOS (Con Auto-Wrap Nativo)
    # ==============================================================================
    story.append(Paragraph("DETALLE DE ARTÍCULOS", bold_sec_style))
    
    articulos_data = [[
        Paragraph('<b>Cant</b>', cell_center_b), 
        Paragraph('<b>Nombre del Insumo</b>', cell_left_b), 
        Paragraph('<b>Total</b>', cell_right_b)
    ]]
    
    for d in orden.detalles:
        articulos_data.append([
            Paragraph(str(d.cantidad), cell_center),
            Paragraph(d.producto.nombre, cell_left), # ReportLab calculará el alto y saltos de línea automáticamente
            Paragraph(f"${int(d.subtotal):,}".replace(",", "."), cell_right)
        ])
        
    # Distribución exacta: 9mm + 43mm + 20mm = 72mm de ancho útil
    t_articulos = Table(articulos_data, colWidths=[9*mm, 43*mm, 20*mm])
    t_articulos.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('LINEBELOW', (0,0), (-1,0), 0.5, colors.black), # Línea bajo el encabezado de la tabla
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_articulos)
    agregar_linea_divisoria()
    
    # ==============================================================================
    # 5. MONTO TOTAL Y MÉTODO DE PAGO
    # ==============================================================================
    total_str = f"${int(orden.total):,}".replace(",", ".")
    story.append(Paragraph(f"TOTAL: {total_str}", ParagraphStyle('TkTotal', fontName='Helvetica-Bold', fontSize=11, alignment=2, spaceBefore=2)))
    if orden.metodo_pago:
        story.append(Paragraph(f"<b>PAGO:</b> {orden.metodo_pago.nombre}", ParagraphStyle('TkPago', fontName='Helvetica', fontSize=7.5, alignment=2, spaceAfter=10)))
    
    # ==============================================================================
    # 6. PIE DE TICKET TERMINAL
    # ==============================================================================
    story.append(Paragraph("Documento interno de control de caja.", subtitle_style))
    story.append(Paragraph("Unidad de TICs - Departamento de Salud Municipal", ParagraphStyle('TkFoot', fontName='Helvetica', fontSize=5.5, alignment=1, textColor=colors.gray)))

    # Construcción final del flujo binario
    doc.build(story)
    buffer.seek(0)
    
    return send_file(buffer, as_attachment=False, mimetype='application/pdf', download_name=f"ticket_caja_orden_{orden.id}.pdf")