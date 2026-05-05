import html
import os
import json
import io
import time
from unittest import runner
import streamlit as st
import fitz

import re

import matplotlib.pyplot as plt
import tempfile
from io import BytesIO

# AOAI
from langchain_openai import AzureChatOpenAI
# Tavily API
from langchain_tavily import TavilySearch

# PDF用
import markdown
from bs4 import BeautifulSoup
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.units import mm
from reportlab.lib.colors import grey

# AIエージェント(agent_runner.pyを同階層に準備すること)
from agent_runner import AgentRunner

# AIエージェントのログ出力表示用
import threading
import sys
from io import StringIO
import time
import re
import streamlit.components.v1 as components

# Debug用
from pprint import pprint

# .env
from dotenv import load_dotenv
load_dotenv(override=True)

#----------------------------------------------------------
# Streamlitページ設定
#----------------------------------------------------------
st.set_page_config(
    page_title="AI Agent App",         # タブタイトル
    # page_icon="🚀",                  # タブのアイコン（絵文字や画像URLも可）
    # layout="wide",                   # ページの横幅設定 ("centered" または "wide")
    # initial_sidebar_state="expanded" # サイドバーの状態
)

#----------------------------------------------------------
# 最新情報の概要文字化け防止
#----------------------------------------------------------
def clean_content(text):
    if not isinstance(text, str):
        return ""
    # 制御文字を削除
    text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', text)
    # 強制的にUTF-8にデコード可能な形に変換(UTF-8に変換出来ない文字があれば無視(ignore))
    return text.encode("utf-8", "ignore").decode("utf-8", "ignore")

#----------------------------------------------------------
# ログの文字化け防止
#----------------------------------------------------------
def clean_ansi_codes(text):
    """ANSI エスケープシーケンス（色付けコード）を除去"""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

#----------------------------------------------------------
# Azure OpenAIモデル定義(GPT-5.4使用) エージェントが共通使用
#----------------------------------------------------------
model = AzureChatOpenAI(
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT_MODEL_NAME"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
    #temperature=0.2,
)

#----------------------------------------------------------
# PDFレポートを生成する関数
#---------------------------------------------------------- 
def generate_pdf(overall_summary, factors=None, market_info=None):
    # PDF出力用のMarkdownテキストを構築
    md_text = ""

    # 収益に影響する外部要因を出力
    if factors:
        md_text += "### 📝収益に影響する外部要因\n\n"
        for f in factors:
            md_text += f"- {f}\n"
        md_text += "\n"

    # 関連市場情報を出力
    if market_info:
        md_text += "### 📝関連市場情報\n\n"
        # for factor, info in market_info.items():
        for factor_idx, (factor, info) in enumerate(market_info.items()):
            md_text += f"##### {factor}\n\n"

            if isinstance(info, dict) and 'results' in info:
                results = info['results']
                for idx, info_dict in enumerate(results):
                    md_text += f"**【タイトル】** {info_dict.get('title', 'N/A')}\n\n"
                    content = clean_content(info_dict.get('content', '')[:200])
                    md_text += f"**【概要】** {content}...\n\n"
                    md_text += f"**【情報源】** {info_dict.get('url', 'N/A')}\n\n"
                    if idx < len(results) - 1:
                        md_text += "---\n\n"
            else:
                md_text += f"{str(info)}\n\n"

            if factor_idx < len(market_info) - 1:
                md_text += "---\n\n"

            md_text += "\n"
    # メインの分析結果
    if overall_summary:
        md_text += "\n---\n\n"
        md_text += f"{overall_summary}\n"
 
    # 日本語フォント
    #font_path = "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf"
    font_path = "./fonts/fonts-japanese-gothic.ttf"
    pdfmetrics.registerFont(TTFont("IPAexGothic", font_path))
 
    save_path = '/tmp/analysis_report.pdf'
    doc = SimpleDocTemplate(save_path, pagesize=A4, leftMargin=20, rightMargin=20)
    styles = getSampleStyleSheet()
    styles['Normal'].fontName = "IPAexGothic"
    styles['Normal'].fontSize = 12
 
    html = markdown.markdown(md_text, extensions=['nl2br', 'tables'])
    soup = BeautifulSoup(html, "html.parser")
 
    flowables = []
    max_table_width = A4[0] - doc.leftMargin - doc.rightMargin
 
    for elem in soup.contents:
        if elem.name in ['h1','h2','h3','h4','h5','h6']:
            style = styles['Heading1']  # 場合により変更
            style.fontName = "IPAexGothic"
            flowables.append(Paragraph(str(elem), style))
        elif elem.name == 'p' or elem.name == 'li':
            flowables.append(Paragraph(str(elem), styles['Normal']))
        elif elem.name in ['ul', 'ol']:
            for li in elem.find_all('li'):
                flowables.append(Paragraph(str(li), styles['Normal']))
        elif elem.name == 'table':
            table_data = []
            for row in elem.find_all('tr'):
                cells = row.find_all(['th', 'td'])
                row_data = [
                    Paragraph(cell.get_text().strip(), styles['Normal']) for cell in cells
                ]
                table_data.append(row_data)
 
            # 列数と最大幅から列幅を決定
            col_count = max(len(r) for r in table_data)
            col_width = max_table_width / col_count
 
            table = Table(table_data, colWidths=[col_width]*col_count, hAlign='LEFT')
            table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'IPAexGothic'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),  # 縦位置上揃え
            ]))
            flowables.append(table)
        elif elem.name == 'hr':  # 水平線の処理
            from reportlab.platypus import HRFlowable
            flowables.append(HRFlowable(width="100%", thickness=1, lineCap='round', color=colors.grey))
        else:
            flowables.append(Paragraph(str(elem), styles['Normal']))
        flowables.append(Spacer(1, 6)) # 適度な段落スペース
 
    doc.build(flowables)
 
    with open(save_path, "rb") as f:
        pdf_bytes = f.read()
    os.remove(save_path)
    return pdf_bytes

#----------------------------------------------------------
# 外部要因取得関数(Agent用)
#----------------------------------------------------------
def extract_factors(company, industry):
    """Get external factors."""
    messages = [
        {
            "role": "system",
            "content": "企業・業界の収益に影響する外部要因を、JSONの配列（文字列リスト）で返してください。"
        },
        {
            "role": "user",
            "content": f"企業「{company}」および業界「{industry}」の収益に影響する要因を3つ挙げてください。"
        },
        {
            "role": "user",
            "content": f"### 制約 ###\n 回答に「```json」や「```」等のコードブロックを入れないでください。"
        },
    ]
    res = model.invoke(messages)
    return json.loads(res.content)

#----------------------------------------------------------
# Tavily API関数（画面表示用)
#----------------------------------------------------------
factor_set = set()
def tavily_func(factor, company, industry):
    """Search the web for information."""
    tavily = TavilySearch(
        api_key=os.getenv("TAVILY_API_KEY"),
        max_results = 3,
        #time_range = "week",
        time_range = "month",  #小規模企業の場合、weekだと取得出来ない可能性あり
        # topic = "news",
        topic = "general",
        # topic = "finance",
        search_depth = "advanced",
        # include_answer = True,
        exclude_domains = ["syukatsu-kaigi.jp", "tleon.co.jp", "mid-tenshoku.com", "directscout.recruit.co.jp", "gaishishukatsu.com", "unistyleinc.com"],
        include_images=False,
        include_image_description=False,
    )
    results = {}
    global factor_set
    for factor in factors:
        query = f"{company} {industry} {factor}"
        factor_set.add(query)
        print("============query=============================")
        print(query)
        print("============query=============================\n")
        try:
            results[factor] = tavily.invoke(query)
        except Exception as e:
            results[factor] = f"検索エラー：{e}"

    return results



#----------------------------------------------------
# Streamlitのセッション状態初期化
#----------------------------------------------------
for key in ["company", "industry", "factors", "market_info", "overall_summary", "factor_set", "pdf_bytes"]:
    if key not in st.session_state:
        st.session_state[key] = ""

#----------------------------------------------------
# Streamlit UI
#----------------------------------------------------
# タイトル
st.markdown(
    "<h2 style='color: #1A237E;'>📋️事業環境レポート</h2>",
    unsafe_allow_html=True
)

#------------------------------------------------
# ステップ2: 企業名と業界の入力
#------------------------------------------------
# 企業名/業界名入力欄
if st.session_state["company"].strip() == "":
    company_name = "ソフトバンク"
else:
    company_name = st.session_state["company"]

if st.session_state["industry"].strip() == "":
    industry_name = "DXコンサルティング"
else:
    industry_name = st.session_state["industry"]

st.session_state["company"]  = st.text_input(label="企業名を入力してください", value=company_name)
st.session_state["industry"] = st.text_input(label="業界を入力してください", value=industry_name)


# 3. 関連市場情報取得
st.divider()
st.markdown(" ##### ℹ️最新情報確認")
if st.button("情報取得開始"):
    with st.spinner("外部要因を抽出しています...", show_time=True):
        time.sleep(5)
        factors = extract_factors(st.session_state["company"] , st.session_state["industry"])
        st.session_state["factors"] = factors

    with st.spinner("次に関連情報を取得しています...", show_time=True):
        market_info = tavily_func(factors, st.session_state["company"], st.session_state["industry"])
        st.session_state["market_info"] = market_info
        # Add
        st.session_state["factor_set"] = factor_set
        print("============== factor_set====================")
        print(st.session_state["factor_set"])
        print("============== factor_set====================\n")


# ボタンブロックの外に表示を移動（セッション状態に基づく）
if st.session_state.get("factors"):
    st.markdown("### 📝収益に影響する外部要因")
    for f in st.session_state["factors"]:
        st.write(f"- {f}")

st.write("")
if st.session_state.get("market_info"):
    st.markdown("### 📝関連情報")
    for factor, info in st.session_state["market_info"].items():
        st.markdown(f" ##### {factor}")

        # st.write(info)
        if  0 < len(info):
            results = info['results']
            for idx, info_dict in enumerate(results):
                st.write(f"【タイトル】{info_dict['title']}\n")
                st.write(f"【概要】{clean_content(info_dict['content'])[:200]}...\n")
                st.write(f"【情報源】{info_dict['url']}")
                if idx < len(results) - 1:
                    st.divider()
        else:
            st.write("直近のニュースがありませんでした。")
        
        st.write("")

#------------------------------------------------
# ステップ3: 分析実行
#------------------------------------------------ 
st.divider()
st.markdown(" ##### 📈PEST/3C/5F/VRIO/SWOT分析")
st.caption("※分析開始ボタン押下後、5分～10分ほどかかる場合もあります。（下記にログ進行が表示されます）")

# セッションステートの初期化
if 'log_lines' not in st.session_state:
    st.session_state.log_lines = []

if st.button("分析開始"):
    with st.spinner("考えています...", show_time=True):
        time.sleep(9)
    with st.spinner("分析中...", show_time=True):
        if st.session_state["company"] and st.session_state["industry"]:
            
            # ログをクリア
            st.session_state.log_lines = []
            
            # ログ表示エリア
            st.write("【AIエージェント進行状況】")
            log_container = st.empty()

            # print関数に置き換える
            original_print = print

            def new_print(*args):
                # 普通のprintも実行
                original_print(*args)
                # ログに追加
                log_text = ' '.join(str(arg) for arg in args)
                cleaned_text = clean_ansi_codes(log_text)
                st.session_state.log_lines.append(cleaned_text)

                # 最新のN行だけ表示（ウィンドウサイズ）
                WINDOW_SIZE = 5  # 表示する行数
                display_lines = st.session_state.log_lines[-WINDOW_SIZE:]
                log_display = '\n'.join(display_lines)
                
                # HTMLで表示
                html_content = f"""
                <div style="
                    border: 1px solid #ccc;
                    padding: 10px;
                    height: 300px;
                    overflow-y: hidden;
                    background-color: #f9f9f9;
                    font-family: monospace;
                    color: blue;
                    font-size: 14px;
                    white-space: pre-wrap;
                    line-height: 1.4;
                ">
                {log_display}
                </div>
                """
                
                log_container.html(html_content)

            # print関数を置き換え
            import builtins
            builtins.print = new_print

            try:
                runner = AgentRunner(st.session_state["company"], st.session_state["industry"])
                st.session_state["overall_summary"] = runner.run_agent()
                
                # 処理完了後、全ログを別のコンテナに表示
                # st.success("【処理完了】全ログは下記です")
                # all_logs_display = '\n'.join(st.session_state.log_lines)
                # st.text_area("", value=all_logs_display, height=400, disabled=True)
                
            finally:
                builtins.print = original_print

# PDFレポートボタン表示 
if st.session_state["overall_summary"]:
    # st.subheader("分析結果")
    st.success("分析完了")
    # st.write(st.session_state["overall_summary"])
    st.markdown(st.session_state["overall_summary"], unsafe_allow_html=True)

    if st.button("PDFレポートを生成"):
        st.session_state["pdf_bytes"] = generate_pdf(
            st.session_state["overall_summary"],
             st.session_state["factors"],
              st.session_state["market_info"]
        )

        st.success("PDFレポートが生成されました。")
        st.download_button("PDFをダウンロード", st.session_state["pdf_bytes"], file_name="analysis_report.pdf")

