import streamlit as st
import pandas as pd
import xml.etree.ElementTree as ET
import zipfile
import io
import json

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="Classificador NF-e Domínio", layout="wide", page_icon="🧾")

# --- 1. INICIALIZAÇÃO DO ESTADO ---
# Variáveis de Dados
if "arquivos_xml" not in st.session_state:
    st.session_state.arquivos_xml = {} 
if "df_notas" not in st.session_state:
    st.session_state.df_notas = pd.DataFrame() 

# Variáveis de Regras
if "regras_cfop" not in st.session_state:
    st.session_state.regras_cfop = {} 
if "regras_ncm" not in st.session_state:
    st.session_state.regras_ncm = {}
if "regras_forn_ncm" not in st.session_state:
    st.session_state.regras_forn_ncm = {}

# --- 2. FUNÇÕES DE LEITURA (XML e ZIP) ---
def processar_xml(nome_arquivo, conteudo_bytes):
    ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
    st.session_state.arquivos_xml[nome_arquivo] = conteudo_bytes
    
    try:
        tree = ET.parse(io.BytesIO(conteudo_bytes))
        root = tree.getroot()
        infNFe = root.find('.//nfe:infNFe', ns)
        if infNFe is None: return []

        ide = infNFe.find('nfe:ide', ns)
        emit = infNFe.find('nfe:emit', ns)
        
        nNF = ide.find('nfe:nNF', ns).text if ide is not None else "S/N"
        xNome = emit.find('nfe:xNome', ns).text if emit is not None else "Desconhecido"
        
        produtos = []
        for i, det in enumerate(root.findall('.//nfe:det', ns)):
            prod = det.find('nfe:prod', ns)
            
            # Extração de dados cruciais
            xProd = prod.find('nfe:xProd', ns).text
            ncm_tag = prod.find('nfe:NCM', ns)
            ncm = ncm_tag.text if ncm_tag is not None else ""
            
            # Valores
            vuncom_tag = prod.find('nfe:vUnCom', ns)
            vProd_tag = prod.find('nfe:vProd', ns)
            vUnCom = float(vuncom_tag.text) if vuncom_tag is not None else 0.0
            vProd = float(vProd_tag.text) if vProd_tag is not None else 0.0
            
            cfop_original = prod.find('nfe:CFOP', ns).text
            
            produtos.append({
                "Arquivo": nome_arquivo,
                "Nota": nNF,
                "Fornecedor": xNome,
                "ID_Item": i,
                "NCM": ncm,
                "Produto": xProd,
                "V_Unit": vUnCom,
                "V_Total": vProd,
                "CFOP_Orig": cfop_original,
                "CFOP_Novo": cfop_original,
                "Status": "⚪ Pendente"
            })
        return produtos
    except Exception as e:
        st.error(f"Erro ao ler {nome_arquivo}: {e}")
        return []

def carregar_arquivos(uploaded_files):
    st.session_state.arquivos_xml = {}
    todos_produtos = []
    
    for file in uploaded_files:
        if file.name.lower().endswith('.zip'):
            with zipfile.ZipFile(file) as z:
                for nome_arq in z.namelist():
                    if nome_arq.lower().endswith('.xml'):
                        conteudo = z.read(nome_arq)
                        todos_produtos.extend(processar_xml(nome_arq, conteudo))
        elif file.name.lower().endswith('.xml'):
            todos_produtos.extend(processar_xml(file.name, file.read()))
            
    if todos_produtos:
        st.session_state.df_notas = pd.DataFrame(todos_produtos)
        # Cria uma coluna de capítulo NCM para ajudar nos filtros
        st.session_state.df_notas["Cap_NCM"] = st.session_state.df_notas["NCM"].astype(str).str[:2]
        st.success("Arquivos carregados com sucesso!")

# --- 3. MOTORES DE AUTOMAÇÃO (ORDEM DE PRIORIDADE) ---
# --- 3. MOTORES DE AUTOMAÇÃO (ORDEM DE PRIORIDADE COM TRAVA) ---
def aplicar_regras():
    if st.session_state.df_notas.empty: return
    df = st.session_state.df_notas
    
    # TRAVA DE SEGURANÇA: Identifica o que foi alterado na mão para proteger
    mascara_protegida = df["Status"] == "🟣 Manual"
    
    # Prioridade 1: Regras Gerais de CFOP (Mais fraca)
    for de_cfop, para_cfop in st.session_state.regras_cfop.items():
        # Aplica a regra APENAS ONDE o CFOP bater E não estiver protegido
        mask = (df["CFOP_Novo"] == de_cfop) & (~mascara_protegida)
        df.loc[mask, "CFOP_Novo"] = para_cfop
        df.loc[mask, "Status"] = "🔵 Regra CFOP"

    # Prioridade 2: Regras de NCM (Média)
    for ncm_prefixo, para_cfop in st.session_state.regras_ncm.items():
        # Aplica APENAS ONDE o NCM bater E não estiver protegido
        mask = (df["NCM"].astype(str).str.startswith(ncm_prefixo)) & (~mascara_protegida)
        df.loc[mask, "CFOP_Novo"] = para_cfop
        df.loc[mask, "Status"] = "🏷️ Regra NCM"

    # Prioridade 3: Regras Específicas (Fornecedor + NCM) (Mais Forte)
    for forn_ncm, para_cfop in st.session_state.regras_forn_ncm.items():
        fornecedor_alvo, ncm_prefixo = forn_ncm.split("|")
        # Aplica APENAS ONDE Fornecedor e NCM baterem E não estiver protegido
        mask = (df["Fornecedor"].str.contains(fornecedor_alvo, case=False, na=False)) & \
               (df["NCM"].astype(str).str.startswith(ncm_prefixo)) & \
               (~mascara_protegida)
        df.loc[mask, "CFOP_Novo"] = para_cfop
        df.loc[mask, "Status"] = "🎯 Forn + NCM"
        
    st.session_state.df_notas = df

# --- 4. EXPORTAÇÃO ---
def gerar_zip_modificado():
    ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
    ET.register_namespace('', ns['nfe'])
    buffer = io.BytesIO()
    df = st.session_state.df_notas
    
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for nome_arquivo, conteudo in st.session_state.arquivos_xml.items():
            tree = ET.parse(io.BytesIO(conteudo))
            root = tree.getroot()
            dets = root.findall('.//nfe:det', ns)
            
            df_arquivo = df[df["Arquivo"] == nome_arquivo]
            
            for _, row in df_arquivo.iterrows():
                if row["CFOP_Orig"] != row["CFOP_Novo"]: 
                    idx = row["ID_Item"]
                    dets[idx].find('nfe:prod', ns).find('nfe:CFOP', ns).text = row["CFOP_Novo"]
            
            xml_bytes = io.BytesIO()
            tree.write(xml_bytes, encoding='utf-8', xml_declaration=True)
            zf.writestr(nome_arquivo, xml_bytes.getvalue())
            
    return buffer.getvalue()


# ==========================================
# INTERFACE DO USUÁRIO (UI)
# ==========================================

st.title("🧾 Preparador de XMLs para a Domínio")

# --- BARRA LATERAL (OPERAÇÕES DE ARQUIVO E REGRAS) ---
with st.sidebar:
    st.header("📂 1. Subir Notas (XML/ZIP)")
    arquivos_upados = st.file_uploader("Arraste os arquivos aqui", type=["xml", "zip"], accept_multiple_files=True)
    if st.button("Processar Arquivos", type="primary", use_container_width=True):
        if arquivos_upados:
            carregar_arquivos(arquivos_upados)
        else:
            st.warning("Selecione arquivos primeiro.")
            
    st.divider()

    st.header("💾 2. Banco de Regras")
    # Consolidar as regras em um único JSON para exportar/importar
    todas_regras = {
        "cfop": st.session_state.regras_cfop,
        "ncm": st.session_state.regras_ncm,
        "forn_ncm": st.session_state.regras_forn_ncm
    }
    
    regras_json = json.dumps(todas_regras, indent=4)
    st.download_button("📥 Baixar Regras (.json)", data=regras_json, file_name="regras_fiscais.json", mime="application/json", use_container_width=True)
    
    arquivo_regras = st.file_uploader("📤 Importar Regras (.json)", type=["json"])
    if arquivo_regras is not None:
        try:
            regras_carregadas = json.load(arquivo_regras)
            if st.button("Aplicar Arquivo Importado"):
                st.session_state.regras_cfop.update(regras_carregadas.get("cfop", {}))
                st.session_state.regras_ncm.update(regras_carregadas.get("ncm", {}))
                st.session_state.regras_forn_ncm.update(regras_carregadas.get("forn_ncm", {}))
                st.rerun()
        except:
            st.error("Arquivo inválido.")

    st.divider()
    
    st.header("🚀 3. Finalizar")
    if not st.session_state.df_notas.empty:
        zip_pronto = gerar_zip_modificado()
        st.download_button(
            label="BAIXAR XMLs AJUSTADOS (ZIP)",
            data=zip_pronto,
            file_name="NFe_Tratadas_Dominio.zip",
            mime="application/zip",
            type="primary",
            use_container_width=True
        )

# --- ÁREA PRINCIPAL ---
if st.session_state.df_notas.empty:
    st.info("👋 Olá! Use a barra lateral para carregar seus arquivos XML ou o arquivo ZIP contendo as notas.")
else:
    df = st.session_state.df_notas
    
    # ESTATÍSTICAS
    total_itens = len(df)
    itens_alterados = len(df[df["CFOP_Orig"] != df["CFOP_Novo"]])
    
    c1, c2, c3 = st.columns(3)
    c1.metric("Total de Produtos", total_itens)
    c2.metric("Classificados", itens_alterados)
    c3.metric("Pendentes", total_itens - itens_alterados)
    st.progress(itens_alterados / total_itens if total_itens > 0 else 0)

    # ABAS
    aba_regras, aba_triagem = st.tabs(["⚙️ Motores de Regras", "🎯 Mesa de Triagem e Filtros"])

    # --- ABA 1: REGRAS ---
    with aba_regras:
        st.markdown("O sistema aplica as regras de cima para baixo. Regras específicas substituem regras gerais.")
        
        colR1, colR2, colR3 = st.columns(3)
        
        # 1. Regra de CFOP Geral
        with colR1:
            with st.container(border=True):
                st.markdown("**1. Regra Geral (CFOP)**")
                de_cfop = st.text_input("CFOP Original:", key="r1_de")
                para_cfop = st.text_input("Novo CFOP:", key="r1_para")
                if st.button("Adicionar Regra CFOP", use_container_width=True):
                    if de_cfop and para_cfop:
                        st.session_state.regras_cfop[de_cfop] = para_cfop
                        st.rerun()
                if st.session_state.regras_cfop:
                    st.caption("Ativas: " + ", ".join([f"{k}➜{v}" for k,v in st.session_state.regras_cfop.items()]))
                    if st.button("Limpar", key="limpar_r1"): st.session_state.regras_cfop = {}; st.rerun()

        # 2. Regra de NCM
        with colR2:
            with st.container(border=True):
                st.markdown("**2. Regra por NCM (Início)**")
                ncm_pref = st.text_input("Início do NCM:", placeholder="Ex: 4802", key="r2_ncm")
                para_cfop2 = st.text_input("Novo CFOP:", key="r2_para")
                if st.button("Adicionar Regra NCM", use_container_width=True):
                    if ncm_pref and para_cfop2:
                        st.session_state.regras_ncm[ncm_pref] = para_cfop2
                        st.rerun()
                if st.session_state.regras_ncm:
                    st.caption("Ativas: " + ", ".join([f"{k}➜{v}" for k,v in st.session_state.regras_ncm.items()]))
                    if st.button("Limpar", key="limpar_r2"): st.session_state.regras_ncm = {}; st.rerun()

        # 3. Regra Específica (Fornecedor + NCM)
        with colR3:
            with st.container(border=True):
                st.markdown("**3. Fornecedor + NCM**")
                forn_nome = st.text_input("Nome (Contém):", placeholder="Ex: KALUNGA", key="r3_forn")
                ncm_pref3 = st.text_input("Início do NCM:", key="r3_ncm")
                para_cfop3 = st.text_input("Novo CFOP:", key="r3_para")
                if st.button("Adicionar Regra Específica", use_container_width=True):
                    if forn_nome and ncm_pref3 and para_cfop3:
                        chave = f"{forn_nome}|{ncm_pref3}"
                        st.session_state.regras_forn_ncm[chave] = para_cfop3
                        st.rerun()
                if st.session_state.regras_forn_ncm:
                    st.caption("Ativas: " + ", ".join([f"{k.replace('|','+')}➜{v}" for k,v in st.session_state.regras_forn_ncm.items()]))
                    if st.button("Limpar", key="limpar_r3"): st.session_state.regras_forn_ncm = {}; st.rerun()

        st.divider()
        if st.button("⚡ EXECUTAR TODAS AS REGRAS NAS NOTAS", type="primary"):
            aplicar_regras()
            st.rerun()


    # --- ABA 2: MESA DE TRIAGEM ---
    with aba_triagem:
        
        # Filtros Dinâmicos
        colF1, colF2, colF3, colF4 = st.columns(4)
        with colF1:
            status_opts = sorted(df["Status"].unique().tolist())
            def_status = ["⚪ Pendente"] if "⚪ Pendente" in status_opts else None
            f_status = st.multiselect("Status:", status_opts, default=def_status)
        with colF2:
            forn_opts = sorted(df["Fornecedor"].unique().tolist())
            f_forn = st.multiselect("Fornecedor:", forn_opts)
        with colF3:
            cfop_opts = sorted(df["CFOP_Orig"].unique().tolist())
            f_cfop = st.multiselect("CFOP Orig:", cfop_opts)
        with colF4:
            ncm_opts = sorted(df[df["Cap_NCM"] != ""]["Cap_NCM"].unique().tolist())
            f_ncm = st.multiselect("Início NCM:", ncm_opts)
            
        ocultar_prontas = st.checkbox("Esconder Notas 100% Classificadas", value=True)

        # Aplicando Filtros
        df_filt = df.copy()
        if f_status: df_filt = df_filt[df_filt["Status"].isin(f_status)]
        if f_forn: df_filt = df_filt[df_filt["Fornecedor"].isin(f_forn)]
        if f_cfop: df_filt = df_filt[df_filt["CFOP_Orig"].isin(f_cfop)]
        if f_ncm: df_filt = df_filt[df_filt["Cap_NCM"].isin(f_ncm)]

        st.divider()

        if df_filt.empty:
            st.success("Nenhum item pendente com esses filtros! 🎉")
        else:
            notas_unicas = df_filt[['Arquivo', 'Nota', 'Fornecedor']].drop_duplicates()

            for _, row in notas_unicas.iterrows():
                arq = row['Arquivo']
                nf = row['Nota']
                forn = row['Fornecedor']
                
                # Para estatística do Expander, olha a nota inteira original
                df_nota_original = df[df['Arquivo'] == arq]
                tot = len(df_nota_original)
                alt = len(df_nota_original[df_nota_original['CFOP_Orig'] != df_nota_original['CFOP_Novo']])
                
                if ocultar_prontas and tot == alt:
                    continue
                
                icone = "🟢" if tot == alt else ("🟡" if alt > 0 else "⚪")
                
                # Filtra apenas o que vai pra grade (respeitando o filtro de cima)
                df_grade = df_filt[df_filt['Arquivo'] == arq].copy()

                with st.expander(f"{icone} NF: {nf} | Fornecedor: {forn} ({alt}/{tot} classificados)"):
                    
                    edited_df = st.data_editor(
                        df_grade,
                        key=f"editor_{arq}",
                        use_container_width=True,
                        hide_index=True,
                        disabled=["NCM", "Produto", "V_Unit", "V_Total", "CFOP_Orig", "Status"], 
                        column_config={
                            "NCM": st.column_config.TextColumn("NCM", width="small"),
                            "Produto": st.column_config.TextColumn("Descrição", width="large"),
                            "V_Unit": st.column_config.NumberColumn("V. Unitário", format="R$ %.2f", width="small"),
                            "V_Total": st.column_config.NumberColumn("V. Total", format="R$ %.2f", width="small"),
                            "CFOP_Orig": st.column_config.TextColumn("Origem", width="small"),
                            "CFOP_Novo": st.column_config.TextColumn("Novo CFOP ✏️", width="small"),
                            "Status": st.column_config.TextColumn("Status", width="small"),
                            "Arquivo": None, "Nota": None, "Fornecedor": None, "ID_Item": None, "Cap_NCM": None
                        }
                    )

                    if not edited_df.equals(df_grade):
                        mask = (edited_df["CFOP_Novo"] != df_grade["CFOP_Novo"])
                        edited_df.loc[mask, "Status"] = "🟣 Manual"
                        st.session_state.df_notas.loc[edited_df.index] = edited_df
                        st.rerun()