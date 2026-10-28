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

# 1. 加载本地环境变量
load_dotenv()

# 2. 安全地读取 API Key（优先云端，其次本地）
api_key = None
try:
    if "DEEPSEEK_API_KEY" in st.secrets:
        api_key = st.secrets["DEEPSEEK_API_KEY"]
except Exception:
    pass

if not api_key:
    api_key = os.environ.get("DEEPSEEK_API_KEY")

if not api_key:
    st.error("❌ 未找到 DEEPSEEK_API_KEY。请检查本地 .env 文件或在 Streamlit Cloud 的 Secrets 中配置是否正确。")
    st.stop()

# 3. 初始化客户端
client = OpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com",
)

# 4. 初始化数据库
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
    # 查看数据库按钮
    if st.button("👀 查看 / 隐藏后台数据库"):
        st.session_state.show_db = not st.session_state.get("show_db", False)


# ================= 函数定义区 =================
# 在 app.py 中找到这个函数
def extract_memory(text):
    prompt = f"""
    你是一个记忆提取器。请仔细分析用户的输入。
    1. 判断是否包含情感事件。
    2. 判断是否包含用户的【喜好、厌恶、习惯或称呼】。
    
    你必须输出严格的 JSON 格式：
    {{
        "has_event": true/false,
        "event_summary": "简短总结发生的事，如果没有则为空",
        "emotion_label": "情绪标签（如开心、焦虑），如果没有则为空",
        "intensity": 0.5,
        "user_preference": "提取用户的喜好或厌恶（如：喜欢火锅、讨厌香菜）。如果有多个请用逗号分隔。如果用户明确说“请记住”、“我的爱好是”，必须提取。没有则为空字符串"
    }}
    
    用户输入：{text}
    """
    res = client.chat.completions.create(
        model="deepseek-flash",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.1  # 👇 加上这个，让提取更严谨
    )
    try:
        return json.loads(res.choices[0].message.content)
    except:
        return {"has_event": False, "user_preference": ""}

def generate_response(text, use_memory, user_id):
    system_prompt = """
    你是一个温暖、自然、像真人朋友一样的陪伴助手。请遵循以下规则：
    1. 语气要口语化、自然，不要像客服机器人那样生硬。不要一上来就“抱歉”、“我理解你的感受”。
    2. 不要强行关联毫无关系的历史记忆，自然交流即可。
    3. 如果用户情绪低落，先共情，再给一点温和的建议或转移注意力；如果用户开心，就跟着一起开心。
    """
    
    if use_memory:
        memory_context = retrieve_relevant_events(user_id, text)
        profile_str = get_user_profile(user_id)
        
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


# 👇 状态控制区：如果开关是打开的，展示数据库 👇
if st.session_state.get("show_db", False):
    st.subheader("🗄️ 当前云端数据库实时数据")
    st.info(f"当前查询用户 ID: {USER_ID}")
    
    conn = get_connection()
    cursor = conn.cursor()
    
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("** 情感事件表 (emotional_events)**")
        cursor.execute("SELECT event_summary, emotion_label, event_time FROM emotional_events WHERE user_id = ?", (USER_ID,))
        events = cursor.fetchall()
        if events:
            st.dataframe(pd.DataFrame(events, columns=["事件摘要", "情绪", "时间"]), use_container_width=True)
        else:
            st.info("暂无情感事件")
            
    with col2:
        st.markdown("**👤 用户画像表 (user_profile)**")
        cursor.execute("SELECT preferences, updated_at FROM user_profile WHERE user_id = ?", (USER_ID,))
        profile = cursor.fetchall()
        if profile:
            st.dataframe(pd.DataFrame(profile, columns=["偏好设定", "更新时间"]), use_container_width=True)
        else:
            st.info("暂无用户画像")
    conn.close()
    
    if st.button("⬅️ 退出查看，返回对话"):
        st.session_state.show_db = False
        st.rerun()
    st.divider() 


# 👇 聊天区：只有在“不查看数据库”的时候，才显示聊天界面 👇
if not st.session_state.get("show_db", False):
    
    # 渲染历史对话
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # 接收输入
    if prompt := st.chat_input("想聊点什么？"):
        with st.chat_message("user"):
            st.markdown(prompt)
        st.session_state.messages.append({"role": "user", "content": prompt})
        
        add_conversation_turn(USER_ID, st.session_state.session_id, "user", prompt)

        with st.spinner("思考中..."):
            use_memory = (group == "B组：有长期记忆 (Experimental)")
            
            if use_memory:
                memory_data = extract_memory(prompt)
                st.toast(f"后台提取: {memory_data}")
                if memory_data.get("has_event"):
                    add_emotional_event(USER_ID, memory_data["event_summary"], memory_data["emotion_label"], memory_data["intensity"])
                if memory_data.get("user_preference"):
                    update_user_profile(USER_ID, memory_data["user_preference"])

            # 注意：这里我加了一个参数 USER_ID，防止函数内部找不到变量
            response_text = generate_response(prompt, use_memory, USER_ID)
            
            add_conversation_turn(USER_ID, st.session_state.session_id, "assistant", response_text)

        with st.chat_message("assistant"):
            st.markdown(response_text)
        st.session_state.messages.append({"role": "assistant", "content": response_text})
