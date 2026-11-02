from __future__ import annotations

from pathlib import Path
from typing import Iterable
import re
import unicodedata

import geopandas as gpd
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon

# -----------------------------------------------------------------------------
# Configuración general
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="Establecimientos Educativos en Corrientes - INDEC vs Oferta",
    layout="wide",
)

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_ESCUELAS = BASE_DIR / "ESCUELAS_DEFINITIVA.xlsx"
ARCHIVO_INDEC = BASE_DIR / "INDEC.xlsx"
ARCHIVO_BARRIOS = BASE_DIR / "BarriosCorrientes.geojson"

CENTRO_CORRIENTES = {"lat": -27.4692, "lon": -58.8306}
ZOOM_INICIAL = 11.5

# Cada columna representa una oferta que puede coexistir con otras en la misma fila.
MAPA_NIVELES = {
    "N. ED. TERMPRANA": "Educación temprana / CDI",
    "N.INICIAL": "Inicial",
    "N. PRIMARIO": "Primaria",
    "N. SECUNDARIO": "Secundaria",
    "N. SUPERIOR": "Superior",
}

MAPA_MODALIDADES = {
    "M. COMUN": "Común",
    "M. ESPECIAL": "Especial",
    "M. RURAL": "Rural",
    "M. TECNICA": "Técnica",
    "M. ARTISTICA": "Artística",
    "M. BILINGÜE": "Bilingüe",
    "M. CONTEXTO DE ENCIERRO": "Contexto de Encierro",
    "M. EPJA": "Jóvenes y Adultos (EPJA)",
}

ORDEN_NIVELES = list(MAPA_NIVELES.values())
ORDEN_MODALIDADES = list(MAPA_MODALIDADES.values())

COLORES_NIVELES = {
    "Educación temprana / CDI": "#F2994A",
    "Inicial": "#9B8AFB",
    "Primaria": "#2D9CDB",
    "Secundaria": "#56CCF2",
    "Superior": "#F2C94C",
    "Sin nivel identificado": "#A0AEC0",
}

COLORES_MODALIDADES = {
    "Común": "#56CCF2",
    "Especial": "#9B8AFB",
    "Rural": "#27AE80",
    "Técnica": "#2D9CDB",
    "Artística": "#F2C94C",
    "Bilingüe": "#BB6BD9",
    "Contexto de Encierro": "#EB5757",
    "Jóvenes y Adultos (EPJA)": "#F2994A",
    "Sin especificar": "#A0AEC0",
}

COLOR_TEXTO = "#EAF2FF"
COLOR_CELESTE = "#56CCF2"
COLOR_SIN_ESTABLECIMIENTOS = "#EF4444"
COLOR_CONTORNO_BARRIOS = "#2563EB"

FLAG_NIVELES = {
    columna: f"_flag_nivel_{i}" for i, columna in enumerate(MAPA_NIVELES)
}
FLAG_MODALIDADES = {
    columna: f"_flag_modalidad_{i}" for i, columna in enumerate(MAPA_MODALIDADES)
}

CATEGORIAS_INDEC = {
    "Educación temprana / CDI": "Jardín maternal, guardería, centro de cuidado, salas de 0 a 3",
    "Inicial": "Sala de 4 o 5 (jardín de infantes o preescolar)",
    "Primaria": "Primario",
    "Secundaria": "Secundario",
    "Superior": "Terciario no universitario",
}

ESTADO_FUERA = "Fuera de los barrios"
ESTADO_SIN_COORDENADAS = "Sin coordenadas válidas"
ESTADO_AMBIGUO = "Asignación ambigua (más de un polígono)"


# -----------------------------------------------------------------------------
# Funciones auxiliares
# -----------------------------------------------------------------------------
def resolver_columna(
    df: pd.DataFrame,
    nombres: Iterable[str],
    obligatoria: bool = False,
):
    """Busca una columna sin distinguir mayúsculas/minúsculas ni espacios externos."""
    equivalencias = {str(col).strip().casefold(): col for col in df.columns}
    for nombre in nombres:
        encontrada = equivalencias.get(str(nombre).strip().casefold())
        if encontrada is not None:
            return encontrada
    if obligatoria:
        raise ValueError(
            "No se encontró ninguna de estas columnas en la base: "
            + ", ".join(nombres)
        )
    return None


def es_uno(serie: pd.Series) -> pd.Series:
    """Interpreta como activa una bandera con valor 1, True o respuesta afirmativa."""
    numerica = pd.to_numeric(serie, errors="coerce").eq(1)
    texto = serie.astype("string").str.strip().str.casefold()
    afirmativos = texto.isin(["true", "sí", "si", "yes", "y"])
    return (numerica | afirmativos).fillna(False).astype(bool)


def formatear_identificador(valor) -> str:
    """Evita mostrar un identificador entero como número decimal terminado en .0."""
    if pd.isna(valor):
        return "No informado"
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    if isinstance(valor, int):
        return str(valor)
    return str(valor).strip() or "No informado"


def formato_entero(valor) -> str:
    """Formato numérico argentino: punto como separador de miles."""
    if valor is None or pd.isna(valor):
        return "—"
    return f"{int(round(float(valor))):,}".replace(",", ".")


def formato_decimal(valor, decimales: int = 1) -> str:
    """Formato numérico argentino con coma decimal."""
    if valor is None or pd.isna(valor):
        return "—"
    texto = f"{float(valor):,.{decimales}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def normalizar_nombre_barrio(valor) -> str:
    """Normaliza solo para comparar el texto del padrón con el nombre del GeoJSON."""
    if valor is None or pd.isna(valor):
        return ""
    texto = str(valor).strip().upper()
    if texto in {"", "NULL", "NONE", "NAN", "S/D", "SIN DATO"}:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"^(?:B[º°]\s*|BARRIO\s+)", "", texto)
    texto = re.sub(r"[^A-Z0-9]+", " ", texto)
    return " ".join(texto.split())


def extraer_poligonos(geometria) -> list[Polygon]:
    """Obtiene los componentes poligonales de una geometría, incluidas colecciones."""
    if geometria is None or geometria.is_empty:
        return []
    if isinstance(geometria, Polygon):
        return [geometria]
    if isinstance(geometria, MultiPolygon):
        return list(geometria.geoms)
    if isinstance(geometria, GeometryCollection) or hasattr(geometria, "geoms"):
        resultado = []
        for parte in geometria.geoms:
            resultado.extend(extraer_poligonos(parte))
        return resultado
    return []


def preparar_geometrias_barrios(gdf: gpd.GeoDataFrame):
    """Valida geometrías; solo usa make_valid cuando la geometría original es inválida."""
    resultado = gdf.copy()
    invalidas_originales = int((~resultado.geometry.is_valid).sum())
    vacias_originales = int(resultado.geometry.is_empty.sum())
    reparadas = 0
    geometrias = []

    for geometria in resultado.geometry:
        if geometria is None or geometria.is_empty:
            geometrias.append(None)
            continue
        if not geometria.is_valid:
            geometria = make_valid(geometria)
            reparadas += 1
        partes = [p for p in extraer_poligonos(geometria) if not p.is_empty]
        if not partes:
            geometrias.append(None)
        elif len(partes) == 1:
            geometrias.append(partes[0])
        else:
            geometrias.append(MultiPolygon(partes))

    resultado = resultado.set_geometry(
        gpd.GeoSeries(geometrias, index=resultado.index, crs=gdf.crs)
    )
    sin_geometria = int(resultado.geometry.isna().sum())
    resultado = resultado[resultado.geometry.notna() & ~resultado.geometry.is_empty].copy()
    if not resultado.geometry.is_valid.all():
        raise ValueError("Quedaron geometrías barriales inválidas luego de la validación.")

    estadisticas = {
        "geometrias_invalidas_originales": invalidas_originales,
        "geometrias_vacias_originales": vacias_originales,
        "geometrias_reparadas": reparadas,
        "geometrias_sin_superficie_poligonal": sin_geometria,
    }
    return resultado, estadisticas


def obtener_geometrias_barrios(ruta_geojson: str):
    """Lee los barrios, conserva sus nombres de origen y disuelve nombres repetidos."""
    barrios = gpd.read_file(ruta_geojson)
    cantidad_features = len(barrios)

    if barrios.crs is None:
        # GeoJSON sin CRS declarado: se interpreta según la especificación habitual.
        barrios = barrios.set_crs("EPSG:4326")
    elif barrios.crs.to_epsg() != 4326:
        barrios = barrios.to_crs("EPSG:4326")

    columna_nombre = resolver_columna(
        barrios,
        ["NAM", "NOMBRE", "NOMBRE_BARRIO", "FNA"],
        obligatoria=True,
    )
    barrios["NOMBRE_BARRIO_GEOJSON"] = (
        barrios[columna_nombre].astype("string").str.strip().str.upper()
    )

    nombres_invalidos = (
        barrios["NOMBRE_BARRIO_GEOJSON"].isna()
        | barrios["NOMBRE_BARRIO_GEOJSON"].isin(["", "SIN BARRIO"])
    )
    cantidad_nombres_invalidos = int(nombres_invalidos.sum())
    if cantidad_nombres_invalidos:
        raise ValueError(
            f"El GeoJSON contiene {cantidad_nombres_invalidos} polígonos sin nombre utilizable."
        )

    barrios, estadisticas_geometrias = preparar_geometrias_barrios(barrios)
    frecuencias = barrios["NOMBRE_BARRIO_GEOJSON"].value_counts()
    nombres_repetidos = {
        str(nombre): int(cantidad)
        for nombre, cantidad in frecuencias[frecuencias > 1].items()
    }

    # Las piezas con el mismo NAM representan una sola unidad barrial en los filtros.
    barrios = barrios.dissolve(
        by="NOMBRE_BARRIO_GEOJSON",
        as_index=False,
        aggfunc="first",
    )
    barrios = barrios.sort_values("NOMBRE_BARRIO_GEOJSON").reset_index(drop=True)
    if barrios.empty:
        raise ValueError("El GeoJSON no contiene polígonos barriales utilizables.")

    estadisticas = {
        "geojson_features_originales": cantidad_features,
        "barrios_unicos_geojson": len(barrios),
        "nombres_repetidos_geojson": nombres_repetidos,
        "cantidad_nombres_repetidos_geojson": len(nombres_repetidos),
        "nombres_invalidos_geojson": cantidad_nombres_invalidos,
        **estadisticas_geometrias,
    }
    return barrios, estadisticas


def asignar_barrios_espacialmente(
    df_escuelas: pd.DataFrame,
    gdf_barrios: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Asigna por relación espacial; nunca usa el barrio textual como sustituto."""
    df = df_escuelas.copy()
    df["Coordenadas_validas"] = (
        df["LATITUD"].notna()
        & df["LONGITUD"].notna()
        & df["LATITUD"].between(-90, 90)
        & df["LONGITUD"].between(-180, 180)
    )
    df["Barrio_Geografico"] = ESTADO_SIN_COORDENADAS
    df["Estado_Asignacion_Geografica"] = ESTADO_SIN_COORDENADAS

    puntos_validos = df.loc[
        df["Coordenadas_validas"], ["_row_id", "LATITUD", "LONGITUD"]
    ].copy()
    if puntos_validos.empty:
        df["Estado_Validacion_Barrio"] = "No evaluable: sin coordenadas válidas"
        return df

    gdf_puntos = gpd.GeoDataFrame(
        puntos_validos[["_row_id"]].copy(),
        geometry=gpd.points_from_xy(
            puntos_validos["LONGITUD"], puntos_validos["LATITUD"]
        ),
        crs="EPSG:4326",
    )
    barrios_join = gdf_barrios[["NOMBRE_BARRIO_GEOJSON", "geometry"]]
    cruce_dentro = gpd.sjoin(
        gdf_puntos,
        barrios_join,
        how="left",
        predicate="within",
    )

    asignaciones: dict[int, str] = {}
    ambiguos: set[int] = set()
    ids_con_match = set()

    coincidencias_dentro = cruce_dentro.dropna(subset=["NOMBRE_BARRIO_GEOJSON"])
    for row_id, grupo in coincidencias_dentro.groupby("_row_id"):
        nombres = sorted(set(grupo["NOMBRE_BARRIO_GEOJSON"].astype(str)))
        ids_con_match.add(int(row_id))
        if len(nombres) == 1:
            asignaciones[int(row_id)] = nombres[0]
        else:
            ambiguos.add(int(row_id))

    # Los puntos no contenidos se revisan contra los bordes. Solo se acepta una
    # intersección única; más de una coincidencia queda explícitamente ambigua.
    ids_sin_match = set(puntos_validos["_row_id"].astype(int)) - ids_con_match
    if ids_sin_match:
        puntos_borde = gdf_puntos[gdf_puntos["_row_id"].isin(ids_sin_match)]
        cruce_borde = gpd.sjoin(
            puntos_borde,
            barrios_join,
            how="left",
            predicate="intersects",
        )
        coincidencias_borde = cruce_borde.dropna(subset=["NOMBRE_BARRIO_GEOJSON"])
        ids_con_interseccion = set()
        for row_id, grupo in coincidencias_borde.groupby("_row_id"):
            nombres = sorted(set(grupo["NOMBRE_BARRIO_GEOJSON"].astype(str)))
            rid = int(row_id)
            ids_con_interseccion.add(rid)
            if len(nombres) == 1:
                asignaciones[rid] = nombres[0]
            else:
                ambiguos.add(rid)
        for rid in ids_sin_match - ids_con_interseccion:
            asignaciones[rid] = ESTADO_FUERA

    for rid, nombre in asignaciones.items():
        df.loc[df["_row_id"].eq(rid), "Barrio_Geografico"] = nombre
        df.loc[df["_row_id"].eq(rid), "Estado_Asignacion_Geografica"] = (
            "Fuera de los barrios" if nombre == ESTADO_FUERA else "Asignado geográficamente"
        )
    for rid in ambiguos:
        df.loc[df["_row_id"].eq(rid), "Barrio_Geografico"] = ESTADO_AMBIGUO
        df.loc[df["_row_id"].eq(rid), "Estado_Asignacion_Geografica"] = ESTADO_AMBIGUO

    estados_validacion = []
    for _, fila in df.iterrows():
        barrio_geo = str(fila["Barrio_Geografico"])
        barrio_texto = fila.get("BARRIOS_CORREGIDO")
        normalizado_texto = normalizar_nombre_barrio(barrio_texto)
        if not bool(fila["Coordenadas_validas"]):
            estados_validacion.append("No evaluable: sin coordenadas válidas")
        elif barrio_geo == ESTADO_FUERA:
            estados_validacion.append("Punto fuera de los polígonos")
        elif barrio_geo == ESTADO_AMBIGUO:
            estados_validacion.append("Asignación espacial ambigua")
        elif not normalizado_texto:
            estados_validacion.append("Sin dato textual para verificar")
        elif normalizado_texto == normalizar_nombre_barrio(barrio_geo):
            estados_validacion.append("Coincide tras normalización")
        else:
            estados_validacion.append("Discrepancia entre texto y ubicación")
    df["Estado_Validacion_Barrio"] = estados_validacion
    return df


def leer_indec(ruta_indec: str):
    """Lee los totales del cuadro INDEC; no transforma /// ni guiones en ceros."""
    raw = pd.read_excel(ruta_indec, sheet_name=0, header=None, engine="openpyxl")
    if raw.empty or raw.shape[1] < 11:
        raise ValueError("INDEC.xlsx no tiene la estructura esperada del Cuadro 2.7.3.")

    primera_columna = raw.iloc[:, 0].astype("string").str.strip().str.casefold()
    filas_total = raw.index[primera_columna.eq("total")].tolist()
    if not filas_total:
        raise ValueError("No se encontró la fila Total en INDEC.xlsx.")
    fila_total = raw.iloc[filas_total[0]]

    valores_siete = pd.to_numeric(fila_total.iloc[4:11], errors="coerce")
    if valores_siete.isna().any():
        raise ValueError("La fila Total del INDEC no contiene números en las siete categorías.")
    total_asiste = pd.to_numeric(pd.Series([fila_total.iloc[3]]), errors="coerce").iloc[0]
    poblacion_total = pd.to_numeric(pd.Series([fila_total.iloc[2]]), errors="coerce").iloc[0]
    if pd.isna(total_asiste) or pd.isna(poblacion_total):
        raise ValueError("No se pudieron leer los totales generales del cuadro INDEC.")

    etiquetas_excel = [
        str(valor).replace("\xa0", " ").strip()
        for valor in raw.iloc[3, 4:11].tolist()
    ]
    categorias = []
    for i, (nivel, categoria) in enumerate(CATEGORIAS_INDEC.items()):
        categorias.append(
            {
                "Nivel": nivel,
                "Categoria_INDEC": categoria,
                "Etiqueta_en_archivo": etiquetas_excel[i],
                "Poblacion_INDEC": int(valores_siete.iloc[i]),
            }
        )
    df_indec = pd.DataFrame(categorias)

    suma_siete = int(valores_siete.sum())
    total_asiste = int(total_asiste)
    total_cinco = int(df_indec["Poblacion_INDEC"].sum())
    universitario = int(valores_siete.iloc[5])
    posgrado = int(valores_siete.iloc[6])
    aditividad_verificada = suma_siete == total_asiste

    notas = []
    for valor in raw.iloc[:, 0].dropna().astype(str).tolist():
        texto = valor.replace("\xa0", " ").strip()
        if texto.startswith("(1)") or texto.startswith("Nota:") or texto.startswith("Fuente:"):
            notas.append(texto)

    metadata = {
        "censo": "Censo Nacional de Población, Hogares y Viviendas 2022",
        "cuadro": "Cuadro 2.7.3",
        "jurisdiccion": "Provincia de Corrientes, departamento Capital",
        "poblacion_en_viviendas_particulares": int(poblacion_total),
        "poblacion_que_asiste_total": total_asiste,
        "suma_siete_categorias": suma_siete,
        "suma_cinco_categorias_comparables": total_cinco,
        "universitario_de_grado": universitario,
        "posgrado": posgrado,
        "aditividad_verificada": aditividad_verificada,
        "notas": notas,
        "hoja": "Primera hoja del libro",
    }
    return df_indec, metadata


def lista_de_flags(df: pd.DataFrame, mapa: dict[str, str], tipo: str) -> pd.Series:
    """Devuelve todas las banderas activas, no una clasificación exclusiva."""
    if tipo == "nivel":
        mapa_flags = FLAG_NIVELES
    else:
        mapa_flags = FLAG_MODALIDADES
    etiquetas = list(mapa.values())
    columnas = list(mapa.keys())
    listas = []
    for _, fila in df.iterrows():
        activas = []
        for columna, etiqueta in zip(columnas, etiquetas):
            flag = mapa_flags[columna]
            if bool(fila[flag]):
                activas.append(etiqueta)
        listas.append(activas)
    return pd.Series(listas, index=df.index, dtype=object)


def construir_texto_combinado(lista, alternativa: str) -> str:
    return " · ".join(lista) if lista else alternativa


def crear_paleta_combinaciones(
    categorias: Iterable[str],
    paleta_base: dict[str, str],
) -> dict[str, str]:
    """Asigna colores estables a combinaciones completas de banderas."""
    paleta = dict(paleta_base)
    colores = list(paleta_base.values())
    for i, categoria in enumerate(sorted(set(str(c) for c in categorias))):
        if categoria not in paleta:
            paleta[categoria] = colores[i % len(colores)]
    return paleta


def aplicar_filtro_modalidad(
    df: pd.DataFrame,
    modalidades_sel: list[str],
    modalidades_presentes: list[str],
) -> pd.DataFrame:
    """Unión de modalidades seleccionadas; vacío o todas equivale a sin filtro."""
    if not modalidades_sel or set(modalidades_sel) >= set(modalidades_presentes):
        return df.copy()
    mascara = pd.Series(False, index=df.index)
    for columna, etiqueta in MAPA_MODALIDADES.items():
        if etiqueta in modalidades_sel:
            mascara |= df[FLAG_MODALIDADES[columna]]
    return df.loc[mascara].copy()


def filtrar_establecimientos(
    df: pd.DataFrame,
    niveles_sel: list[str],
    modalidades_sel: list[str],
    modalidades_presentes: list[str],
    barrios_sel: list[str],
    busqueda: str = "",
    id_seleccionado=None,
    solo_seleccionado: bool = False,
) -> pd.DataFrame:
    """Aplica los filtros a una tabla única; cada fila/establecimiento aparece una vez."""
    resultado = df.copy()

    if niveles_sel:
        mascara_niveles = pd.Series(False, index=resultado.index)
        for columna, etiqueta in MAPA_NIVELES.items():
            if etiqueta in niveles_sel:
                mascara_niveles |= resultado[FLAG_NIVELES[columna]]
        resultado = resultado.loc[mascara_niveles]

    resultado = aplicar_filtro_modalidad(
        resultado, modalidades_sel, modalidades_presentes
    )

    if barrios_sel:
        resultado = resultado[resultado["Barrio_Geografico"].isin(barrios_sel)]

    termino = (busqueda or "").strip()
    if termino:
        columnas_busqueda = [
            "NOMBRE_DISPLAY",
            "ID_DISPLAY",
            "CUE_DISPLAY",
            "MDE_DISPLAY",
        ]
        mascara_busqueda = pd.Series(False, index=resultado.index)
        for columna in columnas_busqueda:
            mascara_busqueda |= resultado[columna].astype("string").str.contains(
                termino, case=False, regex=False, na=False
            )
        resultado = resultado.loc[mascara_busqueda]

    if solo_seleccionado and id_seleccionado is not None:
        resultado = resultado[resultado["_row_id"].eq(int(id_seleccionado))]

    return resultado.copy()


def preparar_geometrias_para_mapa(gdf_barrios: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Simplifica una copia para el mapa; los cálculos usan la geometría original."""
    resultado = gdf_barrios.copy()
    resultado["geometry"] = resultado.geometry.simplify(
        tolerance=0.00008,
        preserve_topology=True,
    )
    return resultado


# -----------------------------------------------------------------------------
# Carga y preparación en caché: no se recalcula con cada interacción del usuario
# -----------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def cargar_excel_original_para_descarga(
    ruta: str,
    marca_archivo: tuple[int, int],
) -> bytes:
    """Lee y cachea los bytes originales para ofrecer su descarga sin transformarlos."""
    _ = marca_archivo
    return Path(ruta).read_bytes()


@st.cache_data(show_spinner="Preparando padrón, INDEC y límites barriales...")
def cargar_datos(
    ruta_escuelas: str,
    marca_escuelas: tuple[int, int],
    ruta_indec: str,
    marca_indec: tuple[int, int],
    ruta_geojson: str,
    marca_geojson: tuple[int, int],
):
    # Las marcas hacen que la caché se invalide cuando cambia un archivo local.
    _ = marca_escuelas, marca_indec, marca_geojson

    df = pd.read_excel(ruta_escuelas, sheet_name=0, engine="openpyxl")
    df.columns = [str(col).strip() for col in df.columns]
    columnas_originales = list(df.columns)
    df = df.reset_index(drop=True)
    df["_row_id"] = range(len(df))

    columna_barrio = resolver_columna(df, ["BARRIOS_CORREGIDO"], obligatoria=True)
    columna_latitud = resolver_columna(df, ["LATITUD", "LATITUDE"], obligatoria=True)
    columna_longitud = resolver_columna(df, ["LONGITUD", "LONGITUDE", "LON"], obligatoria=True)
    columna_nombre = resolver_columna(
        df,
        ["fna", "nombre", "nombre_establecimiento", "establecimiento", "nom_est"],
        obligatoria=True,
    )
    columna_id = resolver_columna(df, ["gid", "id", "identificador"])
    columna_cue = resolver_columna(df, ["cue", "codigo_unico_establecimiento"])
    columna_mde = resolver_columna(df, ["mde", "modalidad_descripcion"])

    if columna_barrio != "BARRIOS_CORREGIDO":
        df["BARRIOS_CORREGIDO"] = df[columna_barrio]
    df["LATITUD"] = pd.to_numeric(df[columna_latitud], errors="coerce")
    df["LONGITUD"] = pd.to_numeric(df[columna_longitud], errors="coerce")
    df["NOMBRE_DISPLAY"] = df[columna_nombre].fillna("Sin nombre").astype(str).str.strip()
    df["ID_DISPLAY"] = (
        df[columna_id].map(formatear_identificador)
        if columna_id
        else df["_row_id"].map(lambda x: str(x + 1))
    )
    df["CUE_DISPLAY"] = (
        df[columna_cue].map(formatear_identificador)
        if columna_cue
        else "No informado"
    )
    df["MDE_DISPLAY"] = (
        df[columna_mde].fillna("Sin descripción").astype(str).str.strip()
        if columna_mde
        else "Sin descripción"
    )
    df["BARRIO_TEXTO_DISPLAY"] = (
        df["BARRIOS_CORREGIDO"].fillna("Sin dato textual").astype(str).str.strip()
    )
    df.loc[
        df["BARRIO_TEXTO_DISPLAY"].str.upper().isin(["NULL", "NONE", "NAN", ""]),
        "BARRIO_TEXTO_DISPLAY",
    ] = "Sin dato textual"

    for columna, flag in FLAG_NIVELES.items():
        df[flag] = es_uno(df[columna]) if columna in df.columns else False
    for columna, flag in FLAG_MODALIDADES.items():
        df[flag] = es_uno(df[columna]) if columna in df.columns else False

    flags_nivel = list(FLAG_NIVELES.values())
    flags_modalidad = list(FLAG_MODALIDADES.values())
    df["Tiene_nivel_de_los_cinco"] = df[flags_nivel].any(axis=1)
    df["Tiene_modalidad_identificada"] = df[flags_modalidad].any(axis=1)

    df["Niveles_Lista"] = lista_de_flags(df, MAPA_NIVELES, "nivel")
    df["Modalidades_Lista"] = lista_de_flags(df, MAPA_MODALIDADES, "modalidad")
    df["Niveles_Disponibles"] = df["Niveles_Lista"].map(
        lambda x: construir_texto_combinado(x, "Sin nivel identificado")
    )
    df["Modalidades_Disponibles"] = df["Modalidades_Lista"].map(
        lambda x: construir_texto_combinado(x, "Sin especificar")
    )
    df["Nivel_Combo"] = df["Niveles_Disponibles"]
    df["Modalidad_Combo"] = df["Modalidades_Disponibles"]

    gdf_barrios, geo_stats = obtener_geometrias_barrios(ruta_geojson)
    df = asignar_barrios_espacialmente(df, gdf_barrios)

    df_indec, indec_metadata = leer_indec(ruta_indec)

    # Control de calidad de los identificadores y de la estructura del padrón.
    duplicate_full_rows = int(df.duplicated(subset=columnas_originales).sum())
    duplicate_gid = int(df[columna_id].duplicated().sum()) if columna_id else 0
    duplicate_cue = int(df[columna_cue].duplicated().sum()) if columna_cue else 0
    nulos_por_columna = {
        str(col): int(df[col].isna().sum())
        for col in columnas_originales
        if int(df[col].isna().sum()) > 0
    }
    coordenadas_validas_df = df.loc[df["Coordenadas_validas"]]
    grupos_coordenadas = coordenadas_validas_df.groupby(["LONGITUD", "LATITUD"]).size()
    grupos_coordenadas_repetidas = grupos_coordenadas[grupos_coordenadas > 1]
    sin_nivel_por_mde = (
        df.loc[~df["Tiene_nivel_de_los_cinco"], "MDE_DISPLAY"]
        .value_counts(dropna=False)
        .to_dict()
    )

    barrios_todos = set(gdf_barrios["NOMBRE_BARRIO_GEOJSON"].astype(str))
    barrios_con_establecimientos = set(
        df.loc[
            df["Barrio_Geografico"].isin(barrios_todos), "Barrio_Geografico"
        ].astype(str)
    )
    barrios_sin_establecimientos = sorted(barrios_todos - barrios_con_establecimientos)

    geo_stats.update(
        {
            "registros_originales": len(df),
            "columnas_originales": len(columnas_originales),
            "registros_con_algun_nivel": int(df["Tiene_nivel_de_los_cinco"].sum()),
            "registros_sin_banderas_de_nivel": int((~df["Tiene_nivel_de_los_cinco"]).sum()),
            "registros_con_modalidad_identificada": int(df["Tiene_modalidad_identificada"].sum()),
            "registros_sin_modalidad_identificada": int((~df["Tiene_modalidad_identificada"]).sum()),
            "registros_con_coordenadas_validas": int(df["Coordenadas_validas"].sum()),
            "registros_con_coordenadas_invalidas": int((~df["Coordenadas_validas"]).sum()),
            "registros_asignados_a_barrio": int(df["Barrio_Geografico"].isin(barrios_todos).sum()),
            "registros_fuera_de_barrios": int(df["Barrio_Geografico"].eq(ESTADO_FUERA).sum()),
            "registros_asignacion_ambigua": int(df["Barrio_Geografico"].eq(ESTADO_AMBIGUO).sum()),
            "barrios_con_establecimientos": len(barrios_con_establecimientos),
            "barrios_sin_establecimientos": len(barrios_sin_establecimientos),
            "barrios_sin_establecimientos_lista": barrios_sin_establecimientos,
            "registros_con_barrio_textual_null": int(
                (
                    df["BARRIOS_CORREGIDO"].isna()
                    | df["BARRIOS_CORREGIDO"].astype("string").str.strip().str.upper().isin(
                        ["NULL", "NONE", "NAN", ""]
                    )
                ).sum()
            ),
            "discrepancias_texto_geografia": int(
                df["Estado_Validacion_Barrio"].eq("Discrepancia entre texto y ubicación").sum()
            ),
            "coordenadas_aproximadas": None,
            "campo_pre_disponible": "pre" in df.columns,
            "filas_completamente_duplicadas": duplicate_full_rows,
            "identificadores_gid_duplicados": duplicate_gid,
            "identificadores_cue_duplicados": duplicate_cue,
            "valores_nulos_por_columna_original": nulos_por_columna,
            "pares_coordenadas_unicos": int(len(grupos_coordenadas)),
            "grupos_coordenadas_repetidas": int(len(grupos_coordenadas_repetidas)),
            "registros_en_pares_coordenadas_repetidas": int(grupos_coordenadas_repetidas.sum()),
            "maximo_registros_misma_coordenada": int(grupos_coordenadas.max()) if len(grupos_coordenadas) else 0,
            "registros_sin_nivel_por_mde": {
                str(k): int(v) for k, v in sin_nivel_por_mde.items()
            },
            "conteos_por_nivel": {
                etiqueta: int(df[FLAG_NIVELES[columna]].sum())
                for columna, etiqueta in MAPA_NIVELES.items()
            },
            "conteos_por_modalidad": {
                etiqueta: int(df[FLAG_MODALIDADES[columna]].sum())
                for columna, etiqueta in MAPA_MODALIDADES.items()
            },
            "registros_con_multiples_niveles": int(
                df["Niveles_Lista"].map(len).gt(1).sum()
            ),
        }
    )

    # La versión de mapa se prepara una sola vez; los cruces siempre usan la original.
    gdf_barrios_plot = preparar_geometrias_para_mapa(gdf_barrios)
    return df, gdf_barrios, gdf_barrios_plot, df_indec, indec_metadata, geo_stats


# -----------------------------------------------------------------------------
# Comprobación de archivos requeridos y carga
# -----------------------------------------------------------------------------
archivos_requeridos = [
    (ARCHIVO_ESCUELAS, "ESCUELAS_DEFINITIVA.xlsx"),
    (ARCHIVO_INDEC, "INDEC.xlsx"),
    (ARCHIVO_BARRIOS, "BarriosCorrientes.geojson"),
]
archivos_faltantes = [nombre for ruta, nombre in archivos_requeridos if not ruta.exists()]
if archivos_faltantes:
    st.error(
        "Faltan archivos necesarios junto a app.py: " + ", ".join(archivos_faltantes)
    )
    st.stop()

marca_escuelas = (ARCHIVO_ESCUELAS.stat().st_mtime_ns, ARCHIVO_ESCUELAS.stat().st_size)
marca_indec = (ARCHIVO_INDEC.stat().st_mtime_ns, ARCHIVO_INDEC.stat().st_size)
marca_geojson = (ARCHIVO_BARRIOS.stat().st_mtime_ns, ARCHIVO_BARRIOS.stat().st_size)

try:
    (
        df_escuelas,
        gdf_barrios,
        gdf_barrios_plot,
        df_indec,
        indec_metadata,
        calidad,
    ) = cargar_datos(
        str(ARCHIVO_ESCUELAS),
        marca_escuelas,
        str(ARCHIVO_INDEC),
        marca_indec,
        str(ARCHIVO_BARRIOS),
        marca_geojson,
    )
except Exception as error:
    st.error(f"No se pudieron preparar los datos: {error}")
    st.stop()

barrios_geograficos = gdf_barrios["NOMBRE_BARRIO_GEOJSON"].astype(str).tolist()
barrios_geograficos_opciones = barrios_geograficos + [ESTADO_FUERA, ESTADO_SIN_COORDENADAS, ESTADO_AMBIGUO]
modalidades_presentes = [
    etiqueta
    for etiqueta in ORDEN_MODALIDADES
    if int(calidad["conteos_por_modalidad"].get(etiqueta, 0)) > 0
]


# -----------------------------------------------------------------------------
# 1. Filtros
# -----------------------------------------------------------------------------
st.sidebar.title("Filtros del tablero")

barrios_sel = st.sidebar.multiselect(
    "Barrio según ubicación geográfica:",
    options=barrios_geograficos_opciones,
    default=[],
    placeholder="Todos los barrios y estados de ubicación",
    help="La asignación se realiza por coordenadas dentro de los polígonos; el texto del padrón solo se usa para verificar.",
)

niveles_sel = st.sidebar.multiselect(
    "Filtrar por niveles educativos:",
    options=ORDEN_NIVELES,
    default=[],
    placeholder="Sin filtro de nivel (incluye registros sin nivel identificado)",
    help="Un establecimiento puede ofrecer más de un nivel. La selección combina los niveles con lógica OR y cada registro se muestra una sola vez.",
)

modalidades_sel = st.sidebar.multiselect(
    "Filtrar por modalidades:",
    options=modalidades_presentes,
    default=[],
    placeholder="Todas las modalidades",
    help="Sin selección se incluyen todas las modalidades, también los registros sin bandera de modalidad. 'Sin especificar' no es una opción seleccionable.",
)

busqueda = st.sidebar.text_input(
    "Buscar por nombre, ID o CUE:",
    value="",
    placeholder="Escribí parte del nombre, ID o CUE",
)

# El selector se alimenta de la misma tabla filtrada; no es una selección desde el mapa.
df_para_selector = filtrar_establecimientos(
    df_escuelas,
    niveles_sel,
    modalidades_sel,
    modalidades_presentes,
    barrios_sel,
    busqueda,
)

opciones_ids = [None] + df_para_selector["_row_id"].astype(int).tolist()
labels_establecimientos = {
    int(fila["_row_id"]): (
        f"{fila['NOMBRE_DISPLAY']} | ID {fila['ID_DISPLAY']} | CUE {fila['CUE_DISPLAY']}"
    )
    for _, fila in df_para_selector.iterrows()
}
establecimiento_sel = st.sidebar.selectbox(
    "Establecimiento encontrado:",
    options=opciones_ids,
    index=0,
    format_func=lambda valor: "No seleccionar" if valor is None else labels_establecimientos.get(valor, str(valor)),
)
solo_establecimiento = st.sidebar.checkbox(
    "Mostrar solamente el establecimiento seleccionado",
    value=False,
)

if solo_establecimiento and establecimiento_sel is None:
    st.sidebar.caption("Elegí un establecimiento para activar la vista individual.")

df_filtrado = filtrar_establecimientos(
    df_escuelas,
    niveles_sel,
    modalidades_sel,
    modalidades_presentes,
    barrios_sel,
    busqueda,
    id_seleccionado=establecimiento_sel,
    solo_seleccionado=solo_establecimiento,
)

# Los indicadores INDEC se guían solo por el filtro de nivel. Nunca se reducen por
# barrio, modalidad o búsqueda porque el numerador corresponde al departamento Capital.
niveles_para_indec = niveles_sel if niveles_sel else ORDEN_NIVELES
modalidad_restringida = bool(modalidades_sel) and set(modalidades_sel) < set(modalidades_presentes)
barrios_restringidos = bool(barrios_sel) and set(barrios_sel) < set(barrios_geograficos_opciones)
filtro_ratio_restringido = barrios_restringidos or modalidad_restringida or bool(busqueda.strip()) or (
    solo_establecimiento and establecimiento_sel is not None
)


# -----------------------------------------------------------------------------
# 2. Resúmenes e indicadores derivados
# -----------------------------------------------------------------------------
def calcular_resumen_niveles(df: pd.DataFrame) -> pd.DataFrame:
    filas = []
    for columna, etiqueta in MAPA_NIVELES.items():
        filas.append({"Nivel": etiqueta, "Establecimientos": int(df[FLAG_NIVELES[columna]].sum())})
    return pd.DataFrame(filas)


def calcular_resumen_modalidades(df: pd.DataFrame) -> pd.DataFrame:
    filas = []
    for columna, etiqueta in MAPA_MODALIDADES.items():
        filas.append({"Modalidad": etiqueta, "Establecimientos": int(df[FLAG_MODALIDADES[columna]].sum())})
    sin_especificar = int((~df["Tiene_modalidad_identificada"]).sum())
    filas.append({"Modalidad": "Sin especificar", "Establecimientos": sin_especificar})
    return pd.DataFrame(filas)


def calcular_barrios_sin_nivel(
    df_completo: pd.DataFrame,
    gdf_completo: gpd.GeoDataFrame,
    niveles: list[str],
    modalidades: list[str],
    modalidades_opciones: list[str],
) -> pd.DataFrame:
    """Cuenta barrios sin registros para cada nivel, sin aplicar filtro de barrio/búsqueda."""
    df_modalidad = aplicar_filtro_modalidad(
        df_completo, modalidades, modalidades_opciones
    )
    nombres_barrios = set(gdf_completo["NOMBRE_BARRIO_GEOJSON"].astype(str))
    filas = []
    for nivel in niveles:
        columna = next(col for col, etiqueta in MAPA_NIVELES.items() if etiqueta == nivel)
        df_nivel = df_modalidad[df_modalidad[FLAG_NIVELES[columna]]]
        barrios_con_nivel = set(
            df_nivel.loc[
                df_nivel["Barrio_Geografico"].isin(nombres_barrios),
                "Barrio_Geografico",
            ].astype(str)
        )
        sin_nivel = sorted(nombres_barrios - barrios_con_nivel)
        filas.append(
            {
                "Nivel": nivel,
                "Cantidad_barrios_sin_registros": len(sin_nivel),
                "Barrios": sin_nivel,
            }
        )
    return pd.DataFrame(filas)


# Indicador estático: se calcula con todo el padrón, no con df_filtrado.
barrios_todos = set(gdf_barrios["NOMBRE_BARRIO_GEOJSON"].astype(str))
barrios_con_establecimientos = set(
    df_escuelas.loc[
        df_escuelas["Barrio_Geografico"].isin(barrios_todos), "Barrio_Geografico"
    ].astype(str)
)
nombres_barrios_sin_establecimientos = sorted(barrios_todos - barrios_con_establecimientos)
gdf_barrios_sin_establecimientos = gdf_barrios_plot[
    gdf_barrios_plot["NOMBRE_BARRIO_GEOJSON"].isin(nombres_barrios_sin_establecimientos)
].copy()

# Indicador dinámico: si no se seleccionó nivel, se muestran los cinco por separado.
niveles_para_indicador = niveles_sel if niveles_sel else ORDEN_NIVELES
df_barrios_sin_nivel = calcular_barrios_sin_nivel(
    df_escuelas,
    gdf_barrios,
    niveles_para_indicador,
    modalidades_sel,
    modalidades_presentes,
)

# Resumen INDEC para los niveles seleccionados; categorías aditivas verificadas en el total.
df_indec_seleccionado = df_indec[df_indec["Nivel"].isin(niveles_para_indec)].copy()
df_indec_seleccionado["Establecimientos_que_ofrecen"] = df_indec_seleccionado["Nivel"].map(
    calcular_resumen_niveles(df_escuelas).set_index("Nivel")["Establecimientos"]
)
df_indec_seleccionado["Personas_por_establecimiento_ofertante"] = pd.NA
if not filtro_ratio_restringido:
    denominador_valido = df_indec_seleccionado["Establecimientos_que_ofrecen"].gt(0)
    df_indec_seleccionado.loc[denominador_valido, "Personas_por_establecimiento_ofertante"] = (
        df_indec_seleccionado.loc[denominador_valido, "Poblacion_INDEC"]
        / df_indec_seleccionado.loc[denominador_valido, "Establecimientos_que_ofrecen"]
    ).round(1)

poblacion_seleccionada_indec = (
    int(df_indec_seleccionado["Poblacion_INDEC"].sum())
    if indec_metadata["aditividad_verificada"]
    else None
)
if not df_indec_seleccionado.empty:
    fila_mayor_demanda = df_indec_seleccionado.loc[
        df_indec_seleccionado["Poblacion_INDEC"].idxmax()
    ]
    mayor_demanda_texto = (
        f"{fila_mayor_demanda['Nivel']} ({formato_entero(fila_mayor_demanda['Poblacion_INDEC'])})"
    )
else:
    mayor_demanda_texto = "Sin datos"

# Concentración geográfica: los registros fuera de los polígonos y sin coordenadas
# permanecen en el padrón, pero no se asignan artificialmente a un barrio.
conteo_geografico = df_filtrado["Barrio_Geografico"].value_counts()
if barrios_sel:
    barrios_para_grafico = [b for b in barrios_sel if b in barrios_todos]
else:
    barrios_para_grafico = barrios_geograficos

df_concentracion = pd.DataFrame({"Barrio": barrios_para_grafico})
df_concentracion["Registros"] = df_concentracion["Barrio"].map(conteo_geografico).fillna(0).astype(int)
df_concentracion = df_concentracion.sort_values(
    ["Registros", "Barrio"], ascending=[False, True]
)
registros_fuera_filtrados = int(df_filtrado["Barrio_Geografico"].eq(ESTADO_FUERA).sum())
registros_sin_coord_filtrados = int(df_filtrado["Barrio_Geografico"].eq(ESTADO_SIN_COORDENADAS).sum())


# -----------------------------------------------------------------------------
# 3. Título e indicadores superiores
# -----------------------------------------------------------------------------
st.title("Establecimientos Educativos en Corrientes - INDEC vs Oferta")

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric(
    "Establecimientos filtrados",
    formato_entero(len(df_filtrado)),
    help="Cada registro del padrón se cuenta una sola vez, aunque ofrezca varios niveles o modalidades.",
)
k2.metric(
    "Población que asiste según INDEC",
    formato_entero(poblacion_seleccionada_indec),
    help="Suma de las categorías INDEC correspondientes a los niveles seleccionados. Corresponde al departamento Capital y no cambia por barrio ni modalidad.",
)
k3.metric(
    "Nivel con mayor asistencia",
    mayor_demanda_texto,
    help="Se calcula únicamente entre los niveles incluidos en el filtro educativo.",
)
barrios_conteo_filtrado = df_filtrado.loc[
    df_filtrado["Barrio_Geografico"].isin(barrios_todos), "Barrio_Geografico"
].value_counts()
if not barrios_conteo_filtrado.empty:
    barrio_mayor = str(barrios_conteo_filtrado.index[0])
    cantidad_barrio_mayor = int(barrios_conteo_filtrado.iloc[0])
    valor_barrio_mayor = f"{barrio_mayor} ({formato_entero(cantidad_barrio_mayor)})"
else:
    valor_barrio_mayor = "Sin datos asignados"
k4.metric(
    "Mayor concentración geográfica",
    valor_barrio_mayor,
    help="Barrio con más registros del subconjunto filtrado cuya coordenada cae dentro de un polígono.",
)
k5.metric(
    "Barrios sin establecimientos registrados",
    formato_entero(len(nombres_barrios_sin_establecimientos)),
    help=f"Indicador estático calculado con los {formato_entero(calidad['registros_originales'])} registros del padrón, sin aplicar filtros.",
)

st.markdown("---")


# -----------------------------------------------------------------------------
# 4. Barrios sin registros para el nivel seleccionado (indicador dinámico)
# -----------------------------------------------------------------------------
st.subheader("Barrios sin establecimientos registrados para cada nivel")
if modalidad_restringida:
    st.caption(
        "Este cálculo representa ausencia de registros para la combinación de nivel y modalidad seleccionada. "
        "Se consideran todos los barrios y se ignoran los filtros de barrio y búsqueda."
    )
else:
    st.caption(
        "Se consideran todos los barrios y todas las modalidades, incluidos los registros sin modalidad identificada. "
        "El resultado no cambia al filtrar un barrio o buscar un establecimiento."
    )

columnas_indicador = st.columns(max(1, len(df_barrios_sin_nivel)))
for columna_ui, (_, fila_indicador) in zip(columnas_indicador, df_barrios_sin_nivel.iterrows()):
    columna_ui.metric(
        f"Sin registros: {fila_indicador['Nivel']}",
        formato_entero(fila_indicador["Cantidad_barrios_sin_registros"]),
    )
with st.expander("Ver nombres de barrios sin registros por nivel"):
    for _, fila_indicador in df_barrios_sin_nivel.iterrows():
        st.markdown(f"**{fila_indicador['Nivel']}**")
        if fila_indicador["Barrios"]:
            st.write(", ".join(fila_indicador["Barrios"]))
        else:
            st.write("Todos los barrios tienen al menos un registro para esta combinación.")

st.markdown("---")


# -----------------------------------------------------------------------------
# 5. Mapa y gráficos
# -----------------------------------------------------------------------------
col_mapa, col_grafico = st.columns([1.1, 0.9])

with col_mapa:
    st.subheader("Distribución territorial de establecimientos")
    capas1, capas2, capas3, capas4 = st.columns(4)
    ver_barrios = capas1.checkbox("Límites de barrios", value=True)
    ver_sin_establecimientos = capas2.checkbox(
        "Destacar sin establecimientos", value=True
    )
    ver_establecimientos = capas3.checkbox("Ver establecimientos", value=True)
    criterio_color = capas4.radio(
        "Colorear por:",
        ["Combinación de niveles", "Combinación de modalidades"],
        horizontal=True,
    )

    df_puntos = df_filtrado[
        df_filtrado["Coordenadas_validas"]
    ].dropna(subset=["LATITUD", "LONGITUD"]).copy()

    centro = dict(CENTRO_CORRIENTES)
    zoom_val = ZOOM_INICIAL
    puntos_en_barrios_sel = df_puntos[
        df_puntos["Barrio_Geografico"].isin(barrios_geograficos)
    ]
    if barrios_sel and not puntos_en_barrios_sel.empty:
        centro = {
            "lat": float(puntos_en_barrios_sel["LATITUD"].mean()),
            "lon": float(puntos_en_barrios_sel["LONGITUD"].mean()),
        }
        zoom_val = 12.8 if len(barrios_sel) > 1 else 13.5
    elif not df_puntos.empty and all(b in [ESTADO_FUERA, ESTADO_SIN_COORDENADAS, ESTADO_AMBIGUO] for b in barrios_sel):
        centro = {
            "lat": float(df_puntos["LATITUD"].mean()),
            "lon": float(df_puntos["LONGITUD"].mean()),
        }

    if criterio_color == "Combinación de niveles":
        campo_color = "Nivel_Combo"
        titulo_color = "Combinación de niveles"
        paleta_base = COLORES_NIVELES
    else:
        campo_color = "Modalidad_Combo"
        titulo_color = "Combinación de modalidades"
        paleta_base = COLORES_MODALIDADES
    paleta_actual = crear_paleta_combinaciones(
        df_escuelas[campo_color].dropna().astype(str).tolist(), paleta_base
    )

    clase_traza_mapa = getattr(go, "Scattermap", None)
    usa_mapa_moderno = clase_traza_mapa is not None
    if clase_traza_mapa is None:
        clase_traza_mapa = getattr(go, "Scattermapbox", None)

    fig_mapa = go.Figure()

    # Primera capa: relleno rojo estático calculado con el padrón completo.
    if (
        ver_sin_establecimientos
        and not gdf_barrios_sin_establecimientos.empty
        and clase_traza_mapa is not None
    ):
        longitudes_rojas = []
        latitudes_rojas = []
        for geometria in gdf_barrios_sin_establecimientos.geometry:
            for poligono in extraer_poligonos(geometria):
                x, y = poligono.exterior.xy
                longitudes_rojas.extend(list(x) + [None])
                latitudes_rojas.extend(list(y) + [None])
        fig_mapa.add_trace(
            clase_traza_mapa(
                lon=longitudes_rojas,
                lat=latitudes_rojas,
                mode="lines",
                fill="toself",
                fillcolor="rgba(239, 68, 68, 0.22)",
                line={"width": 0.1, "color": "rgba(239, 68, 68, 0)"},
                name="Barrios sin establecimientos registrados",
                showlegend=False,
                hoverinfo="skip",
            )
        )

    # Segunda capa: todos los contornos barriales, también sobre los rellenos rojos.
    if ver_barrios and clase_traza_mapa is not None:
        longitudes_limites = []
        latitudes_limites = []
        for geometria in gdf_barrios_plot.geometry:
            for poligono in extraer_poligonos(geometria):
                x, y = poligono.exterior.xy
                longitudes_limites.extend(list(x) + [None])
                latitudes_limites.extend(list(y) + [None])
                for anillo in poligono.interiors:
                    xi, yi = anillo.xy
                    longitudes_limites.extend(list(xi) + [None])
                    latitudes_limites.extend(list(yi) + [None])
        fig_mapa.add_trace(
            clase_traza_mapa(
                lon=longitudes_limites,
                lat=latitudes_limites,
                mode="lines",
                line={"width": 2.0, "color": COLOR_CONTORNO_BARRIOS},
                name="Límites de barrios",
                showlegend=False,
                hoverinfo="skip",
            )
        )

    # Última capa: un punto por registro, sin duplicarlo por sus múltiples niveles.
    if ver_establecimientos and not df_puntos.empty:
        argumentos_mapa = {
            "data_frame": df_puntos,
            "lat": "LATITUD",
            "lon": "LONGITUD",
            "color": campo_color,
            "color_discrete_map": paleta_actual,
            "hover_name": "NOMBRE_DISPLAY",
            "custom_data": [
                "ID_DISPLAY",
                "CUE_DISPLAY",
                "Barrio_Geografico",
                "BARRIO_TEXTO_DISPLAY",
                "Niveles_Disponibles",
                "Modalidades_Disponibles",
            ],
            "center": centro,
            "zoom": zoom_val,
            "height": 560,
            "labels": {campo_color: titulo_color},
        }
        if hasattr(px, "scatter_map"):
            fig_puntos = px.scatter_map(
                **argumentos_mapa,
                map_style="open-street-map",
            )
        else:
            fig_puntos = px.scatter_mapbox(
                **argumentos_mapa,
                mapbox_style="open-street-map",
            )

        plantilla_hover = (
            "<b>%{hovertext}</b>"
            "<br><b>ID:</b> %{customdata[0]}"
            "<br><b>CUE:</b> %{customdata[1]}"
            "<br><b>Barrio geográfico:</b> %{customdata[2]}"
            "<br><b>Barrio textual (verificación):</b> %{customdata[3]}"
            "<br><b>Todos los niveles:</b> %{customdata[4]}"
            "<br><b>Todas las modalidades:</b> %{customdata[5]}"
            "<extra></extra>"
        )
        for traza in fig_puntos.data:
            traza.update(
                hovertemplate=plantilla_hover,
                marker={"size": 10, "opacity": 0.92},
            )
            fig_mapa.add_trace(traza)

    if usa_mapa_moderno:
        fig_mapa.update_layout(
            map={
                "style": "open-street-map",
                "center": {"lat": centro["lat"], "lon": centro["lon"]},
                "zoom": zoom_val,
            }
        )
    else:
        fig_mapa.update_layout(
            mapbox={
                "style": "open-street-map",
                "center": {"lat": centro["lat"], "lon": centro["lon"]},
                "zoom": zoom_val,
            }
        )

    fig_mapa.update_layout(
        template="plotly_dark",
        margin={"l": 0, "r": 0, "t": 12, "b": 0},
        height=560,
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
            "font": {"color": COLOR_TEXTO, "size": 10},
            "bgcolor": "rgba(11, 18, 32, 0.72)",
        },
        hovermode="closest",
        font={"color": COLOR_TEXTO},
    )
    st.plotly_chart(
        fig_mapa,
        use_container_width=True,
        config={"scrollZoom": True, "displayModeBar": False},
        key="mapa_interactivo",
    )
    st.caption(
        "Los polígonos rojos son estáticos: barrios sin puntos asignados en el padrón completo. "
        "Los contornos azules representan los límites de los barrios. Los puntos fuera de los polígonos "
        "se conservan y se identifican como tales en el mapa. El mapa base callejero solo aporta contexto visual."
    )

with col_grafico:
    st.subheader("Concentración (registros por barrio)")
    vista_concentracion = st.selectbox(
        "Barrios a mostrar:",
        options=["Top 15", "Todos"],
        index=0,
        key="vista_concentracion_barrios",
        help="Top 15 muestra los barrios con más registros. Todos muestra el conjunto completo incluido en el filtro.",
    )
    df_concentracion_visible = (
        df_concentracion.head(15).copy()
        if vista_concentracion == "Top 15"
        else df_concentracion.copy()
    )
    if not df_concentracion_visible.empty:
        altura_concentracion = max(
            520,
            min(3000, 18 * len(df_concentracion_visible) + 140),
        )
        fig_concentracion = px.bar(
            df_concentracion_visible,
            x="Registros",
            y="Barrio",
            orientation="h",
            text="Registros",
            color_discrete_sequence=[COLOR_CELESTE],
        )
        fig_concentracion.update_layout(
            template="plotly_dark",
            yaxis={"autorange": "reversed"},
            margin={"l": 0, "r": 10, "t": 10, "b": 10},
            xaxis_title="Registros del padrón",
            yaxis_title="",
            height=altura_concentracion,
            font={"color": COLOR_TEXTO},
        )
        fig_concentracion.update_traces(textposition="outside", cliponaxis=False)
        st.plotly_chart(fig_concentracion, use_container_width=True)
    else:
        st.info("Los filtros seleccionados no corresponden a un barrio del GeoJSON.")
    texto_vista = (
        f"Se muestran {formato_entero(len(df_concentracion_visible))} barrios en la vista {vista_concentracion}. "
        if not df_concentracion_visible.empty
        else "No hay barrios para mostrar con la selección actual. "
    )
    st.caption(
        texto_vista
        + f"Cuando el filtro de barrio está en Todos, el conjunto incluye los {formato_entero(len(barrios_geograficos))} "
        "barrios del GeoJSON, también aquellos con cero registros asignados. "
        f"En el subconjunto actual hay {formato_entero(registros_fuera_filtrados)} puntos fuera de polígonos "
        f"y {formato_entero(registros_sin_coord_filtrados)} registros sin coordenadas válidas; no se suman a ningún barrio."
    )

st.markdown("---")


# -----------------------------------------------------------------------------
# 6. Conteos por nivel/modalidad y comparación descriptiva con INDEC
# -----------------------------------------------------------------------------
col_oferta, col_indec = st.columns([0.9, 1.1])
with col_oferta:
    if criterio_color == "Combinación de niveles":
        st.subheader("Establecimientos que ofrecen cada nivel")
        df_distribucion = calcular_resumen_niveles(df_filtrado)
        fig_oferta = px.bar(
            df_distribucion,
            x="Nivel",
            y="Establecimientos",
            text="Establecimientos",
            color="Nivel",
            color_discrete_map=COLORES_NIVELES,
        )
        fig_oferta.update_layout(
            template="plotly_dark",
            margin={"l": 0, "r": 10, "t": 10, "b": 10},
            height=360,
            yaxis_title="Establecimientos ofertantes registrados",
            xaxis_title="",
            font={"color": COLOR_TEXTO},
            showlegend=False,
        )
        fig_oferta.update_traces(textposition="outside", cliponaxis=False)
        st.plotly_chart(fig_oferta, use_container_width=True)
        st.caption(
            "Los conteos por nivel se superponen: un mismo establecimiento puede aparecer en más de una barra. "
            "Cada barra cuenta una vez a cada establecimiento que ofrece ese nivel."
        )
    else:
        st.subheader("Establecimientos por modalidad registrada")
        df_distribucion = calcular_resumen_modalidades(df_filtrado)
        fig_oferta = px.bar(
            df_distribucion,
            x="Establecimientos",
            y="Modalidad",
            orientation="h",
            text="Establecimientos",
            color="Modalidad",
            color_discrete_map=COLORES_MODALIDADES,
        )
        fig_oferta.update_layout(
            template="plotly_dark",
            margin={"l": 0, "r": 10, "t": 10, "b": 10},
            height=420,
            xaxis_title="Establecimientos con la modalidad",
            yaxis_title="",
            font={"color": COLOR_TEXTO},
            showlegend=False,
        )
        fig_oferta.update_traces(textposition="outside", cliponaxis=False)
        st.plotly_chart(fig_oferta, use_container_width=True)
        st.caption(
            "Las modalidades pueden superponerse. 'Sin especificar' reúne los registros que no tienen "
            "ninguna de las ocho banderas de modalidad activas; no se reclasifican como Común."
        )

with col_indec:
    st.subheader("Población que asiste según INDEC y oferta registrada")
    df_tabla_indec = df_indec_seleccionado.copy()
    df_tabla_indec["Poblacion_INDEC"] = df_tabla_indec["Poblacion_INDEC"].map(formato_entero)
    df_tabla_indec["Establecimientos_que_ofrecen"] = df_tabla_indec["Establecimientos_que_ofrecen"].map(formato_entero)
    df_tabla_indec["Personas_por_establecimiento_ofertante"] = (
        df_tabla_indec["Personas_por_establecimiento_ofertante"].map(
            lambda x: formato_decimal(x, 1) if not pd.isna(x) else "No comparable"
        )
    )
    df_tabla_indec = df_tabla_indec.rename(
        columns={
            "Nivel": "Nivel educativo",
            "Poblacion_INDEC": "Población que asiste según INDEC",
            "Establecimientos_que_ofrecen": "Establecimientos que ofrecen el nivel",
            "Personas_por_establecimiento_ofertante": "Personas por establecimiento ofertante registrado",
        }
    )
    st.dataframe(df_tabla_indec, use_container_width=True, hide_index=True)
    if filtro_ratio_restringido:
        st.info(
            "La población INDEC sigue representando al departamento Capital. Al seleccionar barrio, "
            "modalidad o una búsqueda individual, no se presenta el ratio como comparable para ese subconjunto."
        )
    else:
        st.caption(
            "El ratio es población departamental que asiste según INDEC dividida por establecimientos "
            "del padrón que ofrecen ese nivel. Es descriptivo: no representa matrícula real de cada escuela."
        )
    if indec_metadata["aditividad_verificada"]:
        st.caption(
            "La suma de las categorías seleccionadas es válida según el cuadro: los siete niveles de la fila Total "
            "reproducen exactamente el total de población que asiste. Universidad y Posgrado se excluyen porque "
            "el padrón no ofrece categorías independientes equivalentes."
        )
    else:
        st.warning(
            "La suma de categorías no coincide con el total de asistencia del cuadro; se muestran valores por nivel "
            "y no se agrega la población seleccionada."
        )

st.markdown("---")


# -----------------------------------------------------------------------------
# 7. Descarga de las bases Excel originales
# -----------------------------------------------------------------------------
st.subheader("Descargar bases originales")
st.caption(
    "Descargá los archivos Excel originales completos. Las descargas no aplican filtros "
    "ni modifican los datos de origen."
)
archivo_escuelas_descarga = cargar_excel_original_para_descarga(
    str(ARCHIVO_ESCUELAS), marca_escuelas
)
archivo_indec_descarga = cargar_excel_original_para_descarga(
    str(ARCHIVO_INDEC), marca_indec
)
col_descarga_escuelas, col_descarga_indec = st.columns(2)
with col_descarga_escuelas:
    st.download_button(
        "Descargar ESCUELAS_DEFINITIVA.xlsx",
        data=archivo_escuelas_descarga,
        file_name="ESCUELAS_DEFINITIVA.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key="descargar_padron_original",
    )
with col_descarga_indec:
    st.download_button(
        "Descargar INDEC.xlsx",
        data=archivo_indec_descarga,
        file_name="INDEC.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        key="descargar_indec_original",
    )


# -----------------------------------------------------------------------------
# 8. Metodología y fuentes
# -----------------------------------------------------------------------------
st.markdown("---")
with st.expander("Metodología y fuentes"):
    st.markdown(
        f"""
### 1. Fuentes y años de referencia

Fuentes utilizadas: **Padrón de establecimientos:** archivo `ESCUELAS_DEFINITIVA.xlsx`. **Asistencia educativa:** INDEC, Censo 2022, Cuadro 2.7.3, departamento Capital. **Límites barriales:** `BarriosCorrientes.geojson`.

- **Padrón de establecimientos:** `ESCUELAS_DEFINITIVA.xlsx`, hoja 1, con {formato_entero(calidad['registros_originales'])} registros y {formato_entero(calidad['columnas_originales'])} columnas. La devolución docente identifica la fuente como el Mapa Educativo Nacional publicado en el portal municipal. La misma devolución señala febrero de 2021 como última actualización del archivo publicado; esa fecha no está almacenada como metadato dentro del Excel y se informa como referencia documental.
- **Asistencia educativa:** `INDEC.xlsx`, Censo Nacional de Población, Hogares y Viviendas 2022, Resultados definitivos, Cuadro 2.7.3, Provincia de Corrientes, departamento Capital. El cuadro refiere a población en viviendas particulares. La fila Total informa {formato_entero(indec_metadata['poblacion_en_viviendas_particulares'])} personas en viviendas particulares y {formato_entero(indec_metadata['poblacion_que_asiste_total'])} que asisten a algún establecimiento educativo.
- **Límites geográficos:** `BarriosCorrientes.geojson`. Se utiliza para la asignación espacial y la representación de los polígonos; no aporta población ni otros indicadores estadísticos.
- **Mapa base:** las calles y referencias urbanas se muestran mediante teselas de OpenStreetMap, únicamente como fondo cartográfico de orientación. No se utilizan para asignar barrios, calcular indicadores ni incorporar estadísticas al análisis. Para ver este fondo se necesita conexión a Internet.

### 2. Registros originales, inclusión y exclusión

- El padrón contiene **{formato_entero(calidad['registros_originales'])} registros originales**. No se eliminan filas por falta de nivel, barrio o coordenadas.
- **{formato_entero(calidad['registros_con_algun_nivel'])} registros** tienen al menos una de las cinco banderas de nivel analizadas. **{formato_entero(calidad['registros_sin_banderas_de_nivel'])} registros** no tienen ninguna de esas cinco banderas y permanecen disponibles cuando no se aplica un filtro por nivel.
- Entre los registros sin una bandera de los cinco niveles, el campo descriptivo `mde` presenta esta distribución: {"; ".join(f"{formato_entero(cantidad)} con descripción `{motivo}`" for motivo, cantidad in calidad['registros_sin_nivel_por_mde'].items())}. El Excel no permite separar inequívocamente los registros técnico-profesionales entre formación profesional y un posible taller artístico; por eso no se reproduce una subdivisión 28/1 sin una variable que la respalde.
- Los conteos por nivel son **establecimientos que ofrecen el nivel**, no categorías exclusivas: {', '.join(f"{nivel}: {formato_entero(cantidad)}" for nivel, cantidad in calidad['conteos_por_nivel'].items())}. Un mismo registro puede integrar varias cifras; los totales por nivel no deben sumarse para obtener establecimientos únicos.
- Control de duplicados: {formato_entero(calidad['filas_completamente_duplicadas'])} filas completamente duplicadas, {formato_entero(calidad['identificadores_gid_duplicados'])} identificadores ID repetidos y {formato_entero(calidad['identificadores_cue_duplicados'])} CUE repetidos. Las coordenadas compartidas no se consideran duplicados de establecimiento: hay {formato_entero(calidad['grupos_coordenadas_repetidas'])} pares de coordenadas usados por más de un registro, que agrupan {formato_entero(calidad['registros_en_pares_coordenadas_repetidas'])} registros.
- {formato_entero(calidad['registros_con_multiples_niveles'])} registros ofrecen más de un nivel. {formato_entero(calidad['registros_con_modalidad_identificada'])} tienen al menos una bandera de modalidad y {formato_entero(calidad['registros_sin_modalidad_identificada'])} no tienen ninguna; estos últimos se conservan como **Sin especificar** y no se asignan a Común.

### 3. Asignación geográfica a barrios

- La asignación principal se realiza por relación espacial entre cada punto y el polígono del GeoJSON. El campo `BARRIOS_CORREGIDO` se utiliza únicamente para contrastar la asignación, nunca para reemplazarla.
- El GeoJSON contiene {formato_entero(calidad['geojson_features_originales'])} piezas y {formato_entero(calidad['barrios_unicos_geojson'])} nombres únicos. Los nombres repetidos se disuelven para representar un solo barrio sin duplicar su superficie.
- Se asignaron {formato_entero(calidad['registros_asignados_a_barrio'])} registros a {formato_entero(calidad['barrios_con_establecimientos'])} barrios. Hay {formato_entero(calidad['barrios_sin_establecimientos'])} barrios sin puntos del padrón completo. {formato_entero(calidad['registros_fuera_de_barrios'])} puntos con coordenadas válidas quedan fuera de todos los polígonos y se identifican como **Fuera de los barrios**; no se les asigna el barrio más cercano. {formato_entero(calidad['registros_con_coordenadas_invalidas'])} registros no tienen coordenadas válidas para el mapa o la unión espacial.
- Los barrios rojos se calculan con el padrón completo y muestran **barrios sin establecimientos registrados**; la capa no cambia con los filtros. El contorno azul se dibuja por encima del relleno.
- La revisión encontró {formato_entero(calidad['discrepancias_texto_geografia'])} diferencias entre el texto del barrio y la ubicación espacial, luego de normalizar prefijos y acentos; parte de ellas puede deberse a caracteres de reemplazo presentes en nombres del GeoJSON. También hay {formato_entero(calidad['registros_con_barrio_textual_null'])} registros sin dato utilizable en `BARRIOS_CORREGIDO`. La ubicación geográfica tiene prioridad; los campos textuales y geográficos se conservan por separado durante el procesamiento para permitir la auditoría, sin modificar los datos originales.
- Geometrías inválidas originales: {formato_entero(calidad['geometrias_invalidas_originales'])}. Geometrías vacías originales: {formato_entero(calidad['geometrias_vacias_originales'])}. Puntos con asignación ambigua a más de un polígono: {formato_entero(calidad['registros_asignacion_ambigua'])}.
- El padrón actual no contiene el campo `pre`; por esa razón no se calcula el porcentaje de ubicaciones aproximadas.

### 4. Asistencia INDEC y relación con la oferta

La fila Total del Cuadro 2.7.3 se lee directamente del Excel. Para las cinco categorías comparadas, los valores son:

{chr(10).join(f"- **{fila['Nivel']}:** {formato_entero(fila['Poblacion_INDEC'])}" for _, fila in df_indec.iterrows())}

La suma de estas cinco categorías es **{formato_entero(indec_metadata['suma_cinco_categorias_comparables'])}**. Universidad ({formato_entero(indec_metadata['universitario_de_grado'])}) y Posgrado ({formato_entero(indec_metadata['posgrado'])}) se excluyen de la comparación porque el padrón no contiene categorías independientes equivalentes. La suma de las siete categorías coincide con el total de asistencia ({formato_entero(indec_metadata['poblacion_que_asiste_total'])}). La nota (1) del cuadro incluye Educación Común, Educación de Jóvenes y Adultos y Educación Especial.

El indicador **Personas por establecimiento ofertante registrado** se calcula como asistencia departamental INDEC del nivel ÷ cantidad de registros del padrón que ofrecen ese nivel. Es una relación descriptiva entre dos fuentes y universos temporales distintos; **no es matrícula promedio, matrícula real, capacidad ni vacantes de cada escuela**. No se agrega un ratio entre niveles porque un mismo establecimiento puede ofrecer varios.

La población INDEC corresponde a todo el departamento Capital. Por eso no se presenta un ratio como comparable cuando se filtra por barrio, modalidad o búsqueda individual. El dato censal no se redistribuye a barrios.

### 5. Indicadores, fórmulas y criterios de cálculo

Los cálculos parten de la tabla limpia preparada desde el padrón y de las asignaciones espaciales derivadas del GeoJSON. Cada fila conserva un identificador interno de origen; los filtros no duplican un establecimiento aunque tenga varias banderas activas.

- **Establecimientos filtrados:** `N = cantidad de filas que cumplen simultáneamente los filtros activos`. Los filtros de nivel y modalidad combinan las banderas seleccionadas con lógica OR dentro de cada grupo; entre grupos se aplica lógica AND. Cada registro se cuenta una sola vez.
- **Establecimientos que ofrecen un nivel:** para cada nivel `l`, `E_l = Σ I(bandera del nivel l = 1)`, donde `I` vale 1 si la condición se cumple y 0 en caso contrario. Las cifras pueden superponerse porque un mismo establecimiento puede ofrecer más de un nivel. No se suman para calcular establecimientos únicos.
- **Establecimientos por modalidad:** para cada modalidad `m`, `M_m = Σ I(bandera de modalidad m = 1)`. Un establecimiento puede contar en más de una modalidad, por lo que estos conteos tampoco se suman para obtener establecimientos únicos. **Sin especificar** se calcula como `Σ I(ninguna de las banderas de modalidad está activa)`. Al dejar todas las modalidades sin restringir se incluyen también esos registros; al elegir modalidades concretas, los que no tienen modalidad identificada quedan fuera.
- **Concentración por barrio:** para cada barrio `b`, `C_b = Σ I(registro filtrado y barrio geográfico asignado = b)`. Se cuentan registros, no niveles ofertados. Los puntos fuera de los polígonos, ambiguos o sin coordenadas válidas no se suman a ningún barrio. La opción **Top 15** ordena por `C_b` descendente y muestra los primeros quince; **Todos** muestra todos los barrios del conjunto seleccionado, incluidos los que tienen cero registros. Este selector modifica solo la visualización, no los cálculos de los indicadores.
- **Mayor concentración geográfica:** `b* = argmax_b(C_b)`. El valor que se muestra corresponde al barrio con más registros dentro del subconjunto filtrado; si no hay registros asignados a polígonos, se informa que no hay datos asignados.
- **Barrios sin establecimientos registrados (polígonos rojos):** para cada barrio del GeoJSON se calcula `T_b = Σ I(registro de todo el padrón y barrio geográfico = b)`. Se resaltan los barrios para los que `T_b = 0`. Se utiliza el padrón completo y el resultado no cambia con filtros de nivel, modalidad, barrio o búsqueda.
- **Barrios sin registros de un nivel seleccionado:** para cada nivel `l`, se calcula `T_(b,l) = Σ I(registro asignado al barrio b y bandera del nivel l = 1)`. Se cuentan los barrios con `T_(b,l) = 0` entre todos los polígonos del GeoJSON. Si se restringe a una o varias modalidades específicas (sin seleccionar todas las opciones disponibles), el mismo cálculo se limita a registros que tengan alguna de esas modalidades. Se ignoran los filtros de barrio y búsqueda para que el resultado siempre abarque todos los barrios.
- **Población que asiste según INDEC:** para el conjunto `L` de niveles seleccionados, `P_L = Σ P_l`, donde `P_l` es el valor de la fila Total del Cuadro 2.7.3 para cada categoría comparable. Si no se selecciona un nivel, se consideran los cinco niveles comparados. La suma se presenta únicamente porque la suma de las siete categorías educativas del cuadro coincide con el total de asistencia; Universidad y Posgrado no se agregan a la comparación del padrón.
- **Nivel con mayor asistencia:** `l* = argmax_l(P_l)` dentro de los niveles incluidos en la selección. No depende del filtro de barrio ni de modalidad.
- **Personas por establecimiento ofertante registrado:** para cada nivel `l`, `R_l = P_l ÷ E_l`, donde `P_l` es la asistencia censal departamental y `E_l` es el total de registros del padrón que ofrecen ese nivel. No se calcula si `E_l = 0`. Se omite como ratio comparable cuando se restringe por barrio, modalidad o búsqueda individual, porque el numerador continúa representando a todo el departamento Capital. No se calcula un ratio agregado entre niveles.
- **Descargas:** los botones entregan los bytes originales de `ESCUELAS_DEFINITIVA.xlsx` e `INDEC.xlsx`; no exportan las tablas filtradas ni modifican los libros fuente.

### 6. Tratamiento de marcadores y limitaciones

- Los símbolos `///`, guiones y celdas vacías del detalle censal no se convierten automáticamente en cero. Los indicadores del tablero se obtienen de la fila Total, donde las categorías comparadas contienen valores numéricos.
- `INDEC.xlsx` no aporta población por barrio ni por radio censal para esta aplicación. Por lo tanto, no se calculan niños por establecimiento por barrio, escuelas por cada 1.000 niños, índice de presión relativa territorial ni barrios sin oferta condicionado a población infantil.
- Las fuentes no contienen matrícula real por establecimiento, capacidad edilicia, secciones ni vacantes. No se estima déficit de vacantes ni se afirma que la demanda censal represente alumnos matriculados en cada escuela.
- El padrón contiene {formato_entero(calidad['registros_con_coordenadas_validas'])} coordenadas válidas, pero {formato_entero(calidad['registros_fuera_de_barrios'])} puntos válidos quedan fuera del GeoJSON. Las ubicaciones fuera de polígonos y las coordenadas inválidas se conservan, se identifican y no se asignan artificialmente.
- Las geometrías se usan para localizar registros y contar cobertura del padrón; no representan por sí solas la población residente ni la accesibilidad educativa.
"""
    )

    st.markdown("**Notas de la fuente INDEC:**")
    for nota in indec_metadata["notas"]:
        st.write(nota)
