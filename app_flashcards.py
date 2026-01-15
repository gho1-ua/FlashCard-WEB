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


def detectar_subrayado(texto_dict: dict) -> Dict[int, bool]:
    """
    Detecta qué bloques de texto están subrayados en el PDF.
    Retorna un diccionario con el índice del bloque y si está subrayado.
    """
    subrayados = {}
    
    for block in texto_dict.get("blocks", []):
        if "lines" in block:
            for line in block["lines"]:
                for span in line.get("spans", []):
                    flags = span.get("flags", 0)
                    # El flag 4 (bit 2) indica underline en PyMuPDF
                    # También verificamos si hay alguna propiedad de underline
                    is_underlined = (flags & 4) != 0 or (flags & 8388608) != 0
                    
                    # Obtener el índice del bloque para referencia
                    block_idx = texto_dict["blocks"].index(block)
                    if block_idx not in subrayados:
                        subrayados[block_idx] = False
                    if is_underlined:
                        subrayados[block_idx] = True
    
    return subrayados


def extraer_texto_con_subrayado(pdf_bytes: bytes) -> List[Dict]:
    """
    Extrae preguntas y opciones del PDF, detectando respuestas correctas por subrayado.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    todas_las_preguntas = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        texto_dict = page.get_text("dict")
        texto_plano = page.get_text()
        
        # Detectar bloques subrayados
        subrayados = detectar_subrayado(texto_dict)
        
        # Dividir el texto en líneas para procesar
        lineas = texto_plano.split('\n')
        lineas = [linea.strip() for linea in lineas if linea.strip()]
        
        pregunta_actual = None
        opciones_actuales = []
        indices_subrayados = []
        
        # Buscar patrones de preguntas y opciones
        for i, linea in enumerate(lineas):
            # Detectar si es una pregunta (número seguido de punto o letra)
            if re.match(r'^\d+[\.\)]\s+', linea) or re.match(r'^[A-Z][\.\)]\s+', linea):
                # Si hay una pregunta previa, guardarla
                if pregunta_actual and opciones_actuales:
                    todas_las_preguntas.append({
                        'pregunta': pregunta_actual,
                        'opciones': opciones_actuales,
                        'correcta': indices_subrayados[0] if indices_subrayados else 0
                    })
                
                # Nueva pregunta
                pregunta_actual = linea
                opciones_actuales = []
                indices_subrayados = []
            
            # Detectar opciones (a), b), c), d) o A), B), C), D) o 1), 2), 3), 4)
            elif re.match(r'^[a-dA-D1-4][\.\)]\s+', linea):
                opcion_texto = linea
                opciones_actuales.append(opcion_texto)
                
                # Verificar si esta línea está subrayada
                # Buscar en los bloques de texto si contiene esta línea
                for block_idx, is_underlined in subrayados.items():
                    if is_underlined:
                        block_text = ""
                        if block_idx < len(texto_dict.get("blocks", [])):
                            block = texto_dict["blocks"][block_idx]
                            if "lines" in block:
                                for line in block["lines"]:
                                    for span in line.get("spans", []):
                                        block_text += span.get("text", "")
                        
                        # Si el texto subrayado contiene esta opción, marcarla como correcta
                        if opcion_texto[:10] in block_text or block_text[:50] in opcion_texto:
                            if len(opciones_actuales) - 1 not in indices_subrayados:
                                indices_subrayados.append(len(opciones_actuales) - 1)
            
            # Si no es pregunta ni opción, puede ser continuación de la pregunta
            elif pregunta_actual and not opciones_actuales:
                pregunta_actual += " " + linea
        
        # Guardar la última pregunta
        if pregunta_actual and opciones_actuales:
            todas_las_preguntas.append({
                'pregunta': pregunta_actual,
                'opciones': opciones_actuales,
                'correcta': indices_subrayados[0] if indices_subrayados else 0
            })
    
    doc.close()
    
    # Si no se detectaron subrayados, usar método alternativo más simple
    if not todas_las_preguntas:
        # Método alternativo: buscar texto subrayado directamente
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        texto_completo = ""
        texto_subrayado = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            texto_dict = page.get_text("dict")
            
            for block in texto_dict.get("blocks", []):
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line.get("spans", []):
                            texto_completo += span.get("text", "") + " "
                            flags = span.get("flags", 0)
                            if (flags & 4) != 0 or (flags & 8388608) != 0:
                                texto_subrayado.append(span.get("text", "").strip())
        
        doc.close()
        
        # Procesar texto completo para encontrar preguntas
        lineas = texto_completo.split('\n')
        lineas = [linea.strip() for linea in lineas if linea.strip()]
        
        pregunta_actual = None
        opciones_actuales = []
        respuesta_correcta = 0
        
        for i, linea in enumerate(lineas):
            if re.match(r'^\d+[\.\)]\s+', linea) or re.match(r'^[A-Z][\.\)]\s+', linea):
                if pregunta_actual and opciones_actuales:
                    todas_las_preguntas.append({
                        'pregunta': pregunta_actual,
                        'opciones': opciones_actuales,
                        'correcta': respuesta_correcta
                    })
                
                pregunta_actual = linea
                opciones_actuales = []
                respuesta_correcta = 0
            
            elif re.match(r'^[a-dA-D1-4][\.\)]\s+', linea):
                opciones_actuales.append(linea)
                # Verificar si esta opción está en el texto subrayado
                for texto_sub in texto_subrayado:
                    if linea[:20] in texto_sub or texto_sub in linea[:50]:
                        respuesta_correcta = len(opciones_actuales) - 1
                        break
            
            elif pregunta_actual and not opciones_actuales:
                pregunta_actual += " " + linea
        
        if pregunta_actual and opciones_actuales:
            todas_las_preguntas.append({
                'pregunta': pregunta_actual,
                'opciones': opciones_actuales,
                'correcta': respuesta_correcta
            })
    
    return todas_las_preguntas


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
                    preguntas_extraidas = extraer_texto_con_subrayado(pdf_bytes)
                    
                    if preguntas_extraidas:
                        st.session_state.preguntas = preguntas_extraidas
                        st.session_state.pregunta_actual = 0
                        st.session_state.respuestas_usuario = {}
                        st.session_state.verificaciones = {}
                        st.session_state.pdf_cargado = True
                        st.success(f"✅ Se encontraron {len(preguntas_extraidas)} preguntas")
                        
                        # Advertencia si no se detectaron subrayados
                        subrayados_detectados = any(
                            p['correcta'] != 0 or any(
                                'underline' in str(p).lower() or 
                                p['opciones'][p['correcta']] != p['opciones'][0]
                                for p in preguntas_extraidas
                            )
                            for p in preguntas_extraidas
                        )
                        if not subrayados_detectados:
                            st.warning("⚠️ No se detectaron respuestas subrayadas. Se marcará la primera opción como predeterminada.")
                    else:
                        st.error("❌ No se pudieron extraer preguntas del PDF. Verifica el formato.")
            else:
                st.info(f"📄 PDF cargado: {len(st.session_state.preguntas)} preguntas disponibles")
        
        st.markdown("---")
        if st.session_state.preguntas:
            st.metric("Preguntas totales", len(st.session_state.preguntas))
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
        4. **Navegación**: Usa los botones para avanzar y verificar tus respuestas
        """)
    else:
        preguntas = st.session_state.preguntas
        idx_actual = st.session_state.pregunta_actual
        
        if idx_actual < len(preguntas):
            pregunta_data = preguntas[idx_actual]
            
            # Mostrar pregunta
            st.subheader(f"Pregunta {idx_actual + 1} de {len(preguntas)}")
            st.markdown("---")
            
            # Mostrar el texto de la pregunta (soporta LaTeX)
            st.markdown(f"### {pregunta_data['pregunta']}")
            
            # Radio buttons para opciones
            opciones_labels = [f"{chr(65+i)}. {opcion}" for i, opcion in enumerate(pregunta_data['opciones'])]
            
            respuesta_seleccionada = st.radio(
                "Selecciona tu respuesta:",
                options=list(range(len(pregunta_data['opciones']))),
                format_func=lambda x: opciones_labels[x],
                key=f"respuesta_{idx_actual}",
                index=st.session_state.respuestas_usuario.get(idx_actual, None)
            )
            
            # Guardar respuesta del usuario
            st.session_state.respuestas_usuario[idx_actual] = respuesta_seleccionada
            
            col1, col2, col3 = st.columns([1, 1, 2])
            
            with col1:
                if st.button("✅ Verificar", type="primary", use_container_width=True):
                    es_correcta = respuesta_seleccionada == pregunta_data['correcta']
                    st.session_state.verificaciones[idx_actual] = es_correcta
                    
                    if es_correcta:
                        st.success("🎉 ¡Correcto!")
                    else:
                        respuesta_correcta_letra = chr(65 + pregunta_data['correcta'])
                        st.error(f"❌ Incorrecto. La respuesta correcta es: {respuesta_correcta_letra}")
            
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
                    respuesta_correcta_idx = pregunta_data['correcta']
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

