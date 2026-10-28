# app.py网页端对话界面
import streamlit as st
import uuid
import json
import pandas as pd
from openai import OpenAI
import os
from dotenv import load_dotenv
from datetime import datetime

from database import (
    init_db, add_emotional_event, retrieve_relevant_events,
    add_conversation_turn, get_user_profile, update_user_profile, get_connection
)

# 1. 环境配置 (兼容本地和云端)
load_dotenv()
api_key = None
try:
    if "DEEPSEEK_API_KEY" in st.secrets:
        api_key = st.secrets["DEEPSEEK_API_KEY"]
except Exception:
    pass
if not api_key:
    api_key = os.environ.get("DEEPSEEK_API_KEY")

if not api_key:
    st.error("❌ 未找到 DEEPSEEK_API_KEY。请检查本地 .env 或云端 Secrets。")
    st.stop()

client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")
init_db()

st.set_page_config(page_title="情感陪伴智能体", page_icon="🤖", layout="wide")

# ================= 侧边栏：实验控制面板 =================
with st.sidebar:
    st.header("🔬 实验控制面板")
    
    group = st.radio(
        "当前实验组别：",
        ["B组：有长期记忆 (Experimental)", "A组：无记忆基线 (Control)"],
        help="A组作为对照组，不读取也不写入任何长程记忆；B组为实验组。"
    )
    
    st.divider()
    st.markdown("""
    **📝 评分标准（1-5分）**
    *   **跨轮一致性**：是否自然联系了历史事件？
    *   **共情校准**：语气是否自然，有无过度共情或生硬？
    *   **具体帮助性**：是否给出了有效的建议？
    """)
    
    if st.button("📥 导出当前对话数据 (CSV)"):
        if "messages" in st.session_state and st.session_state.messages:
            df = pd.DataFrame(st.session_state.messages)
            csv = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="下载 CSV",
                data=csv,
                file_name=f"chat_history_{group.split('：')[0]}.csv",
                mime="text/csv",
            )
        else:
            st.warning("当前还没有对话记录哦！")

    st.divider()
    # 新增：查看数据库按钮
    if st.button("👀 查看 / 隐藏后台数据库"):
        st.session_state.show_db = not st.session_state.get("show_db", False)

# ================= 主界面基础设置 =================
st.title(f"🤖 情感陪伴智能体 ({group.split('：')[0]})")

if group.startswith("A组"):
    USER_ID = "test_user_A"
else:
    USER_ID = "test_user_B"

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())[:8]
if "messages" not in st.session_state:
    st.session_state.messages = []

# ================= 核心函数定义 =================
def extract_memory(text):
    prompt = f"""
    分析用户输入，提取情感事件和用户偏好。请严格遵循以下规则：
    1. 只要用户表达了对某事物的【喜欢、讨厌、爱、偏好】，必须在 user_preference 中提取出来（例如：“喜欢霉霉的歌”）。如果有多个，用逗号分隔。
    2. 如果用户描述了【已经发生的、带情绪的客观事件】，has_event 设为 true，并提取 event_summary。
    3. 如果一句话里两者都有，同时提取。
    输出严格 JSON：
    {{
        "has_event": true/false,
        "event_summary": "...",
        "emotion_label": "...",
        "intensity": 0.5,
        "user_preference": "提取用户的喜好或厌恶（如：喜欢火锅、讨厌香菜），如果有多个请用逗号分隔，没有则为空字符串"
    }}
    用户输入：{text}
    """
    res = client.chat.completions.create(
        model="deepseek-flash",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}
    )
    try:
        return json.loads(res.choices[0].message.content)
    except:
        return {"has_event": False, "user_preference": ""}

def generate_response(text, use_memory):
    system_prompt = """
    你是一个温暖、自然、像真人朋友一样的陪伴助手。请遵循以下规则：
    1. 语气要口语化、自然，不要像客服机器人那样生硬。不要一上来就“抱歉”、“我理解你的感受”。
    2. 不要强行关联毫无关系的历史记忆，自然交流即可。
    3. 如果用户情绪低落，先共情，再给一点温和的建议或转移注意力；如果用户开心，就跟着一起开心。
    """
    
    if use_memory:
        memory_context = retrieve_relevant_events(USER_ID, text)
        profile_str = get_user_profile(USER_ID)
        
        if profile_str:
            try:
                pref_list = json.loads(profile_str)
                readable_pref = "、".join(pref_list)
                system_prompt += f"\n【用户画像】用户喜欢/讨厌: {readable_pref}"
            except:
                system_prompt += f"\n【用户画像】{profile_str}"
                
        if memory_context:
            system_prompt += f"\n【用户记忆】{memory_context}"
        
    res = client.chat.completions.create(
        model="deepseek-flash",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        temperature=0.8
    )
    return res.choices[0].message.content

# ================= 动态界面展示（查表 OR 聊天） =================
if st.session_state.get("show_db", False):
    # 处于“查看数据库”模式
    st.subheader("🗄️ 当前云端数据库实时数据")
    st.info(f"当前查询用户 ID: {USER_ID} （注：免费云端环境重启后数据会重置）")
    
    conn = get_connection()
    
    # 表 1
    st.markdown("#### 1. 👤 用户画像表 (`user_profile`)")
    df_profile = pd.read_sql_query(f"SELECT * FROM user_profile WHERE user_id = '{USER_ID}'", conn)
    if not df_profile.empty:
        st.dataframe(df_profile, use_container_width=True)
    else:
        st.warning("暂无数据")

    # 表 2
    st.markdown("#### 2. 🧠 情感事件表 (`emotional_events`)")
    df_events = pd.read_sql_query(f"SELECT * FROM emotional_events WHERE user_id = '{USER_ID}'", conn)
    if not df_events.empty:
        st.dataframe(df_events, use_container_width=True)
    else:
        st.warning("暂无数据")

    # 表 3
    st.markdown("#### 3. 💬 对话流水表 (`conversation_turns`)")
    df_turns = pd.read_sql_query(f"SELECT * FROM conversation_turns WHERE user_id = '{USER_ID}'", conn)
    if not df_turns.empty:
        st.dataframe(df_turns, use_container_width=True)
    else:
        st.warning("暂无数据")

    conn.close()
    
    if st.button("⬅️ 退出查看，返回对话"):
        st.session_state.show_db = False
        st.rerun()
    st.divider() 

else:
    # 处于“正常聊天”模式
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("想聊点什么？"):
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        add_conversation_turn(USER_ID, st.session_state.session_id, "user", prompt)

        with st.spinner("思考中..."):
            use_memory = (group == "B组：有长期记忆 (Experimental)")
            
            if use_memory:
                memory_data = extract_memory(prompt)
                if memory_data.get("has_event"):
                    add_emotional_event(USER_ID, memory_data["event_summary"], memory_data["emotion_label"], memory_data["intensity"])
                if memory_data.get("user_preference"):
                    update_user_profile(USER_ID, memory_data["user_preference"])

            response_text = generate_response(prompt, use_memory)
            add_conversation_turn(USER_ID, st.session_state.session_id, "assistant", response_text)

        with st.chat_message("assistant"):
            st.markdown(response_text)
        st.session_state.messages.append({"role": "assistant", "content": response_text})
