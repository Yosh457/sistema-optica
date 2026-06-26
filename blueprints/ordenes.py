# blueprints/ordenes.py
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file
from flask_login import login_required, current_user
from sqlalchemy import or_
import io

# Modelos e Inclusiones
from models import db, OrdenTrabajo, DetalleOrden, Paciente, RecetaOftalmica, Producto
from utils import registrar_log_sistema

# Configuración ReportLab para el PDF Institucional
from reportlab.lib.pagesizes import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

ordenes_bp = Blueprint('ordenes', __name__, template_folder='../templates', url_prefix='/ordenes')

@ordenes_bp.route('/listar')
@login_required
def listar_ordenes():
    """Muestra el historial de órdenes generadas con filtros por estado y RUT."""
    page = request.args.get('page', 1, type=int)
    busqueda = request.args.get('busqueda', '').strip()
    estado_filtro = request.args.get('estado', '')

    query = OrdenTrabajo.query.join(Paciente)

    if busqueda:
        query = query.filter(
            or_(Paciente.rut.ilike(f'%{busqueda}%'),
                Paciente.nombre_completo.ilike(f'%{busqueda}%'),
                OrdenTrabajo.id == busqueda)
        )
    
    if estado_filtro:
        query = query.filter(OrdenTrabajo.estado == estado_filtro)

    pagination = query.order_by(OrdenTrabajo.fecha_creacion.desc()).paginate(page=page, per_page=10, error_out=False)

    return render_template('ordenes/listar.html', pagination=pagination, busqueda=busqueda, estado_filtro=estado_filtro)


@ordenes_bp.route('/crear/buscar-paciente')
@login_required
def buscar_paciente():
    """Paso 1 del flujo: Buscar paciente y desplegar sus recetas clínicas."""
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
    """Paso 2 del flujo: Reúne al paciente, su receta y procesa la baja de stock."""
    paciente_id = request.args.get('paciente_id', type=int)
    receta_id = request.args.get('receta_id', type=int) # Puede ser None (venta directa)

    if not paciente_id:
        flash('Debe seleccionar un paciente para continuar.', 'danger')
        return redirect(url_for('ordenes.buscar_paciente'))

    paciente = Paciente.query.get_or_404(paciente_id)
    receta = RecetaOftalmica.query.get(receta_id) if receta_id else None
    productos_disponibles = Producto.query.filter_by(activo=True).order_by(Producto.descripcion).all()

    if request.method == 'POST':
        # Captura de listas enviadas desde el formulario dinámico
        productos_ids = request.form.getlist('producto_id[]')
        cantidades = request.form.getlist('cantidad[]')
        observaciones = request.form.get('observaciones', '').strip()

        if not productos_ids or len(productos_ids) == 0:
            flash('Error: Debe añadir al menos un artículo a la orden.', 'danger')
            return redirect(url_for('ordenes.formulario_orden', paciente_id=paciente_id, receta_id=receta_id))

        total_orden = 0
        detalles_a_guardar = []

        # --- VALIDACIÓN DE STOCK EN MEMORIA ANTES DE GUARDAR ---
        for p_id, cant_str in zip(productos_ids, cantidades):
            if not p_id or not cant_str: continue
            
            prod = Producto.query.get(int(p_id))
            cantidad = int(cant_str)

            if cantidad <= 0:
                flash(f'Error: La cantidad para {prod.descripcion} debe ser mayor a 0.', 'danger')
                return redirect(url_for('ordenes.formulario_orden', paciente_id=paciente_id, receta_id=receta_id))

            if prod.stock < cantidad:
                flash(f'⚠️ STOCK INSUFICIENTE: Solo quedan {prod.stock} unidades de "{prod.descripcion}". Operación cancelada.', 'danger')
                return redirect(url_for('ordenes.formulario_orden', paciente_id=paciente_id, receta_id=receta_id))

            subtotal = prod.precio * cantidad
            total_orden += subtotal

            # Decrepamos el stock físicamente
            prod.stock -= cantidad

            # Preparamos el registro del detalle
            detalle = DetalleOrden(
                producto_id=prod.id,
                cantidad=cantidad,
                precio_unitario=prod.precio,
                subtotal=subtotal
            )
            detalles_a_guardar.append(detalle)

        # Creación de la cabecera de la Orden
        nueva_orden = OrdenTrabajo(
            total=total_orden,
            estado='Pendiente',
            observaciones=observaciones,
            usuario_id=current_user.id,
            paciente_id=paciente.id,
            receta_id=receta.id if receta else None
        )

        for d in detalles_a_guardar:
            nueva_orden.detalles.append(d)

        try:
            db.session.add(nueva_orden)
            db.session.commit()
            registrar_log_sistema("Generación Orden", f"Orden ID {nueva_orden.id} creada para paciente RUT {paciente.rut}. Total: ${total_orden}")
            flash(f'Orden de Trabajo Nº {nueva_orden.id} generada con éxito.', 'success')
            return redirect(url_for('ordenes.listar_ordenes'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error crítico al procesar la orden: {str(e)}', 'danger')

    return render_template('ordenes/crear_orden.html', paciente=paciente, receta=receta, productos=productos_disponibles)


@ordenes_bp.route('/cambiar-estado/<int:id>', methods=['POST'])
@login_required
def cambiar_estado(id):
    """Permite cambiar el estado (Entregado / Anulado). Si se anula, se devuelve el stock."""
    orden = OrdenTrabajo.query.get_or_404(id)
    nuevo_estado = request.form.get('nuevo_estado')

    if nuevo_estado == 'Anulado' and orden.estado != 'Anulado':
        # Devolución de stock al inventario
        for detalle in orden.detalles:
            detalle.producto.stock += detalle.cantidad
        orden.estado = 'Anulado'
        flash(f'Orden Nº {id} anulada correctamente. Stock devuelto a bodega.', 'info')
    elif nuevo_estado == 'Entregado':
        orden.estado = 'Entregado'
        flash(f'Orden Nº {id} marcada como entregada al paciente.', 'success')

    db.session.commit()
    registrar_log_sistema("Cambio Estado Orden", f"Orden ID {id} pasó a estado: {nuevo_estado}")
    return redirect(url_for('ordenes.listar_ordenes'))


@ordenes_bp.route('/pdf/<int:id>')
@login_required
def generar_pdf(id):
    """
    Genera el ticket de la orden de trabajo optimizado para impresoras térmicas de 80mm.
    Implementa auto-wrap nativo para descripciones largas y divisores vectoriales limpios.
    """
    orden = OrdenTrabajo.query.get_or_404(id)
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
        Paragraph('<b>Descripción del Insumo</b>', cell_left_b), 
        Paragraph('<b>Total</b>', cell_right_b)
    ]]
    
    for d in orden.detalles:
        articulos_data.append([
            Paragraph(str(d.cantidad), cell_center),
            Paragraph(d.producto.descripcion, cell_left), # ReportLab calculará el alto y saltos de línea automáticamente
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
    # 5. MONTO TOTAL
    # ==============================================================================
    total_str = f"${int(orden.total):,}".replace(",", ".")
    story.append(Paragraph(f"TOTAL: {total_str}", ParagraphStyle('TkTotal', fontName='Helvetica-Bold', fontSize=11, alignment=2, spaceBefore=2, spaceAfter=10)))
    
    # ==============================================================================
    # 6. PIE DE TICKET TERMINAL
    # ==============================================================================
    story.append(Paragraph("Documento interno de control de trabajo.", subtitle_style))
    story.append(Paragraph("Unidad de TICs - Departamento de Salud Municipal", ParagraphStyle('TkFoot', fontName='Helvetica', fontSize=5.5, alignment=1, textColor=colors.gray)))

    # Construcción final del flujo binario
    doc.build(story)
    buffer.seek(0)
    
    return send_file(buffer, as_attachment=False, mimetype='application/pdf', download_name=f"ticket_orden_{orden.id}.pdf")