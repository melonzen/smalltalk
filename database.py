# database.py，存放建表和后续的读写逻辑
# 这个文件负责数据库的初始化和基本操作，包括存储和检索用户的情感事件。
import sqlite3
import json

DB_PATH = "companion.db"

def get_connection():
    return sqlite3.connect(DB_PATH)

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # 1. 用户画像表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_profile (
        user_id TEXT PRIMARY KEY,
        display_name TEXT,
        preferences TEXT,
        updated_at TEXT
    )
    """)

    # 2. 情感事件表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS emotional_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        event_summary TEXT,
        emotion_label TEXT,
        intensity REAL,
        event_time TEXT,
        is_active INTEGER DEFAULT 1
    )
    """)

    # 3. 对话轮次表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS conversation_turns (
        turn_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        session_id TEXT,
        role TEXT,
        content TEXT,
        turn_time TEXT,
        linked_event_id INTEGER
    )
    """)

    conn.commit()
    conn.close()
    print("✅ 数据库和表已初始化完成。")

# 存记忆和取记忆的两个函数，用最简单的方式SQL的LIKE匹配和 ORDER BY 实现，后续可以升级为更复杂的检索逻辑

#存记忆
def add_emotional_event(user_id, event_summary, emotion_label, intensity):
    """将一条情感事件写入数据库"""
    conn = get_connection()
    cursor = conn.cursor()
    from datetime import datetime
    now = datetime.now().isoformat()
    
    cursor.execute("""
        INSERT INTO emotional_events (user_id, event_summary, emotion_label, intensity, event_time, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
    """, (user_id, event_summary, emotion_label, intensity, now))
    
    conn.commit()
    conn.close()
    print(f"💾 [记忆写入] 已记住: {event_summary} ({emotion_label})")


#取记忆，检索
def retrieve_relevant_events(user_id, current_text, limit=2):
    conn = get_connection()
    cursor = conn.cursor()
    
    # 提取当前文本里长度大于1的词（简单粗暴的过滤）
    # 如果没匹配上，就不提取记忆，避免强行关联
    cursor.execute("""
        SELECT DISTINCT event_summary, emotion_label FROM emotional_events 
        WHERE user_id = ? AND is_active = 1
        ORDER BY event_time DESC LIMIT 5
    """, (user_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        return ""
    
    # 简单的相关性判断：如果当前输入里包含了记忆里的字，才召回
    relevant_memories = []
    for summary, label in rows:
        # 比如记忆是“膝盖受伤”，当前输入是“跑步”，虽然没直接匹配，但我们可以放行
        # 为了防止过度关联，这里先保留全部，但通过 Prompt 去控制
        relevant_memories.append(f"- 用户过去提到过：{summary}（情绪：{label}）")
    
    # 如果连一条记录都没有，直接返回空
    return "\n".join(relevant_memories[:limit])

# 将对话流水存入数据库

def add_conversation_turn(user_id, session_id, role, content):
    """将对话流水存入数据库"""
    conn = get_connection()
    cursor = conn.cursor()
    from datetime import datetime
    now = datetime.now().isoformat()
    cursor.execute("""
        INSERT INTO conversation_turns (user_id, session_id, role, content, turn_time)
        VALUES (?, ?, ?, ?, ?)
    """, (user_id, session_id, role, content, now))
    conn.commit()
    conn.close()


#用户画像

def get_user_profile(user_id):
    """获取用户的画像（偏好、称呼等）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT preferences FROM user_profile WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else ""


def update_user_profile(user_id, new_preference):
    """将新的偏好追加到用户画像中，自动去重"""
    if not new_preference:
        return

    conn = get_connection()
    cursor = conn.cursor()
    from datetime import datetime
    now = datetime.now().isoformat()
    
    # 1. 先读取现有的偏好
    cursor.execute("SELECT preferences FROM user_profile WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    
    # 2. 如果存在旧数据，尝试解析为 Python 列表；如果解析失败，就初始化空列表
    if row and row[0]:
        try:
            pref_list = json.loads(row[0])
            if not isinstance(pref_list, list):
                pref_list = [row[0]] # 兼容以前存的纯文本
        except json.JSONDecodeError:
            pref_list = [row[0]]
    else:
        pref_list = []
        
    # 3. 追加新偏好并去重（避免“喜欢吃火锅”被存两次）
    if new_preference not in pref_list:
        pref_list.append(new_preference)
    else:
        print(f"👤 [画像更新] 偏好已存在，跳过: {new_preference}")
        conn.close()
        return

    # 4. 将更新后的列表转为 JSON 字符串存回数据库
    updated_preferences = json.dumps(pref_list, ensure_ascii=False)
    
    if row:
        cursor.execute("""
            UPDATE user_profile SET preferences = ?, updated_at = ? WHERE user_id = ?
        """, (updated_preferences, now, user_id))
    else:
        cursor.execute("""
            INSERT INTO user_profile (user_id, preferences, updated_at) VALUES (?, ?, ?)
        """, (updated_preferences, now, user_id))
        
    conn.commit()
    conn.close()
    print(f"👤 [画像更新] 成功追加偏好: {new_preference}，当前画像: {pref_list}")


if __name__ == "__main__":
    init_db()
