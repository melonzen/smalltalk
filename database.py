# database.py，存放建表和后续的读写逻辑
# 这个文件负责数据库的初始化和基本操作，包括存储和检索用户的情感事件、对话历史以及用户画像。
# 采用 Python 自带的轻量级数据库 SQLite，无需额外配置服务端。

import sqlite3
import json
from datetime import datetime

# 数据库文件路径。如果当前目录下没有这个文件，SQLite 会自动创建。
DB_PATH = "companion.db"

def get_connection():
    """
    获取数据库连接。
    每次操作都新建一个连接并在操作后关闭，避免多线程（如 Streamlit 后台）下的锁死问题。
    """
    return sqlite3.connect(DB_PATH)

def init_db():
    """
    初始化数据库表结构。
    如果表已经存在，则跳过创建（IF NOT EXISTS）。保证每次运行代码时表结构是就绪的。
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 1. 用户画像表：记录用户的长期基础偏好（如称呼、喜好、厌恶等）
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_profile (
        user_id TEXT PRIMARY KEY,  -- 用户唯一标识
        display_name TEXT,         -- 昵称（预留字段）
        preferences TEXT,          -- 偏好列表，以 JSON 字符串格式存储（如 ["喜欢火锅", "讨厌香菜"]）
        updated_at TEXT            -- 最后更新时间
    )
    """)

    # 2. 情感事件表：记录对话中发生的关键情感事件（如“今天面试很焦虑”）
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS emotional_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, -- 自增主键
        user_id TEXT,              -- 属于哪个用户
        event_summary TEXT,        -- 事件摘要（如“下周有面试”）
        emotion_label TEXT,        -- 情绪标签（如“焦虑”）
        intensity REAL,            -- 情绪强度（0.0 - 1.0，预留）
        event_time TEXT,           -- 发生时间
        is_active INTEGER DEFAULT 1 -- 是否有效（1=有效，0=已遗忘/已解决），用于软删除
    )
    """)

    # 3. 对话轮次表：记录完整的对话流水，用于日后复盘或计算 Token
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS conversation_turns (
        turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        session_id TEXT,           -- 会话 ID，用于区分同一天的不同聊天窗口
        role TEXT,                 -- 角色（"user" 或 "assistant"）
        content TEXT,              -- 聊天内容
        turn_time TEXT,            -- 时间戳
        linked_event_id INTEGER    -- 关联的情感事件 ID（预留，方便追溯某句话对应哪个事件）
    )
    """)

    conn.commit()
    conn.close()
    print("✅ 数据库和表已初始化完成。")


# ================= 情感事件表操作 =================

def add_emotional_event(user_id, event_summary, emotion_label, intensity):
    """将一条情感事件写入数据库"""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    
    # 插入新事件，默认 is_active = 1（有效）
    cursor.execute("""
        INSERT INTO emotional_events (user_id, event_summary, emotion_label, intensity, event_time, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
    """, (user_id, event_summary, emotion_label, intensity, now))
    
    conn.commit()
    conn.close()
    print(f"💾 [记忆写入] 已记住: {event_summary} ({emotion_label})")


def retrieve_relevant_events(user_id, current_text, limit=2):
    """
    检索相关的情感记忆。
    当前采用最简单的策略：按时间倒序（最近发生的优先）取出有效事件。
    未来可在这里加入关键词匹配或向量检索。
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    # 按时间倒序取出最近 5 条有效事件（多取一点备用）
    cursor.execute("""
        SELECT DISTINCT event_summary, emotion_label FROM emotional_events 
        WHERE user_id = ? AND is_active = 1
        ORDER BY event_time DESC LIMIT 5
    """, (user_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return ""
    
    # 拼装成大模型能读懂的自然语言提示
    relevant_memories = []
    for summary, label in rows:
        relevant_memories.append(f"- 用户过去提到过：{summary}（情绪：{label}）")
    
    # 限制返回给大模型的数量（默认最多 2 条），防止上下文过长
    return "\n".join(relevant_memories[:limit])


# ================= 对话流水表操作 =================

def add_conversation_turn(user_id, session_id, role, content):
    """将每一句对话（用户或 AI）存入数据库，方便日后调取历史记录"""
    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO conversation_turns (user_id, session_id, role, content, turn_time)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, session_id, role, content, now))
    conn.commit()
    conn.close()


# ================= 用户画像表操作 =================

def get_user_profile(user_id):
    """获取用户的画像（拿到的是 JSON 字符串）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT preferences FROM user_profile WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    # 如果查到了，返回字符串；没查到返回空字符串
    return row[0] if row else ""


def update_user_profile(user_id, new_preference):
    """
    将新的偏好追加到用户画像中。
    【重点】：新增了按逗号拆分和去重的逻辑，确保画像是一个清晰的列表，而不是一大段糊在一起的句子。
    """
    if not new_preference:
        return

    conn = get_connection()
    cursor = conn.cursor()
    now = datetime.now().isoformat()
    
    # 1. 先查出该用户现有的偏好
    cursor.execute("SELECT preferences FROM user_profile WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    # 2. 解析现有的 JSON 字符串
    if row and row[0]:
        try:
            pref_list = json.loads(row[0]) # 尝试把 '["喜欢火锅"]' 解析成 Python 列表
            if not isinstance(pref_list, list):
                pref_list = [row[0]] # 如果老数据不是列表格式，强行包成列表
        except json.JSONDecodeError:
            pref_list = [row[0]] # 解析失败（说明是早期存的纯文本），直接当字符串放进列表
    else:
        pref_list = [] # 如果是新用户，初始化空列表
        
    # 3. 核心修复：将模型返回的一句话（如“喜欢火锅，讨厌香菜”）按逗号拆开
    # 这里兼容了中文逗号 '，' 和英文逗号 ','，并且去除了两端的空格
    new_prefs = [p.strip() for p in new_preference.replace('，', ',').split(',') if p.strip()]
    
    # 4. 遍历拆分后的新偏好，逐个检查并追加（去重）
    updated = False
    for p in new_prefs:
        if p not in pref_list:
            pref_list.append(p)
            updated = True
            
    # 如果这次提取的偏好全都在数据库里了，就不需要更新数据库，直接返回
    if not updated:
        print(f"👤 [画像更新] 偏好已存在，跳过: {new_prefs}")
        conn.close()
        return

    # 5. 将更新后的列表重新转回 JSON 字符串，准备存回数据库
    updated_preferences = json.dumps(pref_list, ensure_ascii=False)
    
    # 6. 如果之前有记录，执行 UPDATE；如果没有，执行 INSERT
    if row:
        cursor.execute("UPDATE user_profile SET preferences = ?, updated_at = ? WHERE user_id = ?", (updated_preferences, now, user_id))
    else:
        cursor.execute("INSERT INTO user_profile (user_id, preferences, updated_at) VALUES (?, ?, ?)", (updated_preferences, now, user_id))
        
    conn.commit()
    conn.close()
    print(f"👤 [画像更新] 成功追加偏好: {new_prefs}，当前画像: {pref_list}")


# 如果直接运行这个文件，则执行初始化（方便测试）
if __name__ == "__main__":
    init_db()
