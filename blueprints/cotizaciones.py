# blueprints/cotizaciones.py
import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, send_from_directory, current_app
from flask_login import login_required, current_user
from sqlalchemy import or_
from datetime import timedelta

# Importaciones para generar PDF
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from models import db, Cotizacion, DetalleCotizacion, EstadoCotizacion, ConfiguracionSistema, Producto, Paciente, obtener_hora_chile

# Definimos el blueprint y su carpeta de plantillas
cotizaciones_bp = Blueprint('cotizaciones', __name__, template_folder='../templates', url_prefix='/cotizaciones')

@cotizaciones_bp.route('/')
@login_required
def listado():
    """Muestra el directorio general de cotizaciones del sistema."""
    page = request.args.get('page', 1, type=int)
    busqueda = request.args.get('busqueda', '').strip()

    # Hacemos un JOIN con Paciente para poder buscar por RUT o Nombre
    query = Cotizacion.query.join(Paciente)

    if busqueda:
        query = query.filter(
            or_(
                Paciente.rut.ilike(f'%{busqueda}%'),
                Paciente.nombre_completo.ilike(f'%{busqueda}%')
            )
        )

    # Paginamos ordenando por la más reciente primero
    pagination = query.order_by(Cotizacion.fecha_emision.desc()).paginate(page=page, per_page=10, error_out=False)

    # Calculamos estadísticas rápidas para los KPIs
    todas_las_cotizaciones = Cotizacion.query.all()
    stats = {
        'total': len(todas_las_cotizaciones),
        'vigentes': sum(1 for c in todas_las_cotizaciones if c.estado_logico == 'Vigente'),
        'vencidas': sum(1 for c in todas_las_cotizaciones if c.estado_logico == 'Vencida'),
        'convertidas': sum(1 for c in todas_las_cotizaciones if c.estado.nombre == 'Convertida en Venta')
    }

    return render_template('cotizaciones/listado.html', pagination=pagination, busqueda=busqueda, stats=stats)

@cotizaciones_bp.route('/crear', methods=['GET', 'POST'])
@login_required
def crear():
    """Maneja el carrito dinámico y guarda la nueva cotización en la BD."""
    if request.method == 'POST':
        paciente_id = request.form.get('paciente_id')
        observaciones = request.form.get('observaciones', '').strip()
        total_raw = request.form.get('total', 0)

        # 1. Validaciones iniciales
        if not paciente_id:
            flash('Error: Debe seleccionar un paciente válido.', 'danger')
            return redirect(url_for('cotizaciones.crear'))

        # Obtener el estado "Vigente"
        estado_vigente = EstadoCotizacion.query.filter_by(nombre='Vigente').first()
        if not estado_vigente:
            flash('Error crítico de configuración: No existe el estado "Vigente".', 'danger')
            return redirect(url_for('cotizaciones.crear'))

        # 2. Calcular la fecha de vencimiento automáticamente
        config_dias = ConfiguracionSistema.query.filter_by(clave='dias_vigencia_cotizacion').first()
        dias_vigencia = int(config_dias.valor) if config_dias else 15
        
        fecha_emision = obtener_hora_chile()
        fecha_vencimiento = fecha_emision + timedelta(days=dias_vigencia)

        try:
            # 3. Crear la Cabecera de la Cotización
            nueva_cotizacion = Cotizacion(
                fecha_emision=fecha_emision,
                fecha_vencimiento=fecha_vencimiento,
                paciente_id=paciente_id,
                observaciones=observaciones,
                total=float(total_raw),
                estado_id=estado_vigente.id,
                usuario_id=current_user.id
            )
            db.session.add(nueva_cotizacion)
            db.session.flush() # Flush nos da el ID de la cotización antes de hacer el commit final

            # 4. Extraer y procesar los productos del carrito desde el formulario
            # El formulario envía nombres como: name="productos[0][id]"
            productos_data = {}
            for key, value in request.form.items():
                if key.startswith('productos['):
                    # Limpiamos los corchetes para extraer el índice y el campo
                    parts = key.replace(']', '').split('[')
                    if len(parts) == 3:
                        idx = parts[1] # Ej: '0'
                        campo = parts[2] # Ej: 'id', 'cantidad', 'precio'
                        if idx not in productos_data:
                            productos_data[idx] = {}
                        productos_data[idx][campo] = value

            if not productos_data:
                flash('Error: La cotización debe tener al menos un producto.', 'danger')
                db.session.rollback()
                return redirect(url_for('cotizaciones.crear'))

            # 5. Crear el Detalle de la Cotización
            for idx, data in productos_data.items():
                prod_id = int(data.get('id', 0))
                cantidad = int(data.get('cantidad', 1))
                precio = float(data.get('precio', 0))
                subtotal = cantidad * precio

                detalle = DetalleCotizacion(
                    cotizacion_id=nueva_cotizacion.id,
                    producto_id=prod_id,
                    cantidad=cantidad,
                    precio_unitario=precio,
                    subtotal=subtotal
                )
                db.session.add(detalle)

            # 6. Confirmar todo en la base de datos
            db.session.commit()
            flash(f'Cotización {nueva_cotizacion.numero_formateado} guardada exitosamente.', 'success')

            # 7. Redirigir al listado, enviando una señal para que se abra el PDF en nueva pestaña
            return redirect(url_for('cotizaciones.listado', open_pdf=nueva_cotizacion.id))

        except Exception as e:
            db.session.rollback()
            flash(f'Error de base de datos al guardar: {str(e)}', 'danger')

    # Extraemos todos los productos activos para llenar el selector del frontend (Método GET)
    productos_activos = Producto.query.filter_by(activo=True).order_by(Producto.nombre).all()
    
    return render_template('cotizaciones/crear_cotizacion.html', productos=productos_activos)

# --- NUEVA RUTA: GENERACIÓN DEL PDF ---
@cotizaciones_bp.route('/<int:id>/pdf')
@login_required
def generar_pdf(id):
    """Genera el PDF de la cotización, lo guarda físicamente en disco y lo muestra."""
    cotizacion = Cotizacion.query.get_or_404(id)
    
    # 1. Definimos el nombre del archivo y la ruta física segura
    nombre_archivo = f"{cotizacion.numero_formateado}.pdf"
    directorio_uploads = os.path.join(current_app.root_path, 'uploads', 'cotizaciones')
    
    # Nos aseguramos de que la carpeta exista
    os.makedirs(directorio_uploads, exist_ok=True)
    
    ruta_completa = os.path.join(directorio_uploads, nombre_archivo)
    
    # 2. Comprobamos si el PDF ya fue generado y guardado previamente
    if not os.path.exists(ruta_completa):
        # Si no existe, configuramos el documento apuntando a la RUTA FÍSICA en lugar de la memoria RAM
        doc = SimpleDocTemplate(ruta_completa, pagesize=letter, rightMargin=40, leftMargin=40, topMargin=40, bottomMargin=40)
        elementos = []
    
        # Estilos
        styles = getSampleStyleSheet()
        estilo_titulo = styles['Heading1']
        estilo_titulo.alignment = 1 # Centrado
        estilo_normal = styles['Normal']
        
        # 1. Encabezado del Documento
        elementos.append(Paragraph("<b>ÓPTICA MUNICIPAL DE ALTO HOSPICIO</b>", estilo_titulo))
        elementos.append(Spacer(1, 10))
        elementos.append(Paragraph(f"<b>Documento:</b> Cotización {cotizacion.numero_formateado}", estilo_normal))
        elementos.append(Paragraph(f"<b>Fecha Emisión:</b> {cotizacion.fecha_emision.strftime('%d-%m-%Y %H:%M')}", estilo_normal))
        elementos.append(Paragraph(f"<b>Válida hasta:</b> {cotizacion.fecha_vencimiento.strftime('%d-%m-%Y')} (Estado: {cotizacion.estado_logico})", estilo_normal))
        elementos.append(Spacer(1, 20))
    
        # 2. Datos del Paciente
        elementos.append(Paragraph("<b>DATOS DEL PACIENTE</b>", styles['Heading3']))
        datos_paciente = [
            [Paragraph("<b>Nombre:</b>", estilo_normal), Paragraph(cotizacion.paciente.nombre_completo, estilo_normal)],
            [Paragraph("<b>RUT:</b>", estilo_normal), Paragraph(cotizacion.paciente.rut, estilo_normal)],
            [Paragraph("<b>Teléfono:</b>", estilo_normal), Paragraph(cotizacion.paciente.telefono, estilo_normal)],
            [Paragraph("<b>Dirección:</b>", estilo_normal), Paragraph(cotizacion.paciente.direccion, estilo_normal)]
        ]
        tabla_paciente = Table(datos_paciente, colWidths=[80, 400])
        elementos.append(tabla_paciente)
        elementos.append(Spacer(1, 20))
        
        # 3. Detalle de Productos
        elementos.append(Paragraph("<b>DETALLE DE PRODUCTOS</b>", styles['Heading3']))
    
        # Cabeceras de la tabla
        datos_tabla = [["Cant.", "Descripción", "Precio Unit.", "Subtotal"]]
    
        # Filas dinámicas
        for det in cotizacion.detalles:
            datos_tabla.append([
                str(det.cantidad),
                det.producto.nombre,
                f"${det.precio_unitario:,.0f}".replace(",", "."),
                f"${det.subtotal:,.0f}".replace(",", ".")
            ])
        
        # Fila del Total
        datos_tabla.append(["", "", "TOTAL:", f"${cotizacion.total:,.0f}".replace(",", ".")])
        
        # Diseño de la tabla
        tabla_detalle = Table(datos_tabla, colWidths=[40, 280, 100, 100])
        estilo_tabla = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#275c80')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (1, 1), (1, -1), 'LEFT'), # Descripción a la izquierda
            ('ALIGN', (2, 1), (-1, -1), 'RIGHT'), # Precios a la derecha
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
            ('BACKGROUND', (0, 1), (-1, -2), colors.HexColor('#f9fafb')),
            ('GRID', (0, 0), (-1, -2), 1, colors.lightgrey),
            ('FONTNAME', (2, -1), (-1, -1), 'Helvetica-Bold'), # Fila de Total en negrita
            ('LINEABOVE', (2, -1), (-1, -1), 2, colors.HexColor('#275c80')),
        ])
        tabla_detalle.setStyle(estilo_tabla)
        elementos.append(tabla_detalle)
        elementos.append(Spacer(1, 20))
    
        # 4. Observaciones y Notas Legales
        if cotizacion.observaciones:
            elementos.append(Paragraph("<b>Observaciones Adicionales:</b>", styles['Heading4']))
            elementos.append(Paragraph(cotizacion.observaciones, estilo_normal))
            elementos.append(Spacer(1, 10))
        
        # Agregamos la nota fija exigida sobre la validez
        config_dias = ConfiguracionSistema.query.filter_by(clave='dias_vigencia_cotizacion').first()
        dias = config_dias.valor if config_dias else "15"
        nota_legal = f"<b>Nota:</b> Los precios y el stock detallados en este documento tienen una validez de {dias} días corridos desde su emisión. Documento no válido como boleta ni garantía de stock final sin pago."
        elementos.append(Paragraph(nota_legal, estilo_normal))
        
        # Al ejecutar build() con una ruta de archivo, lo guarda directamente en el disco duro
        doc.build(elementos)
    
    # 3. Le enviamos al navegador el archivo físico que ahora vive en el servidor
    return send_from_directory(
        directorio_uploads, 
        nombre_archivo, 
        as_attachment=False # False para que lo abra en el navegador en vez de forzar descarga
    )