import streamlit as st
import pandas as pd
import numpy as np
import io
import gspread
from google.oauth2.service_account import Credentials

SPREADSHEET_ID = "1CFH3mzs5-rMai_1OxmeBmoSO88OAM_FJDc_Df-CVUuI"
SHEET_NAME = "produtos"

st.set_page_config(page_title="Precificação de Fretes", layout="wide")


# ── Tabela de faixas ────────────────────────────────────────────────────────

@st.cache_data
def load_faixas():
    df = pd.read_csv("tabela_faixas_frete.csv", header=1, index_col=0)
    def br_to_float(val):
        if isinstance(val, str):
            return float(val.replace(",", "."))
        return float(val)
    return df.map(br_to_float)

# ── Produtos do Google Sheets ────────────────────────────────────────────────

@st.cache_data(ttl=300)
def load_produtos():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    if "gcp_service_account" in st.secrets:
        creds = Credentials.from_service_account_info(dict(st.secrets["gcp_service_account"]), scopes=scope)
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=scope)
    client = gspread.authorize(creds)
    worksheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    df = pd.DataFrame(worksheet.get_all_records())
    for col in ["PRODUTO_ID", "PESO", "COMPRIMENTO_UNIDADE", "LARGURA_UNIDADE",
                "ALTURA_UNIDADE", "PRECO_VENDA"]:
        df[col] = (
            df[col].astype(str)
            .str.replace(r"\.(?=\d{3})", "", regex=True)  # remove ponto separador de milhar
            .str.replace(",", ".", regex=False)             # troca vírgula decimal por ponto
        )
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["VOLUME"] = (
        df["COMPRIMENTO_UNIDADE"] * df["LARGURA_UNIDADE"] * df["ALTURA_UNIDADE"]
    ) / 6000
    return df

# ── Cálculo de frete ─────────────────────────────────────────────────────────

PESO_LIMITES = [0.3, 0.5, 1, 1.5, 2, 3, 4, 5, 6, 7, 8, 9, 11, 13, 15, 17, 20,
                25, 30, 40, 50, 60, 70, 80, 90, 100, 125, 150, np.inf]
PRECO_LIMITES = [18.99, 48.99, 78.99, 99.99, 119.99, 149.99, 199.99, np.inf]

def get_faixa(valor, limites, faixas):
    for lim, faixa in zip(limites, faixas):
        if valor <= lim:
            return faixa
    return faixas[-1]

def calcular_frete(df_prod, df_faixas):
    faixas_peso = df_faixas.index.tolist()
    faixas_preco = df_faixas.columns.tolist()

    def _frete(row):
        maior = max(row["PESO"], row["VOLUME"])
        fp = get_faixa(maior, PESO_LIMITES, faixas_peso)
        fv = get_faixa(row["PRECO_VENDA"], PRECO_LIMITES, faixas_preco)
        return df_faixas.loc[fp, fv], fp, fv

    resultado = df_prod.apply(lambda r: pd.Series(_frete(r)), axis=1)
    resultado.columns = ["FRETE", "FAIXA_PESO", "FAIXA_PRECO"]
    return pd.concat([df_prod, resultado], axis=1)

# ── Carregamento ─────────────────────────────────────────────────────────────

col1, col2 = st.columns([8, 1])
with col1:
    st.title("Precificação de Fretes — Liven Marketplace")
with col2:
    if st.button("🔄 Atualizar"):
        load_produtos.clear()
        st.rerun()

df_faixas = load_faixas()

with st.spinner("Carregando produtos do Google Sheets..."):
    try:
        df_base = load_produtos()
        db_ok = True
    except Exception as e:
        st.error(f"Erro ao conectar ao banco: {e}")
        st.stop()

# ── Sidebar — filtros e ajuste de preço ─────────────────────────────────────

st.sidebar.header("Filtros")

sku_input = st.sidebar.text_input("SKU (PRODUTO_ID)", placeholder="Ex: 2292, 2294")
nome_input = st.sidebar.text_input("Nome do produto", placeholder="Busca parcial")

st.sidebar.markdown("---")
st.sidebar.header("Ajuste de Preço")

ajuste_tipo = st.sidebar.radio(
    "Tipo de ajuste",
    ["Nenhum", "Percentual (%)", "Definir preço fixo (R$)", "Importar XLSX"],
)
ajuste_valor = 0.0
if ajuste_tipo in ("Percentual (%)", "Definir preço fixo (R$)"):
    label = "Novo preço (R$)" if ajuste_tipo == "Definir preço fixo (R$)" else "Percentual (%)"
    help_text = "O PRECO_VENDA de todos os produtos filtrados será substituído por esse valor." if ajuste_tipo == "Definir preço fixo (R$)" else "Use valores negativos para reduzir o preço."
    ajuste_valor = st.sidebar.number_input(
        label,
        value=0.0,
        min_value=0.0 if ajuste_tipo == "Definir preço fixo (R$)" else None,
        step=0.5,
        format="%.2f",
        help=help_text,
    )

xlsx_precos = None
if ajuste_tipo == "Importar XLSX":
    uploaded = st.sidebar.file_uploader(
        "Arquivo XLSX (colunas: PRODUTO_ID, PRECO_VENDA)",
        type=["xlsx"],
    )
    if uploaded:
        try:
            df_xlsx = pd.read_excel(uploaded, dtype={"PRODUTO_ID": int, "PRECO_VENDA": str})
            def normalizar_preco(val):
                val = str(val).strip()
                # remove separador de milhar (ponto ou vírgula seguido de 3 dígitos)
                import re
                val = re.sub(r"[.,](\d{3})(?=[.,]|\s*$)", r"\1", val)
                val = val.replace(",", ".")
                return float(val)
            df_xlsx["PRECO_VENDA"] = df_xlsx["PRECO_VENDA"].apply(normalizar_preco)
            xlsx_precos = df_xlsx.set_index("PRODUTO_ID")["PRECO_VENDA"]
            st.sidebar.success(f"{len(df_xlsx)} produtos carregados do XLSX.")
        except Exception as e:
            st.sidebar.error(f"Erro ao ler o arquivo: {e}")

# ── Filtragem ────────────────────────────────────────────────────────────────

df = df_base.copy()

if sku_input.strip():
    skus = [s.strip() for s in sku_input.split(",") if s.strip()]
    try:
        skus_int = [int(s) for s in skus]
        df = df[df["PRODUTO_ID"].isin(skus_int)]
    except ValueError:
        st.sidebar.warning("SKUs devem ser números inteiros separados por vírgula.")

if nome_input.strip():
    df = df[df["NOME"].str.contains(nome_input.strip(), case=False, na=False)]

if df.empty:
    st.info("Nenhum produto encontrado para os filtros aplicados.")
    st.stop()

# ── Ajuste de preço ──────────────────────────────────────────────────────────

if ajuste_tipo == "Percentual (%)":
    df["PRECO_VENDA"] = df["PRECO_VENDA"] * (1 + ajuste_valor / 100)
elif ajuste_tipo == "Definir preço fixo (R$)":
    df["PRECO_VENDA"] = ajuste_valor
elif ajuste_tipo == "Importar XLSX" and xlsx_precos is not None:
    df["PRECO_VENDA"] = df["PRODUTO_ID"].map(xlsx_precos).fillna(df["PRECO_VENDA"])

df["PRECO_VENDA"] = df["PRECO_VENDA"].clip(lower=0)

# ── Cálculo ──────────────────────────────────────────────────────────────────

df_result = calcular_frete(df, df_faixas)

colunas_exibir = [
    "PRODUTO_ID", "NOME", "PESO", "VOLUME",
    "PRECO_VENDA", "FAIXA_PESO", "FAIXA_PRECO", "FRETE",
]
df_exibir = df_result[colunas_exibir].copy()
df_exibir["PRECO_VENDA"] = df_exibir["PRECO_VENDA"].round(2)

# ── Tabela ───────────────────────────────────────────────────────────────────

st.subheader(f"Produtos ({len(df_exibir):,})")

st.dataframe(
    df_exibir,
    use_container_width=True,
    height=600,
    column_config={
        "PESO":       st.column_config.NumberColumn("PESO (kg)", format="%.3f"),
        "VOLUME":     st.column_config.NumberColumn("VOLUME", format="%.4f"),
        "PRECO_VENDA": st.column_config.NumberColumn("PREÇO VENDA", format="R$ %.2f"),
        "FRETE":      st.column_config.NumberColumn("FRETE", format="R$ %.2f"),
    },
)

# ── Export CSV ───────────────────────────────────────────────────────────────

csv_buffer = io.StringIO()
df_exibir.to_csv(csv_buffer, index=False, sep=";", decimal=",")

st.download_button(
    label="⬇ Exportar CSV",
    data=csv_buffer.getvalue().encode("utf-8-sig"),
    file_name="frete_produtos.csv",
    mime="text/csv",
)