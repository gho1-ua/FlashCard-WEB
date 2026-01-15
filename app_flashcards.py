import streamlit as st
import fitz  # PyMuPDF
import re
from typing import List, Dict, Optional
import io

# Configuración de la página
st.set_page_config(
    page_title="Simulador de Exámenes",
    page_icon="📚",
    layout="wide"
)

# Inicializar Session State
if 'preguntas' not in st.session_state:
    st.session_state.preguntas = []
if 'pregunta_actual' not in st.session_state:
    st.session_state.pregunta_actual = 0
if 'respuestas_usuario' not in st.session_state:
    st.session_state.respuestas_usuario = {}
if 'verificaciones' not in st.session_state:
    st.session_state.verificaciones = {}
if 'pdf_cargado' not in st.session_state:
    st.session_state.pdf_cargado = False
if 'modo_revision' not in st.session_state:
    st.session_state.modo_revision = True  # Inicia en modo revisión
if 'revision_completada' not in st.session_state:
    st.session_state.revision_completada = False
if 'subrayado_detectado' not in st.session_state:
    st.session_state.subrayado_detectado = {}  # Dict para rastrear qué preguntas tienen subrayado


def es_ruido_pagina(texto: str) -> bool:
    """
    Detecta si una línea es ruido de página (header/footer) que debe ignorarse.
    """
    if not texto:
        return True
    
    texto_upper = texto.upper()
    patrones_ruido = [
        "PAG.",
        "PÁGINA",
        "DESCARGADO POR",
        "PROFESORAS:",
        "PROFESORES:",
        "DEPARTAMENTO DE",
        "DIRECCIÓN COMERCIAL",
        "SISTEMA DE PUNTUACIÓN",
        "□",  # Símbolo de cuadro
    ]
    
    for patron in patrones_ruido:
        if patron in texto_upper:
            return True
    
    return False


def detectar_subrayado_resaltado(span: dict) -> bool:
    """
    Detecta si un span está subrayado o resaltado (incluyendo colores de fondo como verde).
    Busca específicamente underline (línea por debajo) y resaltado (fondo de color).
    Retorna True si está marcado de alguna forma.
    """
    flags = span.get("flags", 0)
    
    # Detectar underline (flag 4 y 8388608) - línea por debajo del texto
    is_underlined = (flags & 4) != 0 or (flags & 8388608) != 0
    
    # Buscar atributo s_line o underline explícito
    if "s_line" in span or "underline" in span:
        is_underlined = True
    
    # Detectar resaltado por color de fondo
    back_color = span.get("back_color", None)
    is_highlighted = False
    
    if back_color is not None:
        try:
            if isinstance(back_color, (list, tuple)) and len(back_color) >= 3:
                r, g, b = float(back_color[0]), float(back_color[1]), float(back_color[2])
                # Verificar si NO es blanco (1,1,1) ni negro/transparente (0,0,0)
                if not (abs(r - 1.0) < 0.01 and abs(g - 1.0) < 0.01 and abs(b - 1.0) < 0.01):
                    if not (abs(r) < 0.01 and abs(g) < 0.01 and abs(b) < 0.01):
                        is_highlighted = True
            elif isinstance(back_color, (int, float)):
                # Formato entero: 16777215 es blanco (0xFFFFFF)
                if back_color != 16777215 and back_color != 0:
                    is_highlighted = True
        except (ValueError, TypeError):
            pass
    
    return is_underlined or is_highlighted


def limpiar_texto(texto: str) -> str:
    """
    Limpia espacios extra y normaliza el texto.
    Preserva espacios simples entre palabras, pero elimina múltiples espacios consecutivos.
    """
    if not texto:
        return ""
    # Reemplazar múltiples espacios/tabs/newlines por un solo espacio
    texto = re.sub(r'[\s\t\n\r]+', ' ', texto)
    # Eliminar espacios al inicio y final
    texto = texto.strip()
    return texto


def limpiar_etiqueta_opcion(texto: str) -> str:
    """
    Elimina la etiqueta de opción (a., b), A-, etc.) del inicio del texto.
    Ejemplo: "a. Texto de la opción" → "Texto de la opción"
    """
    if not texto:
        return ""
    # Eliminar patrón de letra seguida de punto, paréntesis o guion al inicio
    texto_limpio = re.sub(r'^\s*[a-eA-E][\.\)\-]\s*', '', texto)
    return texto_limpio.strip()


def detectar_vf_en_enunciado(enunciado: str) -> tuple[str, Optional[int]]:
    """
    Detecta si al final del enunciado hay una marca V/F (Verdadero/Falso).
    Busca patrones como "(V)", "- F", " V", etc.
    
    Retorna: (enunciado_limpio, respuesta_vf)
    - enunciado_limpio: El enunciado sin la marca V/F
    - respuesta_vf: 0 si es Verdadero (V), 1 si es Falso (F), None si no se encontró
    """
    if not enunciado:
        return enunciado, None
    
    # Patrón para detectar V o F al final: \s*[\(\-\s]*(V|F)[\)\s]*$
    patron_vf = re.compile(r'\s*[\(\-\s]*(V|F)[\)\s]*$', re.IGNORECASE)
    match = patron_vf.search(enunciado)
    
    if match:
        vf_encontrado = match.group(1).upper()
        # Eliminar la marca del enunciado
        enunciado_limpio = patron_vf.sub('', enunciado).strip()
        # Retornar respuesta: 0 = Verdadero (V), 1 = Falso (F)
        respuesta = 0 if vf_encontrado == 'V' else 1
        return enunciado_limpio, respuesta
    
    return enunciado, None


def tiene_patrones_opcion_en_texto(texto: str) -> bool:
    """
    Detecta si un texto contiene patrones de opciones (a., b), etc.).
    Útil para detectar posibles errores de clasificación.
    """
    if not texto:
        return False
    # Buscar patrones de opciones en cualquier parte del texto
    patron = re.compile(r'[a-eA-E][\.\)\-]\s+')
    return bool(patron.search(texto))


def extraer_spans_con_formato(page):
    """
    Extrae todos los spans de texto de una página con su información de formato.
    NO descarta textos cortos - conserva todo el texto que sea parte de preguntas/respuestas.
    Filtra ruido de página (headers/footers).
    Retorna una lista de diccionarios con: texto, subrayado/resaltado, posición Y, posición X
    Ordenados por posición Y (arriba a abajo) y luego por X (izquierda a derecha)
    """
    texto_dict = page.get_text("dict")
    spans_info = []
    
    for block in texto_dict.get("blocks", []):
        if "lines" in block:
            for line in block["lines"]:
                # Construir texto completo de la línea para verificar ruido
                texto_linea_completo = ""
                spans_linea = []
                
                for span in line.get("spans", []):
                    texto = span.get("text", "").strip()
                    if texto:
                        texto_linea_completo += texto + " "
                        bbox = span.get("bbox", [0, 0, 0, 0])
                        y_pos = bbox[1]
                        x_pos = bbox[0]
                        is_marked = detectar_subrayado_resaltado(span)
                        spans_linea.append({
                            'texto': texto,
                            'marcado': is_marked,
                            'y': y_pos,
                            'x': x_pos
                        })
                
                # Filtrar ruido de página (solo si toda la línea es ruido)
                texto_linea_completo = texto_linea_completo.strip()
                if texto_linea_completo and not es_ruido_pagina(texto_linea_completo):
                    # Agregar todos los spans de la línea (sin descartar textos cortos)
                    spans_info.extend(spans_linea)
    
    # Ordenar por Y (arriba a abajo) y luego por X (izquierda a derecha)
    spans_info.sort(key=lambda s: (s['y'], s['x']))
    
    return spans_info


def extraer_texto_con_subrayado(pdf_bytes: bytes):
    """
    Extrae preguntas y opciones del PDF con lógica de contenedores robusta.
    
    LÓGICA DE CONTENEDORES:
    - Pregunta: Empieza con patrón numérico, frase anclaje específica o texto nuevo tras cerrar pregunta anterior
    - Captura Total: Todo el texto siguiente pertenece a la pregunta hasta que aparezca opción "a)"
    - Opción: Una vez detectada "a)", todo el texto siguiente pertenece a esa opción hasta "b)", etc.
    - Límite estricto: Solo se aceptan 4 opciones (a, b, c, d). La opción "e" o posteriores se fusionan con "d"
    - Cierre automático: Después de la opción "d)", la pregunta se cierra automáticamente
    
    REGLAS:
    1. NO descarta textos cortos (eliminado límite de 10 caracteres)
    2. Filtra ruido de página (headers/footers)
    3. Elimina V/F de opciones y lo usa para marcar respuesta correcta
    4. Detecta subrayado específicamente (underline, no solo resaltado)
    5. Detección por frase anclaje: Frases específicas fuerzan creación de nueva pregunta
    
    Retorna: (lista de preguntas, diccionario con índices de preguntas que tienen subrayado detectado)
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    todas_las_preguntas = []
    subrayado_por_pregunta = {}
    
    # Patrones de detección
    patron_pregunta = re.compile(r'^\s*(\d+)[\.\-\s]')  # Número seguido de punto, guion o espacio
    patron_opcion = re.compile(r'^\s*([a-eA-E])[\.\)\-]\s*')  # Letra a-e seguida de punto, paréntesis o guion
    
    # Frase anclaje específica para forzar creación de pregunta
    FRASE_ANCLAJE = "Con relación al producto como instrumento del marketing-mix, se puede afirmar que:"
    
    # Estado actual de la pregunta que estamos procesando
    pregunta_actual = None
    opciones_actuales = []
    opciones_marcadas = []  # Lista de booleanos indicando si cada opción está marcada
    pregunta_idx = 0
    estado_actual = "enunciado"  # "enunciado" o "opciones"
    tiene_numero = False  # Indica si la pregunta actual tiene número
    pregunta_cerrada = False  # Indica si la pregunta ya está cerrada (después de opción d)
    
    # Procesar cada página
    for page_num in range(len(doc)):
        page = doc[page_num]
        spans_info = extraer_spans_con_formato(page)
        
        if not spans_info:
            continue
        
        # Agrupar spans por líneas visuales (misma Y aproximadamente)
        TOLERANCIA_Y = 5  # píxeles
        
        lineas_visuales = []
        if spans_info:
            linea_actual = []
            y_actual = spans_info[0]['y']
            
            for span in spans_info:
                y_pos = span['y']
                if abs(y_pos - y_actual) <= TOLERANCIA_Y:
                    linea_actual.append(span)
                else:
                    if linea_actual:
                        lineas_visuales.append(linea_actual)
                    linea_actual = [span]
                    y_actual = y_pos
            
            if linea_actual:
                lineas_visuales.append(linea_actual)
        
        # Procesar cada línea visual
        for linea_visual in lineas_visuales:
            # Ordenar spans dentro de la línea por X (izquierda a derecha)
            linea_visual.sort(key=lambda s: s['x'])
            
            # Unir todos los textos de la línea
            textos_linea = [span['texto'] for span in linea_visual]
            texto_completo = " ".join(textos_linea)
            texto_completo = texto_completo.strip()
            
            if not texto_completo:
                continue
            
            # Si alguna parte está marcada, toda la línea está marcada
            marcado_linea = any(span['marcado'] for span in linea_visual)
            
            # Verificar si es pregunta u opción
            es_pregunta = patron_pregunta.match(texto_completo)
            es_opcion = patron_opcion.match(texto_completo)
            
            # DETECCIÓN POR FRASE ANCLAJE: Si contiene la frase específica, forzar nueva pregunta
            contiene_frase_anclaje = FRASE_ANCLAJE.lower() in texto_completo.lower()
            
            # NO DESCARTAR TEXTOS CORTOS - Si es parte de una pregunta/respuesta iniciada, conservarlo siempre
            
            # Si la pregunta ya está cerrada (después de opción d), descartar texto o iniciar nueva pregunta
            if pregunta_cerrada:
                # Si es ruido, descartarlo
                if es_ruido_pagina(texto_completo):
                    continue
                # Si es nueva pregunta o frase anclaje, reiniciar
                if es_pregunta or contiene_frase_anclaje:
                    pregunta_cerrada = False
                    # Continuar con la lógica de nueva pregunta más abajo
                else:
                    # Texto después de pregunta cerrada que no es ruido ni nueva pregunta
                    # Tratarlo como inicio de nueva pregunta sin número
                    pregunta_cerrada = False
                    pregunta_actual = texto_completo
                    opciones_actuales = []
                    opciones_marcadas = []
                    estado_actual = "enunciado"
                    tiene_numero = False
                    continue
            
            # 1. IDENTIFICADOR DE PREGUNTA: Si empieza por número o contiene frase anclaje, crear nueva pregunta
            if es_pregunta or contiene_frase_anclaje:
                # Guardar pregunta anterior si existe (CLASIFICACIÓN FINAL)
                if pregunta_actual and not pregunta_cerrada:
                    if len(opciones_actuales) > 0:
                        # Pregunta con opciones → Opción Múltiple
                        # Limitar a máximo 4 opciones si hay más
                        if len(opciones_actuales) > 4:
                            # Fusionar opciones adicionales con la última (opción d)
                            texto_extra = " ".join(opciones_actuales[4:])
                            opciones_actuales[3] += " " + texto_extra
                            opciones_actuales = opciones_actuales[:4]
                            opciones_marcadas = opciones_marcadas[:4]
                        
                        respuesta_correcta = 0
                        tiene_subrayado = False
                        for idx, esta_marcada in enumerate(opciones_marcadas):
                            if esta_marcada:
                                respuesta_correcta = idx
                                tiene_subrayado = True
                                break
                        
                        # Limpiar etiquetas y V/F de todas las opciones
                        opciones_limpias = []
                        for op in opciones_actuales:
                            op_limpia = limpiar_etiqueta_opcion(limpiar_texto(op))
                            # Eliminar V/F al final de opciones (solo para opciones múltiples)
                            op_limpia = re.sub(r'\s*[\(\-\s]*(V|F)[\)\s]*$', '', op_limpia, flags=re.IGNORECASE).strip()
                            opciones_limpias.append(op_limpia)
                        
                        todas_las_preguntas.append({
                            'pregunta': limpiar_texto(pregunta_actual),
                            'opciones': opciones_limpias,
                            'correcta': respuesta_correcta,
                            'tipo': 'opcion_multiple',
                            'tiene_numero': tiene_numero
                        })
                        subrayado_por_pregunta[pregunta_idx] = tiene_subrayado
                        pregunta_idx += 1
                    else:
                        # Pregunta sin opciones → Verdadero/Falso
                        enunciado_limpio, respuesta_vf = detectar_vf_en_enunciado(limpiar_texto(pregunta_actual))
                        respuesta_correcta = respuesta_vf if respuesta_vf is not None else 0
                        vf_detectado_enunciado = respuesta_vf is not None
                        
                        todas_las_preguntas.append({
                            'pregunta': enunciado_limpio,
                            'opciones': [],
                            'correcta': respuesta_correcta,
                            'tipo': 'V/F',
                            'vf_detectado_enunciado': vf_detectado_enunciado,
                            'tiene_numero': tiene_numero
                        })
                        subrayado_por_pregunta[pregunta_idx] = False
                        pregunta_idx += 1
                
                # Nueva pregunta (resetear estado)
                pregunta_actual = texto_completo
                opciones_actuales = []
                opciones_marcadas = []
                estado_actual = "enunciado"
                tiene_numero = es_pregunta  # Solo tiene número si empieza con número
                pregunta_cerrada = False
                continue
            
            # 2. IDENTIFICADOR DE OPCIÓN: Si empieza por letra a-e, añadir a opciones
            if es_opcion:
                # Extraer la letra de la opción
                match_opcion = patron_opcion.match(texto_completo)
                if match_opcion:
                    letra_opcion = match_opcion.group(1).lower()
                    
                    # LÍMITE ESTRICTO DE 4 OPCIONES: Solo aceptar a, b, c, d
                    if letra_opcion == 'e' or (letra_opcion.isalpha() and ord(letra_opcion) > ord('d')):
                        # Si ya tenemos 4 opciones, fusionar con la última (opción d)
                        if len(opciones_actuales) >= 4:
                            # Fusionar el texto con la opción d (índice 3)
                            opcion_limpia = limpiar_etiqueta_opcion(texto_completo)
                            opciones_actuales[3] += " " + opcion_limpia
                            # Si está marcada, también marcar la opción d
                            if marcado_linea:
                                opciones_marcadas[3] = True
                            continue
                    
                    # Cambiar a estado "opciones" si aún estábamos en "enunciado"
                    if estado_actual == "enunciado":
                        estado_actual = "opciones"
                        # Si no había pregunta iniciada, crear una sin número
                        # Esto puede pasar si el PDF empieza directamente con opciones
                        if not pregunta_actual:
                            pregunta_actual = "[S/N]"
                            tiene_numero = False
                    
                    # Nueva opción - Limpiar etiqueta (a., b), etc.)
                    opcion_limpia = limpiar_etiqueta_opcion(texto_completo)
                    
                    # Detectar y limpiar V/F al final de la línea de la opción
                    # Buscar V/F al final del texto de la opción
                    vf_match = re.search(r'\s*[\(\-\s]*(V|F)[\)\s]*$', opcion_limpia, re.IGNORECASE)
                    respuesta_vf_opcion = None
                    if vf_match:
                        vf_encontrado = vf_match.group(1).upper()
                        opcion_limpia = opcion_limpia[:vf_match.start()].strip()
                        # Marcar respuesta correcta: 0=a, 1=b, 2=c, 3=d
                        # V corresponde a índice 0, F a índice 1 (pero esto es para V/F, no para opciones múltiples)
                        # En opciones múltiples, si encontramos V/F, lo ignoramos o lo usamos como marcador
                        # Por ahora, solo limpiamos el texto
                    
                    opciones_actuales.append(opcion_limpia)
                    opciones_marcadas.append(marcado_linea)
                    
                    # CIERRE DE PREGUNTA: Si es la opción "d", cerrar la pregunta
                    if letra_opcion == 'd' and len(opciones_actuales) == 4:
                        pregunta_cerrada = True
                        # Guardar pregunta inmediatamente después de detectar la opción d
                        respuesta_correcta = 0
                        tiene_subrayado = False
                        
                        # Primero verificar si hay V/F al final de alguna opción para marcar respuesta correcta
                        for idx, op in enumerate(opciones_actuales):
                            vf_match = re.search(r'\s*[\(\-\s]*(V|F)[\)\s]*$', op, re.IGNORECASE)
                            if vf_match:
                                # Si encontramos V/F, marcar esta opción como correcta
                                respuesta_correcta = idx
                                tiene_subrayado = True
                                break
                        
                        # Si no se encontró V/F, buscar por subrayado/resaltado
                        if not tiene_subrayado:
                            for idx, esta_marcada in enumerate(opciones_marcadas):
                                if esta_marcada:
                                    respuesta_correcta = idx
                                    tiene_subrayado = True
                                    break
                        
                        # Limpiar etiquetas y V/F de todas las opciones
                        opciones_limpias = []
                        for op in opciones_actuales:
                            op_limpia = limpiar_etiqueta_opcion(limpiar_texto(op))
                            # Eliminar V/F al final de opciones
                            op_limpia = re.sub(r'\s*[\(\-\s]*(V|F)[\)\s]*$', '', op_limpia, flags=re.IGNORECASE).strip()
                            opciones_limpias.append(op_limpia)
                        
                        todas_las_preguntas.append({
                            'pregunta': limpiar_texto(pregunta_actual),
                            'opciones': opciones_limpias,
                            'correcta': respuesta_correcta,
                            'tipo': 'opcion_multiple',
                            'tiene_numero': tiene_numero
                        })
                        subrayado_por_pregunta[pregunta_idx] = tiene_subrayado
                        pregunta_idx += 1
                        
                        # Resetear para siguiente pregunta
                        pregunta_actual = None
                        opciones_actuales = []
                        opciones_marcadas = []
                        estado_actual = "enunciado"
                        tiene_numero = False
                    continue
            
            # 3. DETECCIÓN DE PREGUNTA SIN NÚMERO:
            # Si detectamos texto que NO es pregunta ni opción y no hay pregunta iniciada,
            # y el texto es significativo, asumir pregunta nueva sin número
            if not pregunta_actual and not es_pregunta and not es_opcion:
                if len(texto_completo) > 15:  # Texto significativo
                    pregunta_actual = texto_completo
                    tiene_numero = False
                    estado_actual = "enunciado"
                    continue
            
            # 4. ACUMULACIÓN DE TEXTO: Captura total según estado
            if pregunta_actual and not pregunta_cerrada:
                if estado_actual == "opciones" and len(opciones_actuales) > 0:
                    # Ya encontramos opciones → añadir a la última opción (CAPTURA TOTAL)
                    # Si ya tenemos 4 opciones, añadir a la última (opción d)
                    if len(opciones_actuales) >= 4:
                        opciones_actuales[3] += " " + texto_completo
                        # REFUERZO DE SUBRAYADO: Si alguna parte está marcada, marcar toda la opción
                        if marcado_linea:
                            opciones_marcadas[3] = True
                    else:
                        opciones_actuales[-1] += " " + texto_completo
                        # REFUERZO DE SUBRAYADO: Si alguna parte está marcada, marcar toda la opción
                        if marcado_linea:
                            opciones_marcadas[-1] = True
                else:
                    # Aún no hay opciones → añadir al enunciado (CAPTURA TOTAL)
                    pregunta_actual += " " + texto_completo
    
    # Guardar última pregunta (CLASIFICACIÓN FINAL)
    if pregunta_actual and not pregunta_cerrada:
        if len(opciones_actuales) > 0:
            # Pregunta con opciones → Opción Múltiple
            # Limitar a máximo 4 opciones si hay más
            if len(opciones_actuales) > 4:
                # Fusionar opciones adicionales con la última (opción d)
                texto_extra = " ".join(opciones_actuales[4:])
                opciones_actuales[3] += " " + texto_extra
                opciones_actuales = opciones_actuales[:4]
                opciones_marcadas = opciones_marcadas[:4]
            
            respuesta_correcta = 0
            tiene_subrayado = False
            for idx, esta_marcada in enumerate(opciones_marcadas):
                if esta_marcada:
                    respuesta_correcta = idx
                    tiene_subrayado = True
                    break
            
            # Limpiar etiquetas y V/F de todas las opciones
            # También detectar V/F al final de cada opción para marcar respuesta correcta
            opciones_limpias = []
            for idx, op in enumerate(opciones_actuales):
                op_limpia = limpiar_etiqueta_opcion(limpiar_texto(op))
                # Detectar V/F al final de la opción
                vf_match = re.search(r'\s*[\(\-\s]*(V|F)[\)\s]*$', op_limpia, re.IGNORECASE)
                if vf_match:
                    # Si encontramos V/F, marcar esta opción como correcta
                    # (aunque normalmente V/F se usa para preguntas V/F, aquí lo usamos como marcador)
                    # Por ahora, solo limpiamos el texto
                    op_limpia = op_limpia[:vf_match.start()].strip()
                else:
                    # Eliminar V/F al final de opciones (limpieza adicional)
                    op_limpia = re.sub(r'\s*[\(\-\s]*(V|F)[\)\s]*$', '', op_limpia, flags=re.IGNORECASE).strip()
                opciones_limpias.append(op_limpia)
            
            todas_las_preguntas.append({
                'pregunta': limpiar_texto(pregunta_actual),
                'opciones': opciones_limpias,
                'correcta': respuesta_correcta,
                'tipo': 'opcion_multiple',
                'tiene_numero': tiene_numero
            })
            subrayado_por_pregunta[pregunta_idx] = tiene_subrayado
        else:
            # Pregunta sin opciones → Verdadero/Falso
            enunciado_limpio, respuesta_vf = detectar_vf_en_enunciado(limpiar_texto(pregunta_actual))
            respuesta_correcta = respuesta_vf if respuesta_vf is not None else 0
            vf_detectado_enunciado = respuesta_vf is not None
            
            todas_las_preguntas.append({
                'pregunta': enunciado_limpio,
                'opciones': [],
                'correcta': respuesta_correcta,
                'tipo': 'V/F',
                'vf_detectado_enunciado': vf_detectado_enunciado,
                'tiene_numero': tiene_numero
            })
            subrayado_por_pregunta[pregunta_idx] = False
    
    doc.close()
    
    # Normalizar: asegurar que todas las preguntas tengan un tipo asignado y limpiar prefijos
    for pregunta in todas_las_preguntas:
        if 'tipo' not in pregunta:
            if len(pregunta.get('opciones', [])) > 0:
                pregunta['tipo'] = 'opcion_multiple'
            else:
                pregunta['tipo'] = 'V/F'
        
        # Limpiar prefijo [S/N] del texto almacenado (solo se muestra en el título)
        if pregunta.get('pregunta', '').startswith("[S/N]"):
            pregunta['pregunta'] = pregunta['pregunta'].replace("[S/N]", "").strip()
            pregunta['tiene_numero'] = False
    
    return todas_las_preguntas, subrayado_por_pregunta


def mostrar_modo_revision():
    """
    Interfaz compacta de revisión con vista por defecto optimizada.
    Permite edición profunda solo cuando el usuario lo solicita.
    Todos los expanders se abren por defecto para facilitar la revisión rápida.
    """
    st.header("⚡ Revisión Rápida - Validación de Datos")
    st.markdown("---")
    st.info("🎯 **Vista Compacta**: Todas las preguntas están expandidas por defecto. Usa '🔧 Editar contenido' solo cuando necesites corregir el texto.")
    
    preguntas = st.session_state.preguntas
    
    if not preguntas:
        st.warning("No hay preguntas para revisar.")
        return
    
    # Estadísticas rápidas compactas
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Total", len(preguntas))
    with col2:
        sin_respuesta = sum(1 for idx, p in enumerate(preguntas) 
                           if p.get('correcta', None) is None or 
                           (p.get('tipo') == 'opcion_multiple' and len(p.get('opciones', [])) > 0 and p.get('correcta', -1) < 0))
        st.metric("⚠️ Sin respuesta", sin_respuesta, delta=None)
    with col3:
        con_respuesta = len(preguntas) - sin_respuesta
        st.metric("✅ Con respuesta", con_respuesta)
    with col4:
        preguntas_vf = sum(1 for p in preguntas if p.get('tipo') == 'V/F' or len(p.get('opciones', [])) == 0)
        st.metric("✓/✗ V/F", preguntas_vf)
    with col5:
        preguntas_opcion_multiple = sum(1 for p in preguntas if len(p.get('opciones', [])) > 0)
        st.metric("A/B/C/D", preguntas_opcion_multiple)
    
    st.markdown("---")
    
    # Lista de preguntas con diseño compacto usando expanders (todos abiertos por defecto)
    preguntas_sin_respuesta = []
    
    for idx in range(len(preguntas)):
        pregunta_data = st.session_state.preguntas[idx]
        
        # Determinar tipo y estado
        tipo_pregunta = pregunta_data.get('tipo', 'opcion_multiple')
        es_vf = tipo_pregunta == 'V/F' or len(pregunta_data.get('opciones', [])) == 0
        
        # Verificar si tiene respuesta marcada
        tiene_respuesta = False
        if es_vf:
            tiene_respuesta = pregunta_data.get('correcta', None) is not None
        else:
            tiene_respuesta = (pregunta_data.get('correcta', None) is not None and 
                             pregunta_data.get('correcta', -1) >= 0 and
                             pregunta_data.get('correcta', -1) < len(pregunta_data.get('opciones', [])))
        
        if not tiene_respuesta:
            preguntas_sin_respuesta.append(idx)
        
        # Título del expander: número + inicio del enunciado
        enunciado = pregunta_data.get('pregunta', '')
        enunciado_preview = enunciado[:60] + "..." if len(enunciado) > 60 else enunciado
        emoji_tipo = "✓/✗" if es_vf else "A/B/C/D"
        estado_emoji = "✅" if tiene_respuesta else "⚠️"
        
        # Manejar preguntas sin número
        tiene_numero = pregunta_data.get('tiene_numero', True)
        if not tiene_numero:
            # Pregunta sin número - usar prefijo S/N en el título
            titulo_expander = f"{estado_emoji} Pregunta {idx + 1} [S/N] [{emoji_tipo}] - {enunciado_preview}"
        else:
            titulo_expander = f"{estado_emoji} Pregunta {idx + 1} [{emoji_tipo}] - {enunciado_preview}"
        
        # TODOS LOS EXPANDERS ABIERTOS POR DEFECTO
        with st.expander(titulo_expander, expanded=True):
            # Checkbox para editar contenido (en la misma línea con columnas)
            # Streamlit maneja automáticamente el estado del widget mediante la key
            col_edit, col_spacer = st.columns([1, 4])
            with col_edit:
                edit_mode = st.checkbox(
                    "🔧 Editar contenido",
                    key=f"edit_content_{idx}",
                    value=False  # Valor por defecto, Streamlit lo maneja automáticamente
                )
            
            st.markdown("---")
            
            # VISUALIZACIÓN: Texto simple por defecto, text_area si se activa edición
            # El prefijo [S/N] ya se limpia en la normalización, solo se muestra en el título
            enunciado_actual = pregunta_data.get('pregunta', '')
            
            if edit_mode:
                # Modo edición: text_area
                nuevo_enunciado = st.text_area(
                    "**Enunciado:**",
                    value=enunciado_actual,
                    key=f"enunciado_{idx}",
                    height=100,
                    help="Edita el enunciado completo de la pregunta"
                )
                # PERSISTENCIA INSTANTÁNEA
                if nuevo_enunciado != enunciado_actual:
                    st.session_state.preguntas[idx]['pregunta'] = nuevo_enunciado
            else:
                # Modo visualización: texto simple completo
                st.markdown("**Enunciado:**")
                st.write(enunciado_actual)
            
            # ALERTAS VISUALES
            vf_detectado = pregunta_data.get('vf_detectado_enunciado', False)
            if es_vf and vf_detectado:
                st.success("✅ Respuesta extraída del enunciado (V/F)")
            
            if es_vf and tiene_patrones_opcion_en_texto(enunciado_actual):
                st.warning("⚠️ Posible error de detección de formato: El enunciado contiene patrones de opciones (a., b.), etc.)")
            
            st.markdown("---")
            
            # Opciones (si es opción múltiple)
            if not es_vf:
                opciones_actuales = pregunta_data.get('opciones', [])
                if len(opciones_actuales) > 0:
                    st.markdown("**Opciones:**")
                    
                    if edit_mode:
                        # Modo edición: text_area para cada opción
                        nuevas_opciones = []
                        for opcion_idx, opcion_texto in enumerate(opciones_actuales):
                            nueva_opcion = st.text_area(
                                f"Opción {chr(65 + opcion_idx)}:",
                                value=opcion_texto,
                                key=f"opcion_{idx}_{opcion_idx}",
                                height=80,
                                help=f"Edita el texto completo de la opción {chr(65 + opcion_idx)}"
                            )
                            nuevas_opciones.append(nueva_opcion)
                        
                        # PERSISTENCIA INSTANTÁNEA: Actualizar toda la lista si hay cambios
                        if nuevas_opciones != opciones_actuales:
                            st.session_state.preguntas[idx]['opciones'] = nuevas_opciones
                    else:
                        # Modo visualización: texto simple completo (sin etiquetas a., b., etc.)
                        # Las opciones ya están limpias (sin a., b.), solo mostramos el texto
                        for opcion_idx, opcion_texto in enumerate(opciones_actuales):
                            letra_opcion = chr(65 + opcion_idx)
                            st.markdown(f"**{letra_opcion}.** {opcion_texto}")
                    
                    st.markdown("---")
                else:
                    st.warning("⚠️ No hay opciones detectadas. Activa 'Editar contenido' para agregarlas.")
            
            # QUICK-SELECT: Selector rápido de respuesta correcta (siempre visible)
            col_radio, col_spacer2 = st.columns([3, 1])
            with col_radio:
                st.markdown("**Respuesta Correcta:**")
                if es_vf:
                    # Verdadero/Falso - Radio horizontal rápido
                    respuesta_actual = pregunta_data.get('correcta', 0)
                    respuesta_vf = st.radio(
                        "Selecciona la respuesta correcta:",  # Label explícito para evitar advertencias
                        options=['Verdadero', 'Falso'],
                        index=respuesta_actual if respuesta_actual in [0, 1] else 0,
                        key=f"quick_vf_{idx}",
                        horizontal=True,
                        label_visibility="collapsed"  # Oculto visualmente pero presente internamente
                    )
                    # PERSISTENCIA INSTANTÁNEA
                    nueva_respuesta = 0 if respuesta_vf == 'Verdadero' else 1
                    if nueva_respuesta != respuesta_actual:
                        st.session_state.preguntas[idx]['correcta'] = nueva_respuesta
                        st.session_state.preguntas[idx]['tipo'] = 'V/F'
                else:
                    # Opción múltiple - Radio horizontal rápido
                    opciones_actuales = pregunta_data.get('opciones', [])
                    respuesta_actual = pregunta_data.get('correcta', -1)
                    
                    if len(opciones_actuales) > 0:
                        # Preparar labels con formato visual (solo letras para radio horizontal)
                        opciones_labels = []
                        for opcion_idx in range(len(opciones_actuales)):
                            letra_opcion = chr(65 + opcion_idx)
                            es_seleccionada = (respuesta_actual == opcion_idx)
                            emoji = "✅" if es_seleccionada else "○"
                            opciones_labels.append(f"{emoji} {letra_opcion}")
                        
                        # Radio buttons horizontales compactos
                        respuesta_seleccionada = st.radio(
                            "Selecciona la respuesta correcta:",  # Label explícito para evitar advertencias
                            options=list(range(len(opciones_actuales))),
                            format_func=lambda x: opciones_labels[x],
                            index=respuesta_actual if respuesta_actual >= 0 and respuesta_actual < len(opciones_actuales) else 0,
                            key=f"quick_radio_{idx}",
                            horizontal=True,
                            label_visibility="collapsed"  # Oculto visualmente pero presente internamente
                        )
                        
                        # PERSISTENCIA INSTANTÁNEA
                        if respuesta_seleccionada != respuesta_actual:
                            st.session_state.preguntas[idx]['correcta'] = respuesta_seleccionada
            
            # Editor avanzado (solo si se activa edición y es necesario)
            if edit_mode:
                st.markdown("---")
                st.markdown("**Opciones Avanzadas:**")
                
                # Selector de tipo
                tipo_actual = pregunta_data.get('tipo', 'opcion_multiple')
                if len(pregunta_data.get('opciones', [])) == 0:
                    tipo_actual = 'V/F'
                
                tipo_seleccionado = st.radio(
                    "Tipo de pregunta:",
                    options=['opcion_multiple', 'V/F'],
                    format_func=lambda x: 'Opción Múltiple (A/B/C/D)' if x == 'opcion_multiple' else 'Verdadero/Falso (✓/✗)',
                    index=0 if tipo_actual == 'opcion_multiple' else 1,
                    key=f"tipo_pregunta_{idx}",
                    horizontal=True
                )
                # Actualizar tipo solo si cambió (evitar sobreescritura innecesaria)
                if tipo_seleccionado != tipo_actual:
                    st.session_state.preguntas[idx]['tipo'] = tipo_seleccionado
                
                # Botones para agregar/eliminar opciones (solo si es opción múltiple)
                if tipo_seleccionado == 'opcion_multiple':
                    col_add, col_del = st.columns(2)
                    with col_add:
                        if st.button("➕ Agregar Opción", key=f"add_{idx}", use_container_width=True):
                            opciones_actuales = st.session_state.preguntas[idx].get('opciones', [])
                            opciones_actuales.append('')
                            st.session_state.preguntas[idx]['opciones'] = opciones_actuales
                            st.rerun()
                    with col_del:
                        opciones_actuales = st.session_state.preguntas[idx].get('opciones', [])
                        if len(opciones_actuales) > 2 and st.button("➖ Eliminar Última", key=f"del_{idx}", use_container_width=True):
                            opciones_actuales.pop()
                            st.session_state.preguntas[idx]['opciones'] = opciones_actuales
                            st.rerun()
    
    st.markdown("---")
    
    # Resumen y acciones finales
    if preguntas_sin_respuesta:
        st.warning(f"⚠️ **{len(preguntas_sin_respuesta)} pregunta(s) sin respuesta marcada.** Revisa las preguntas destacadas en amarillo/naranja arriba.")
    
    # Botones de acción rápida
    col1, col2, col3 = st.columns([1, 1, 1])
    
    with col1:
        if st.button("✅ Marcar Todas como Revisadas", use_container_width=True, 
                    help="Marca todas las preguntas como revisadas y pasa al test"):
            # Asegurar que todas tengan al menos una respuesta por defecto
            for idx in range(len(preguntas)):
                pregunta_data = st.session_state.preguntas[idx]
                if pregunta_data.get('correcta', None) is None:
                    if pregunta_data.get('tipo') == 'V/F' or len(pregunta_data.get('opciones', [])) == 0:
                        st.session_state.preguntas[idx]['correcta'] = 0
                    elif len(pregunta_data.get('opciones', [])) > 0:
                        st.session_state.preguntas[idx]['correcta'] = 0
            
            st.session_state.modo_revision = False
            st.session_state.revision_completada = True
            st.session_state.pregunta_actual = 0
            st.session_state.respuestas_usuario = {}
            st.session_state.verificaciones = {}
            st.rerun()
    
    with col2:
        if st.button("🎮 Ir al Test", type="primary", use_container_width=True,
                    help="Comienza el simulador de flashcards"):
            st.session_state.modo_revision = False
            st.session_state.revision_completada = True
            st.session_state.pregunta_actual = 0
            st.session_state.respuestas_usuario = {}
            st.session_state.verificaciones = {}
            st.rerun()
    
    with col3:
        if st.button("🔄 Recargar Vista", use_container_width=True,
                    help="Actualiza la vista de revisión"):
            st.rerun()


def main():
    st.title("📚 Simulador de Exámenes Interactivo")
    st.markdown("---")
    
    # Sidebar para cargar archivo
    with st.sidebar:
        st.header("📁 Cargar PDF")
        uploaded_file = st.file_uploader(
            "Selecciona un archivo PDF con preguntas",
            type=['pdf'],
            help="El PDF debe contener preguntas de opción múltiple con la respuesta correcta subrayada"
        )
        
        if uploaded_file is not None:
            if not st.session_state.pdf_cargado or st.session_state.preguntas == []:
                with st.spinner("Procesando PDF..."):
                    pdf_bytes = uploaded_file.read()
                    preguntas_extraidas, subrayado_info = extraer_texto_con_subrayado(pdf_bytes)
                    
                    if preguntas_extraidas:
                        st.session_state.preguntas = preguntas_extraidas
                        st.session_state.subrayado_detectado = subrayado_info
                        st.session_state.pregunta_actual = 0
                        st.session_state.respuestas_usuario = {}
                        st.session_state.verificaciones = {}
                        st.session_state.pdf_cargado = True
                        st.session_state.modo_revision = True  # Activar modo revisión
                        st.session_state.revision_completada = False
                        st.success(f"✅ Se encontraron {len(preguntas_extraidas)} preguntas")
                        
                        # Contar preguntas sin subrayado
                        sin_subrayado = sum(1 for idx in range(len(preguntas_extraidas)) 
                                          if not subrayado_info.get(idx, False))
                        if sin_subrayado > 0:
                            st.warning(f"⚠️ {sin_subrayado} pregunta(s) no tienen subrayado detectado. Revisa manualmente en la fase de revisión.")
                        else:
                            st.info("✅ Todas las preguntas tienen subrayado detectado. Revisa y confirma antes de comenzar.")
                    else:
                        st.error("❌ No se pudieron extraer preguntas del PDF. Verifica el formato.")
            else:
                st.info(f"📄 PDF cargado: {len(st.session_state.preguntas)} preguntas disponibles")
                if st.session_state.modo_revision:
                    st.info("📝 Modo: Revisión")
                else:
                    st.info("🎯 Modo: Simulador")
        
        st.markdown("---")
        if st.session_state.preguntas:
            if st.session_state.modo_revision:
                st.info("📝 Modo: Revisión")
            else:
                st.info("🎯 Modo: Simulador")
                if st.button("✏️ Volver a Revisión", use_container_width=True):
                    st.session_state.modo_revision = True
                    st.rerun()
            
            st.markdown("---")
            st.metric("Preguntas totales", len(st.session_state.preguntas))
            if not st.session_state.modo_revision:
                st.metric("Pregunta actual", st.session_state.pregunta_actual + 1)
                respuestas_completadas = len([k for k in st.session_state.respuestas_usuario.keys() 
                                             if k < len(st.session_state.preguntas)])
                st.metric("Completadas", respuestas_completadas)
    
    # Área principal
    if not st.session_state.preguntas:
        st.info("👆 Por favor, carga un archivo PDF desde el panel lateral para comenzar.")
        st.markdown("""
        ### Instrucciones:
        1. **Carga tu PDF**: Usa el panel lateral para seleccionar un archivo PDF
        2. **Formato esperado**: El PDF debe contener preguntas de opción múltiple
        3. **Respuestas correctas**: Deben estar subrayadas en el PDF original
        4. **Fase de Revisión**: Después de cargar, revisa y edita las preguntas extraídas
        5. **Simulador**: Una vez confirmado, comienza a responder las preguntas
        """)
    elif st.session_state.modo_revision and not st.session_state.revision_completada:
        # Mostrar modo de revisión
        mostrar_modo_revision()
    else:
        preguntas = st.session_state.preguntas
        idx_actual = st.session_state.pregunta_actual
        
        if idx_actual < len(preguntas):
            pregunta_data = preguntas[idx_actual]
            
            # Mostrar pregunta
            st.subheader(f"Pregunta {idx_actual + 1} de {len(preguntas)}")
            st.markdown("---")
            
            # Mostrar el texto de la pregunta (texto limpio, sin etiquetas)
            st.markdown(f"### {pregunta_data['pregunta']}")
            
            # Determinar tipo de pregunta
            tipo_pregunta = pregunta_data.get('tipo', 'opcion_multiple')
            es_vf = tipo_pregunta == 'V/F' or len(pregunta_data.get('opciones', [])) == 0
            
            if es_vf:
                # Pregunta Verdadero/Falso - Interfaz dinámica con botones grandes
                st.markdown("**Tipo: Verdadero/Falso**")
                st.markdown("---")
                respuesta_seleccionada = st.radio(
                    "**Selecciona tu respuesta:**",
                    options=['Verdadero', 'Falso'],
                    key=f"respuesta_{idx_actual}",
                    index=st.session_state.respuestas_usuario.get(idx_actual, None),
                    horizontal=True
                )
                
                # Convertir a índice numérico (0 = Verdadero, 1 = Falso)
                respuesta_idx = 0 if respuesta_seleccionada == 'Verdadero' else 1
                st.session_state.respuestas_usuario[idx_actual] = respuesta_idx
            else:
                # Pregunta de opción múltiple - Botones grandes y claros (texto limpio)
                st.markdown("**Selecciona tu respuesta:**")
                st.markdown("---")
                # Las opciones ya están limpias (sin a., b), etc.)
                opciones_labels = [f"**{chr(65+i)}.** {opcion}" for i, opcion in enumerate(pregunta_data['opciones'])]
            
            respuesta_seleccionada = st.radio(
                    "",
                options=list(range(len(pregunta_data['opciones']))),
                format_func=lambda x: opciones_labels[x],
                key=f"respuesta_{idx_actual}",
                    index=st.session_state.respuestas_usuario.get(idx_actual, None),
                    label_visibility="collapsed"
            )
            
            # Guardar respuesta del usuario
            st.session_state.respuestas_usuario[idx_actual] = respuesta_seleccionada
            
            col1, col2, col3 = st.columns([1, 1, 2])
            
            with col1:
                if st.button("✅ Verificar", type="primary", use_container_width=True):
                    # Obtener respuesta seleccionada (ya está guardada en session_state)
                    respuesta_usuario_idx = st.session_state.respuestas_usuario.get(idx_actual)
                    respuesta_correcta_idx = pregunta_data.get('correcta', 0)
                    
                    es_correcta = respuesta_usuario_idx == respuesta_correcta_idx
                    st.session_state.verificaciones[idx_actual] = es_correcta
                    
                    if es_correcta:
                        st.success("🎉 ¡Correcto!")
                    else:
                        if es_vf:
                            respuesta_correcta_texto = "Verdadero" if respuesta_correcta_idx == 0 else "Falso"
                            st.error(f"❌ Incorrecto. La respuesta correcta es: **{respuesta_correcta_texto}**")
                        else:
                            respuesta_correcta_letra = chr(65 + respuesta_correcta_idx)
                            st.error(f"❌ Incorrecto. La respuesta correcta es: **{respuesta_correcta_letra}**")
            
            with col2:
                if st.button("➡️ Siguiente", use_container_width=True):
                    if idx_actual < len(preguntas) - 1:
                        st.session_state.pregunta_actual = idx_actual + 1
                        st.rerun()
                    else:
                        st.info("📝 Has llegado al final del examen.")
            
            # Mostrar resultado de verificación si existe
            if idx_actual in st.session_state.verificaciones:
                es_correcta = st.session_state.verificaciones[idx_actual]
                if es_correcta:
                    st.success("✅ Respuesta correcta")
                else:
                    respuesta_correcta_idx = pregunta_data.get('correcta', 0)
                    if es_vf:
                        respuesta_correcta_texto = "Verdadero" if respuesta_correcta_idx == 0 else "Falso"
                        st.error(f"❌ La respuesta correcta es: **{respuesta_correcta_texto}**")
                    else:
                        respuesta_correcta_texto = pregunta_data['opciones'][respuesta_correcta_idx]
                        st.error(f"❌ La respuesta correcta es: **{chr(65 + respuesta_correcta_idx)}. {respuesta_correcta_texto}**")
            
            # Navegación rápida
            st.markdown("---")
            st.subheader("Navegación rápida")
            cols_nav = st.columns(min(10, len(preguntas)))
            
            for i in range(min(10, len(preguntas))):
                with cols_nav[i]:
                    estado = "✅" if i in st.session_state.verificaciones else "📝"
                    if st.button(f"{estado} {i+1}", key=f"nav_{i}", use_container_width=True):
                        st.session_state.pregunta_actual = i
                        st.rerun()
            
            # Resumen al final
            if idx_actual == len(preguntas) - 1:
                st.markdown("---")
                st.subheader("📊 Resumen del Examen")
                total_preguntas = len(preguntas)
                respuestas_verificadas = len(st.session_state.verificaciones)
                respuestas_correctas = sum(1 for v in st.session_state.verificaciones.values() if v)
                
                if respuestas_verificadas > 0:
                    porcentaje = (respuestas_correctas / respuestas_verificadas) * 100
                    st.metric("Respuestas correctas", f"{respuestas_correctas}/{respuestas_verificadas}")
                    st.metric("Porcentaje de aciertos", f"{porcentaje:.1f}%")
        
        else:
            st.info("No hay más preguntas disponibles.")


if __name__ == "__main__":
    main()

