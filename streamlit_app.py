import streamlit as st

# --- Instrucciones de Ejecución ---
# Para ejecutar esta aplicación, abre tu terminal, navega a la carpeta
# del proyecto y ejecuta el siguiente comando:
# streamlit run streamlit_app.py
# -----------------------------------

# Configuración de la página (opcional, pero recomendado)
st.set_page_config(
    page_title="Proyecto Titán v2.0",
    page_icon="🤖",
    layout="wide"
)

# Título principal de la aplicación
st.title("Proyecto Titán v2.0 - Interfaz Web")

# Encabezado y texto introductorio
st.header("Bienvenido a la nueva plataforma de análisis")

st.markdown("""
Esta es la base para la nueva interfaz web del Proyecto Titán. 
Desde aquí construiremos las funcionalidades para:

-   Cargar y procesar los reportes PDF.
-   Visualizar los datos de telemetría de forma interactiva.
-   Comparar la evolución de los operarios a lo largo del tiempo.
-   Generar y descargar los informes en PDF.
""")

st.info("La aplicación se actualizará automáticamente cada vez que guardes este archivo.")
