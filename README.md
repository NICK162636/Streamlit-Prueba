# Streamlit-Prueba

DASHBOARD TERRITORIAL EDUCATIVO - CORRIENTES
============================================

Aplicación: Establecimientos Educativos en Corrientes - INDEC vs Oferta
Archivo principal: app.py

ARCHIVOS DEL PROYECTO
---------------------
Deben permanecer en la misma carpeta que app.py:
- ESCUELAS_DEFINITIVA.xlsx
- INDEC.xlsx
- BarriosCorrientes.geojson
- requirements.txt

Guia Unida.pdf es la devolución docente y referencia metodológica del proyecto;
no se utiliza como fuente de cifras por la aplicación. Este README describe el
paquete recibido y sus criterios. No existe una carpeta .streamlit/config.toml
en el ZIP original y no es necesaria para ejecutar la aplicación.

INSTALACIÓN Y EJECUCIÓN LOCAL
-----------------------------
1. Abrir una terminal en la carpeta del proyecto.
2. Instalar las dependencias declaradas:

   python -m pip install -r requirements.txt

3. Iniciar Streamlit:

   streamlit run app.py

DESPLIEGUE EN STREAMLIT COMMUNITY CLOUD
---------------------------------------
- Seleccionar el repositorio y la rama correspondientes.
- Definir app.py como archivo principal.
- Mantener los tres archivos de datos junto a app.py.
- No se necesita una base de datos, credenciales ni un servicio externo.
- No se requiere una configuración .streamlit adicional para esta versión.

FUENTES Y ALCANCE
-----------------
- ESCUELAS_DEFINITIVA.xlsx: padrón de 365 registros y 20 columnas.
- INDEC.xlsx: Censo 2022, Cuadro 2.7.3, departamento Capital.
- BarriosCorrientes.geojson: soporte espacial de los polígonos barriales; no
  aporta población ni otros indicadores estadísticos.
- Guia Unida.pdf: devolución docente, usada como orientación metodológica.

CONTROLES DE CALIDAD COMPROBADOS EN LOS ARCHIVOS RECIBIDOS
----------------------------------------------------------
- 365 registros originales; 332 tienen al menos una de las cinco banderas de
  nivel analizadas y 33 no tienen ninguna de esas banderas.
- Conteos por nivel (pueden superponerse): Educación temprana/CDI 43, Inicial
  105, Primaria 133, Secundaria 98 y Superior 28. Hay 42 registros con más de
  un nivel. No se deben sumar las barras para obtener establecimientos únicos.
- Los 33 registros sin bandera de esos niveles se describen en MDE como 29
  TECNICO PROFESIONAL, 3 SERVICIOS COMPLEMENTARIOS y 1
  DOMICILIARIA_HOSPITALARIA. El Excel actual no permite separar de manera
  inequívoca formación profesional de un posible taller artístico.
- 308 registros tienen al menos una modalidad identificada; 57 quedan como
  Sin especificar y no se asignan a Común.
- 363 registros tienen coordenadas válidas; 343 se asignan espacialmente a un
  barrio, 20 quedan fuera de los polígonos y 2 tienen coordenadas inválidas.
- El GeoJSON tiene 141 piezas originales y 139 nombres únicos después de
  disolver piezas con el mismo nombre. 74 barrios contienen registros y 65 no
  tienen establecimientos registrados en el padrón completo.
- No hay filas completas duplicadas ni identificadores gid o CUE repetidos.
- El padrón actual no contiene un campo pre; no se calcula un porcentaje de
  ubicaciones aproximadas.
- INDEC: las cinco categorías comparadas suman 131.012 personas; las siete
  categorías educativas del total suman 161.863, incluyendo Universidad y
  Posgrado. El código lee estos valores de INDEC.xlsx.

CRITERIOS IMPORTANTES
---------------------
- Un establecimiento puede ofrecer varios niveles o modalidades. Cuenta una
  vez en cada bandera activa, pero una sola vez como registro individual.
- La pertenencia a un barrio se determina por coordenadas y polígonos. El
  barrio textual se utiliza para contraste, no para asignar una ubicación.
- Los puntos fuera de los polígonos no se asignan al barrio más cercano.
- Los polígonos rojos representan barrios sin establecimientos registrados
  en todo el padrón y no cambian con los filtros. Los límites se muestran en
  azul.
- La asistencia INDEC corresponde al departamento Capital; no se distribuye
  por barrio y no representa matrícula de cada escuela.
- Las fuentes no incluyen matrícula por establecimiento, capacidad ni
  vacantes. El tablero no estima déficit de vacantes.

Los archivos originales de datos deben conservarse sin modificaciones. Las
transformaciones se realizan dentro de app.py y las tablas derivadas se
preparan en memoria.
