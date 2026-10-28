# schemas.py存放数据结构定义，使用dataclass来定义PerceptionPacket和ExpressionPlan两个类，分别用于表示感知数据包（输入）和表达计划（输出）
from dataclasses import dataclass, field
from datetime import datetime
import uuid

@dataclass
class PerceptionPacket:
    text: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    user_emotion_hint: str | None = None

@dataclass
class ExpressionPlan:
    text: str
    emotion_tag: str = "neutral"
    intensity: float = 0.5
    gesture_hint: str | None = None