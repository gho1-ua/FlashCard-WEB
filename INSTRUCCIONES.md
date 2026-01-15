# 📚 Simulador de Exámenes Interactivo

## Instalación

1. **Instalar las dependencias necesarias:**

py -m pip install -r requirements.txt


## Ejecución

Para ejecutar la aplicación, usa el siguiente comando:

py -m streamlit run app_flashcards.py

La aplicación se abrirá automáticamente en tu navegador en `http://localhost:8501`

## Uso

1. **Cargar PDF**: Usa el panel lateral para seleccionar un archivo PDF con preguntas de opción múltiple
2. **Formato del PDF**: 
   - Las preguntas deben estar numeradas (ej: "1. Pregunta...")
   - Las opciones deben estar marcadas con letras o números (ej: "a) Opción 1", "b) Opción 2")
   - La respuesta correcta debe estar **subrayada** en el PDF original
3. **Navegar**: Usa los botones "Siguiente" y la navegación rápida para moverte entre preguntas
4. **Verificar**: Selecciona tu respuesta y presiona "Verificar" para ver si es correcta
5. **Progreso**: Tu progreso se guarda automáticamente durante la sesión

## Notas Importantes

- Si no se detectan respuestas subrayadas en el PDF, se marcará la primera opción como predeterminada
- El formato del PDF debe ser texto seleccionable (no imágenes escaneadas)
- La aplicación soporta símbolos matemáticos y LaTeX en las preguntas

