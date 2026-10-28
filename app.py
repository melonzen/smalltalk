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

load_dotenv()
init_db()

client = OpenAI(
    api_key=os.environ.get("DEEPSEEK_API_KEY"),
    base_url="https://api.deepseek.com",
)

st.set_page_config(page_title="情感陪伴智能体", page_icon="🤖", layout="wide")

# ================= 侧边栏：实验控制面板 =================
with st.sidebar:
    
    st.header("🔬 实验控制面板")
    
    # 这里就是 A/B 对照开关
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
    
    # 导出数据按钮
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

# ================= 主界面 =================
st.title(f"🤖 情感陪伴智能体 ({group.split('：')[0]})")

if group.startswith("A组"):
    USER_ID = "test_user_A"
else:
    USER_ID = "test_user_B"

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())[:8]

if "messages" not in st.session_state:
    st.session_state.messages = []

def extract_memory(text):
    prompt = f"""
    分析用户输入，提取情感事件和用户偏好。
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
    
    # 【核心控制点】：根据实验组别决定是否读取记忆
    if use_memory:
        memory_context = retrieve_relevant_events(USER_ID, text)
        profile_str = get_user_profile(USER_ID) # 拿到的是 JSON 字符串
        
        if profile_str:
            
            try:
                # 解析成列表，然后用“、”拼接成自然语言
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

# 渲染历史对话
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 接收输入
if prompt := st.chat_input("想聊点什么？"):
    with st.chat_message("user"):
        st.markdown(prompt)
    st.session_state.messages.append({"role": "user", "content": prompt})
    
    # 存入对话流水（两组的聊天记录都要存，方便日后分析）
    add_conversation_turn(USER_ID, st.session_state.session_id, "user", prompt)

    with st.spinner("思考中..."):
        use_memory = (group == "B组：有长期记忆 (Experimental)")
        
        # 【核心控制点】：只有在 B 组，才自动提取并写入记忆数据库
        if use_memory:
            memory_data = extract_memory(prompt)
            if memory_data.get("has_event"):
                add_emotional_event(USER_ID, memory_data["event_summary"], memory_data["emotion_label"], memory_data["intensity"])
            if memory_data.get("user_preference"):
                update_user_profile(USER_ID, memory_data["user_preference"])

        # 生成回复
        response_text = generate_response(prompt, use_memory)
        
        # 存入对话流水
        add_conversation_turn(USER_ID, st.session_state.session_id, "assistant", response_text)

    with st.chat_message("assistant"):
        st.markdown(response_text)
    st.session_state.messages.append({"role": "assistant", "content": response_text})