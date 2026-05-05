import os 
import json
import time

# Azure OpenAI
from langchain_openai import AzureChatOpenAI
# LangGraph Supervisor
from langgraph_supervisor import create_supervisor
from langgraph.prebuilt import create_react_agent

# Tavily API
from langchain_tavily import TavilySearch

# 動作確認用
from print_for_langchain import pretty_print_message, pretty_print_messages

class AgentRunner:
    def __init__(self, company: str = "ソフトバンク", industry="DXコンサルティング"):
        self.company = company
        self.industry = industry
        # 共有データストレージを追加
        self.shared_data = {
            "research_results": None,
            "pest_results": None,
            "three_c_results": None,
            "five_f_results": None,
            "vrio_results": None,
            "swot_results": None,
            "execution_order": [],
            "completed_agents": set()  # 完了したエージェントを追跡
        }

    def format_table_results(self, analysis_type, results):
        """分析結果をMarkdown Table形式に変換"""
        if not results or not isinstance(results, list):
            return f"| {analysis_type} | 結果なし |"
        
        # カテゴリごとに結果を整理
        categories = {}
        for item in results:
            if isinstance(item, str) and ":" in item:
                category, content = item.split(":", 1)
                category = category.strip()
                content = content.strip()
                if category not in categories:
                    categories[category] = []
                categories[category].append(content)
            else:
                # カテゴリがない場合は「その他」として扱う
                if "その他" not in categories:
                    categories["その他"] = []
                categories["その他"].append(str(item))
        
        # Markdown table生成
        table_lines = []
        table_lines.append(f"| カテゴリ | 内容 |")
        table_lines.append("| --- | --- |")
        
        for category, contents in categories.items():
            for i, content in enumerate(contents):
                if i == 0:
                    table_lines.append(f"| {category} | {content} |")
                else:
                    table_lines.append(f"| | {content} |")
        
        return "\n".join(table_lines)

    def run_agent(self):
        #---------------------------------------------------------------------------
        # モデル定義
        #---------------------------------------------------------------------------
        model = AzureChatOpenAI(
            azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
            api_key=os.getenv("AZURE_OPENAI_API_KEY"),
            azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT_MODEL_NAME"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION"),
            #temperature=0.1,
        )

        #---------------------------------------------------------------------------
        # Agent用ツール定義（共有データ活用版）
        #---------------------------------------------------------------------------
       
        # Web検索用関数(直近1週間)
        def web_search(query):
            """Search the web for information."""
            try:
                # 既に十分な検索結果がある場合はスキップ
                if self.shared_data["research_results"] and len(self.shared_data["research_results"]) >= 5:
                    self.shared_data["completed_agents"].add("research_expert")
                    return "既に十分な検索結果があります。検索タスクを完了します。"
                
                tavily = TavilySearch(
                    api_key=os.getenv("TAVILY_API_KEY"),  
                    max_results=5,
                    # time_range="week",
                    time_range="month",
                    topic="news",
                    search_depth="advanced",
                    include_answer=True,
                )
                result = tavily.invoke(query)
                
                # 検索結果を共有データに保存
                if self.shared_data["research_results"] is None:
                    self.shared_data["research_results"] = []
                
                # 結果が有効な場合のみ保存
                if result and not isinstance(result, str) or not result.startswith("検索エラー"):
                    self.shared_data["research_results"].append({
                        "query": query,
                        "results": result
                    })
                    
                    # research_expertの完了を記録
                    if len(self.shared_data["research_results"]) >= 3:
                        self.shared_data["completed_agents"].add("research_expert")
                    
                    return f"検索完了: {query}\n結果件数: {len(result) if isinstance(result, list) else 1}\n現在の検索回数: {len(self.shared_data['research_results'])}/5"
                else:
                    return result
                    
            except Exception as e:
                return f"検索エラーが発生しました: {str(e)}"

        # 分析完了を確実にする共通関数
        def mark_analysis_complete(analysis_type, result):
            """分析完了を記録して結果を返す"""
            self.shared_data["completed_agents"].add(f"{analysis_type}_expert")
            return result

        # PEST分析用関数
        def pest_analysis():
            """Conduct a PEST analysis using research data only."""
            try:
                # 既に結果がある場合は既存の結果を返す
                if self.shared_data["pest_results"] is not None:
                    return mark_analysis_complete("pest_analysis", self.shared_data["pest_results"])
                
                # 検索結果の文脈を準備
                research_context = ""
                if self.shared_data["research_results"]:
                    research_context = f"\n### 参考情報（直近1週間の検索結果） ###\n"
                    for item in self.shared_data["research_results"][:3]:
                        research_context += f"検索クエリ: {item['query']}\n結果: {str(item['results'])[:300]}...\n\n"
               
                messages = [
                    {
                        "role": "system",
                        "content": "あなたは優秀なPEST分析者です。PEST分析の結果をJSON形式の文字列配列で返してください。"
                    },
                    {
                        "role": "user",
                        "content": f"企業：「{self.company}」および、業界：「{self.industry}」の観点でPEST分析をしてください。{research_context}"
                    },
                    {
                        "role": "user",
                        "content": """### 制約 ###
回答に「```json」や「```」等のコードブロックを入れないでください。
以下の形式で、最低8項目を含めてください：
["P:政治的要因1", "P:政治的要因2", "E:経済的要因1", "E:経済的要因2", "S:社会的要因1", "S:社会的要因2", "T:技術的要因1", "T:技術的要因2"]"""
                    },
                ]
                
                res = model.invoke(messages)
                content = res.content.strip()
                
                # JSONパース試行
                try:
                    result = json.loads(content)
                    if isinstance(result, list):
                        self.shared_data["pest_results"] = result
                        return mark_analysis_complete("pest_analysis", result)
                except:
                    pass
                
                # フォールバック
                fallback_result = [
                    f"P:政治 - {self.industry}に関する規制動向",
                    f"P:政治 - 政府の産業政策",
                    f"E:経済 - {self.industry}の市場成長率",
                    f"E:経済 - 原材料価格の変動",
                    f"S:社会 - 環境意識の高まり",
                    f"S:社会 - 労働力人口の変化",
                    f"T:技術 - デジタル化の進展",
                    f"T:技術 - 新技術の導入"
                ]
                self.shared_data["pest_results"] = fallback_result
                return mark_analysis_complete("pest_analysis", fallback_result)
                
            except Exception as e:
                fallback_result = [f"PEST分析エラー: {str(e)}"]
                self.shared_data["pest_results"] = fallback_result
                return mark_analysis_complete("pest_analysis", fallback_result)

        # 3C分析用関数
        def three_c_analysis():
            """Conduct a 3C analysis using research data only."""
            try:
                if self.shared_data["three_c_results"] is not None:
                    return mark_analysis_complete("3c_analysis", self.shared_data["three_c_results"])
                
                research_context = ""
                if self.shared_data["research_results"]:
                    research_context = f"\n### 参考情報（直近1週間の検索結果） ###\n"
                    for item in self.shared_data["research_results"][:3]:
                        research_context += f"検索クエリ: {item['query']}\n結果: {str(item['results'])[:300]}...\n\n"
               
                messages = [
                    {
                        "role": "system",
                        "content": "あなたは優秀な3C分析者です。3C分析の結果をJSON形式の文字列配列で返してください。"
                    },
                    {
                        "role": "user",
                        "content": f"企業：「{self.company}」および、業界：「{self.industry}」の観点で3C分析をしてください。{research_context}"
                    },
                    {
                        "role": "user",
                        "content": """### 制約 ###
回答に「```json」や「```」等のコードブロックを入れないでください。
以下の形式で、最低6項目を含めてください：
["顧客:顧客分析1", "顧客:顧客分析2", "競合:競合分析1", "競合:競合分析2", "自社:自社分析1", "自社:自社分析2"]"""
                    },
                ]
                
                res = model.invoke(messages)
                content = res.content.strip()
                
                try:
                    result = json.loads(content)
                    if isinstance(result, list):
                        self.shared_data["three_c_results"] = result
                        return mark_analysis_complete("3c_analysis", result)
                except:
                    pass
                
                fallback_result = [
                    f"顧客 - {self.industry}の主要顧客層",
                    f"顧客 - 顧客ニーズの変化",
                    f"競合 - 業界内の主要競合他社",
                    f"競合 - 競合他社の戦略動向",
                    f"自社 - {self.company}の強み",
                    f"自社 - {self.company}の課題"
                ]
                self.shared_data["three_c_results"] = fallback_result
                return mark_analysis_complete("3c_analysis", fallback_result)
                
            except Exception as e:
                fallback_result = [f"3C分析エラー: {str(e)}"]
                self.shared_data["three_c_results"] = fallback_result
                return mark_analysis_complete("3c_analysis", fallback_result)

        # 5F分析用関数
        def five_f_analysis():
            """Conduct a 5F analysis using research data only."""
            try:
                if self.shared_data["five_f_results"] is not None:
                    return mark_analysis_complete("5f_analysis", self.shared_data["five_f_results"])
                
                research_context = ""
                if self.shared_data["research_results"]:
                    research_context = f"\n### 参考情報（直近1週間の検索結果） ###\n"
                    for item in self.shared_data["research_results"][:3]:
                        research_context += f"検索クエリ: {item['query']}\n結果: {str(item['results'])[:300]}...\n\n"
               
                messages = [
                    {
                        "role": "system",
                        "content": "あなたは優秀な5F分析者です。5F分析の結果をJSON形式の文字列配列で返してください。"
                    },
                    {
                        "role": "user",
                        "content": f"企業：「{self.company}」および、業界：「{self.industry}」の観点で5F分析をしてください。{research_context}"
                    },
                    {
                        "role": "user",
                        "content": """### 制約 ###
回答に「```json」や「```」等のコードブロックを入れないでください。
以下の形式で、最低10項目を含めてください：
["売り手:分析1", "売り手:分析2", "買い手:分析1", "買い手:分析2", "競争:分析1", "競争:分析2", "新規参入:分析1", "新規参入:分析2", "代替品:分析1", "代替品:分析2"]"""
                    },
                ]
                
                res = model.invoke(messages)
                content = res.content.strip()
                
                try:
                    result = json.loads(content)
                    if isinstance(result, list):
                        self.shared_data["five_f_results"] = result
                        return mark_analysis_complete("5f_analysis", result)
                except:
                    pass
                
                fallback_result = [
                    f"売り手 - {self.industry}のサプライヤー集中度",
                    f"売り手 - 切り替えコスト",
                    f"買い手 - 顧客の価格感応度",
                    f"買い手 - 顧客の集中度",
                    f"競争 - 業界の成長率",
                    f"競争 - 差別化の程度",
                    f"新規参入 - 参入障壁の高さ",
                    f"新規参入 - 規模の経済性",
                    f"代替品 - 代替品の性能",
                    f"代替品 - 切り替えコスト"
                ]
                self.shared_data["five_f_results"] = fallback_result
                return mark_analysis_complete("5f_analysis", fallback_result)
                
            except Exception as e:
                fallback_result = [f"5F分析エラー: {str(e)}"]
                self.shared_data["five_f_results"] = fallback_result
                return mark_analysis_complete("5f_analysis", fallback_result)

        # VRIO分析用関数
        def vrio_analysis():
            """Conduct a VRIO analysis using research data only."""
            try:
                if self.shared_data["vrio_results"] is not None:
                    return mark_analysis_complete("vrio_analysis", self.shared_data["vrio_results"])
                
                research_context = ""
                if self.shared_data["research_results"]:
                    research_context = f"\n### 参考情報（直近1週間の検索結果） ###\n"
                    for item in self.shared_data["research_results"][:3]:
                        research_context += f"検索クエリ: {item['query']}\n結果: {str(item['results'])[:300]}...\n\n"
               
                messages = [
                    {
                        "role": "system",
                        "content": "あなたは優秀なVRIO分析者です。VRIO分析の結果をJSON形式の文字列配列で返してください。"
                    },
                    {
                        "role": "user",
                        "content": f"企業：「{self.company}」および、業界：「{self.industry}」の観点でVRIO分析をしてください。{research_context}"
                    },
                    {
                        "role": "user",
                        "content": """### 制約 ###
回答に「```json」や「```」等のコードブロックを入れないでください。
以下の形式で、最低8項目を含めてください：
["価値:分析1", "価値:分析2", "希少性:分析1", "希少性:分析2", "模倣可能性:分析1", "模倣可能性:分析2", "組織:分析1", "組織:分析2"]"""
                    },
                ]
                
                res = model.invoke(messages)
                content = res.content.strip()
                
                try:
                    result = json.loads(content)
                    if isinstance(result, list):
                        self.shared_data["vrio_results"] = result
                        return mark_analysis_complete("vrio_analysis", result)
                except:
                    pass
                
                fallback_result = [
                    f"価値 - {self.company}の技術力",
                    f"価値 - ブランド価値",
                    f"希少性 - 独自技術の保有",
                    f"希少性 - 特許・知的財産",
                    f"模倣可能性 - 技術の複雑性",
                    f"模倣可能性 - 投資規模の必要性",
                    f"組織 - 経営資源の活用体制",
                    f"組織 - 組織文化と能力"
                ]
                self.shared_data["vrio_results"] = fallback_result
                return mark_analysis_complete("vrio_analysis", fallback_result)
                
            except Exception as e:
                fallback_result = [f"VRIO分析エラー: {str(e)}"]
                self.shared_data["vrio_results"] = fallback_result
                return mark_analysis_complete("vrio_analysis", fallback_result)

        # SWOT分析用関数
        def swot_analysis():
            """Conduct a SWOT analysis using research data only."""
            try:
                if self.shared_data["swot_results"] is not None:
                    return mark_analysis_complete("swot_analysis", self.shared_data["swot_results"])
                
                research_context = ""
                if self.shared_data["research_results"]:
                    research_context = f"\n### 参考情報（直近1週間の検索結果） ###\n"
                    for item in self.shared_data["research_results"][:3]:
                        research_context += f"検索クエリ: {item['query']}\n結果: {str(item['results'])[:300]}...\n\n"
               
                messages = [
                    {
                        "role": "system",
                        "content": "あなたは優秀なSWOT分析者です。SWOT分析の結果をJSON形式の文字列配列で返してください。"
                    },
                    {
                        "role": "user",
                        "content": f"企業：「{self.company}」および、業界：「{self.industry}」の観点でSWOT分析をしてください。{research_context}"
                    },
                    {
                        "role": "user",
                        "content": """### 制約 ###
回答に「```json」や「```」等のコードブロックを入れないでください。
以下の形式で、最低8項目を含めてください：
["強み:分析1", "強み:分析2", "弱み:分析1", "弱み:分析2", "機会:分析1", "機会:分析2", "脅威:分析1", "脅威:分析2"]"""
                    },
                ]
                
                res = model.invoke(messages)
                content = res.content.strip()
                
                try:
                    result = json.loads(content)
                    if isinstance(result, list):
                        self.shared_data["swot_results"] = result
                        return mark_analysis_complete("swot_analysis", result)
                except:
                    pass
                
                fallback_result = [
                    f"強み - {self.company}の技術力",
                    f"強み - 市場での地位",
                    f"弱み - 規模の制約",
                    f"弱み - 海外展開の遅れ",
                    f"機会 - {self.industry}市場の成長",
                    f"機会 - 新技術の活用可能性",
                    f"脅威 - 競合他社の台頭",
                    f"脅威 - 原材料価格の高騰"
                ]
                self.shared_data["swot_results"] = fallback_result
                return mark_analysis_complete("swot_analysis", fallback_result)
                
            except Exception as e:
                fallback_result = [f"SWOT分析エラー: {str(e)}"]
                self.shared_data["swot_results"] = fallback_result
                return mark_analysis_complete("swot_analysis", fallback_result)

        #---------------------------------------------------------------------------
        # Agent定義
        #---------------------------------------------------------------------------
       
        # 検索エージェント
        research_agent = create_react_agent(
            model=model,
            tools=[web_search],
            name="research_expert",
            prompt=f"""あなたは世界レベルのWeb検索の専門家です。
            
            企業「{self.company}」と業界「{self.industry}」に関する最新情報（直近2ヶ月以内）を収集してください。
            
            ### 検索戦略 ###
            以下の観点から3-5回の検索を実行してください：
            1. 企業名を含む最新ニュース
            2. 業界全体の動向や市場情報
            3. 競合他社の動き
            4. 技術革新や規制変更
            5. 顧客動向や市場需要
            
            ### 注意事項 ###
            - 各検索は異なる観点から行ってください
            - 3回以上検索したら「検索タスク完了」と報告してください
            - あなたは検索のみを行い、分析は行いません
            - 検索が完了したら、必ず「検索タスク完了です。次はPEST分析に進んでください。」と明示してください
            """
        )

        # 各分析エージェント - 完了メッセージを明確化
        pest_analysis_agent = create_react_agent(
            model=model,
            tools=[pest_analysis],
            name="pest_analysis_expert",
            prompt="pest_analysisツールを1回実行してください。結果が返ったら「PEST分析完了です。次は3C分析に進んでください。」と報告してください。"
        )

        three_c_analysis_agent = create_react_agent(
            model=model,
            tools=[three_c_analysis],
            name="3c_analysis_expert",
            prompt="three_c_analysisツールを1回実行してください。結果が返ったら「3C分析完了です。次は5F分析に進んでください。」と報告してください。"
        )

        five_f_analysis_agent = create_react_agent(
            model=model,
            tools=[five_f_analysis],
            name="5f_analysis_expert",
            prompt="five_f_analysisツールを1回実行してください。結果が返ったら「5F分析完了です。次はVRIO分析に進んでください。」と報告してください。"
        )

        vrio_analysis_agent = create_react_agent(
            model=model,
            tools=[vrio_analysis],
            name="vrio_analysis_expert",
            prompt="vrio_analysisツールを1回実行してください。結果が返ったら「VRIO分析完了です。次はSWOT分析に進んでください。」と報告してください。"
        )

        swot_analysis_agent = create_react_agent(
            model=model,
            tools=[swot_analysis],
            name="swot_analysis_expert",
            prompt="swot_analysisツールを1回実行してください。結果が返ったら「SWOT分析完了です。全ての分析が完了しました。」と報告してください。"
        )

        #---------------------------------------------------------------------------
        # Supervisor定義
        #---------------------------------------------------------------------------
        def get_next_agent():
            """次に実行すべきエージェントを決定"""
            agent_order = [
                "research_expert",
                "pest_analysis_expert",
                "3c_analysis_expert",
                "5f_analysis_expert",
                "vrio_analysis_expert",
                "swot_analysis_expert"
            ]
            
            for agent in agent_order:
                if agent not in self.shared_data["completed_agents"]:
                    return agent
            return None

        def get_remaining_agents():
            """未実行のエージェントをリスト化"""
            agent_order = [
                "research_expert",
                "pest_analysis_expert",
                "3c_analysis_expert",
                "5f_analysis_expert",
                "vrio_analysis_expert",
                "swot_analysis_expert"
            ]
            return [agent for agent in agent_order if agent not in self.shared_data["completed_agents"]]

        supervisor_prompt = f"""### ロール ###
        あなたは6つのエージェントの実行を管理するスーパーバイザーです。分析や結論は一切出しません。
        また、以下の「現在の状況」は出力しないでください。

        ### エージェント実行順序（必須） ###
        1. research_expert
        2. pest_analysis_expert
        3. 3c_analysis_expert
        4. 5f_analysis_expert
        5. vrio_analysis_expert
        6. swot_analysis_expert

        ### 現在の状況 ###
        完了済み: {list(self.shared_data['completed_agents'])}
        未実行: {get_remaining_agents()}
        次に実行すべき: {get_next_agent() or '全て完了'}

        ### 絶対的ルール ###
        1. 未実行のエージェントがある限り、絶対にFINISHしない
        2. 次に実行すべきエージェントを必ず呼び出す
        3. エージェントから結果が返ってきたら、即座に次のエージェントへ処理を渡す
        4. 分析内容や結論を出さない（エージェント実行管理のみ）
        5. 全6エージェントが完了した時のみFINISHを選択

        ### 次のアクション ###
        {get_next_agent() or 'FINISH'}を選択してください。
        """
       
        workflow = create_supervisor(
            [research_agent, pest_analysis_agent, three_c_analysis_agent, 
             five_f_analysis_agent, vrio_analysis_agent, swot_analysis_agent],
            model=model,
            prompt=supervisor_prompt,
            add_handoff_back_messages=True,
            output_mode="last_message",  # full_historyからlast_messageに変更
        ).compile()

        # 再帰制限を増加
        config = {"recursion_limit": 40}

        user_prompt = f"""### 厳密な実行指示 ###
        
        以下の6つのエージェントを必ず全て順番に実行してください：
        
        1. research_expert: 企業「{self.company}」と業界「{self.industry}」の最新情報を検索
        2. pest_analysis_expert: PEST分析を実行
        3. 3c_analysis_expert: 3C分析を実行
        4. 5f_analysis_expert: 5F分析を実行（必須）
        5. vrio_analysis_expert: VRIO分析を実行（必須）
        6. swot_analysis_expert: SWOT分析を実行（必須）

        ### 重要な注意事項 ###
        - 途中で終了しない
        - 全6エージェントを実行するまでFINISHしない
        - 各エージェントの結果に関わらず、次のエージェントに進む
        - 5f_analysis_expert、vrio_analysis_expert、swot_analysis_expertも必ず実行する
        """

        try:
            # ストリーミング実行
            final_chunk = None
            for chunk in workflow.stream({"messages": [{"role": "user", "content": user_prompt}]}, config=config):
                pretty_print_messages(chunk, last_message=True)
                final_chunk = chunk
                
                # 途中終了を防ぐチェック
                if "supervisor" in chunk and len(self.shared_data["completed_agents"]) < 6:
                    # まだ全エージェントが完了していない場合は継続
                    continue

            # 全エージェントが完了していることを確認
            if len(self.shared_data["completed_agents"]) < 6:
                # 未完了のエージェントがある場合、強制的に実行
                remaining = get_remaining_agents()
                print(f"警告: 以下のエージェントが未実行です: {remaining}")
                
                # 未実行の分析を手動で実行
                if "5f_analysis_expert" not in self.shared_data["completed_agents"]:
                    five_f_analysis()
                if "vrio_analysis_expert" not in self.shared_data["completed_agents"]:
                    vrio_analysis()
                if "swot_analysis_expert" not in self.shared_data["completed_agents"]:
                    swot_analysis()

            # 最終結果の整形
            report = f"### 【分析レポート】\n #### {self.company} - {self.industry} \n\n *** \n\n"

            
            
            # PEST分析結果
            if self.shared_data["pest_results"]:
                report += "#### PEST分析結果\n\n"
                report += self.format_table_results("PEST", self.shared_data["pest_results"])
                report += "\n\n"
            
            # 3C分析結果
            if self.shared_data["three_c_results"]:
                report += "#### 3C分析結果\n\n"
                report += self.format_table_results("3C", self.shared_data["three_c_results"])
                report += "\n\n"
            
            # 5F分析結果
            if self.shared_data["five_f_results"]:
                report += "#### 5F分析結果\n\n"
                report += self.format_table_results("5F", self.shared_data["five_f_results"])
                report += "\n\n"
            
            # VRIO分析結果
            if self.shared_data["vrio_results"]:
                report += "#### VRIO分析結果\n\n"
                report += self.format_table_results("VRIO", self.shared_data["vrio_results"])
                report += "\n\n"
            
            # SWOT分析結果
            if self.shared_data["swot_results"]:
                report += "#### SWOT分析結果\n\n"
                report += self.format_table_results("SWOT", self.shared_data["swot_results"])
                report += "\n\n"
            
            # 総合見解を生成
            report += "## 総合見解\n\n"
            report += self.generate_conclusion()
            
            return report
           
        except Exception as e:
            # エラー時でも可能な限り結果を返す
            error_report = f"#### 【分析レポート】\n\nエラーが発生しました: {str(e)}\n\n"
            
            # 収集できた結果を整形して返す
            if self.shared_data["pest_results"]:
                error_report += "### PEST分析結果\n\n"
                error_report += self.format_table_results("PEST", self.shared_data["pest_results"])
                error_report += "\n\n"
            
            if self.shared_data["three_c_results"]:
                error_report += "### 3C分析結果\n\n"
                error_report += self.format_table_results("3C", self.shared_data["three_c_results"])
                error_report += "\n\n"
            
            if self.shared_data["five_f_results"]:
                error_report += "### 5F分析結果\n\n"
                error_report += self.format_table_results("5F", self.shared_data["five_f_results"])
                error_report += "\n\n"
            
            if self.shared_data["vrio_results"]:
                error_report += "### VRIO分析結果\n\n"
                error_report += self.format_table_results("VRIO", self.shared_data["vrio_results"])
                error_report += "\n\n"
            
            if self.shared_data["swot_results"]:
                error_report += "### SWOT分析結果\n\n"
                error_report += self.format_table_results("SWOT", self.shared_data["swot_results"])
                error_report += "\n\n"
                
            return error_report

    def generate_conclusion(self):
        """分析結果を基に総合見解を生成"""
        conclusion = f"### 事業の現状の問題点\n\n"
        
        # SWOT分析から弱みと脅威を抽出
        if self.shared_data["swot_results"]:
            weaknesses = [item for item in self.shared_data["swot_results"] if "弱み" in item]
            threats = [item for item in self.shared_data["swot_results"] if "脅威" in item]
            
            if weaknesses or threats:
                conclusion += f"{self.company}は、"
                if weaknesses:
                    conclusion += "内部的には" + "、".join([w.split(":", 1)[1].strip() for w in weaknesses[:2]]) + "という課題を抱えています。"
                if threats:
                    conclusion += "また、外部環境では" + "、".join([t.split(":", 1)[1].strip() for t in threats[:2]]) + "という脅威に直面しています。"
                conclusion += "\n\n"
        
        conclusion += "### 問題点を解決する戦略提案\n\n"
        
        # SWOT分析から強みと機会を抽出
        if self.shared_data["swot_results"]:
            strengths = [item for item in self.shared_data["swot_results"] if "強み" in item]
            opportunities = [item for item in self.shared_data["swot_results"] if "機会" in item]
            
            if strengths and opportunities:
                conclusion += "1. **強みを活かした成長戦略**: "
                conclusion += f"{strengths[0].split(':', 1)[1].strip()}を活用し、{opportunities[0].split(':', 1)[1].strip()}の機会を捉える\n\n"
        
        # VRIO分析から競争優位性を抽出
        if self.shared_data["vrio_results"]:
            valuable_resources = [item for item in self.shared_data["vrio_results"] if "価値" in item]
            if valuable_resources:
                conclusion += "2. **競争優位性の強化**: "
                conclusion += f"{valuable_resources[0].split(':', 1)[1].strip()}をさらに強化し、持続可能な競争優位を構築する\n\n"
        
        # 5F分析から市場ポジショニングを提案
        if self.shared_data["five_f_results"]:
            conclusion += "3. **市場ポジショニングの最適化**: "
            conclusion += f"{self.industry}業界の競争環境を踏まえ、差別化戦略を推進し、顧客価値を最大化する\n\n"
        
        return conclusion

