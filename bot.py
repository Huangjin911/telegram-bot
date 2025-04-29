from telegram import Update, Dice
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import random
import sqlite3
from datetime import datetime, timedelta

# 数据库初始化
def init_db():
    conn = sqlite3.connect("points.db")
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        points INTEGER DEFAULT 0,
        last_message TIMESTAMP,
        consecutive_wins INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS achievements (
        user_id INTEGER,
        achievement_name TEXT,
        unlocked INTEGER DEFAULT 0,
        progress INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, achievement_name)
    )""")
    conn.commit()
    conn.close()

# 获取用户积分
def get_points(user_id):
    conn = sqlite3.connect("points.db")
    c = conn.cursor()
    c.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else 0

# 更新积分
def update_points(user_id, username, points_change):
    conn = sqlite3.connect("points.db")
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO users (user_id, username, points) VALUES (?, ?, COALESCE((SELECT points FROM users WHERE user_id = ?) + ?, ?))",
              (user_id, username, user_id, points_change, points_change))
    conn.commit()
    check_achievements(user_id, "points")
    conn.close()

# 检查成就
def check_achievements(user_id, trigger_type):
    conn = sqlite3.connect("points.db")
    c = conn.cursor()
    achievements = [
        ("新手玩家", lambda: trigger_type == "play", 10),
        ("连胜大师", lambda: c.execute("SELECT consecutive_wins FROM users WHERE user_id = ?", (user_id,)).fetchone()[0] >= 3, 50),
        ("积分达人", lambda: get_points(user_id) >= 100, 20),
        ("活跃分子", lambda: c.execute("SELECT progress FROM achievements WHERE user_id = ? AND achievement_name = ?", (user_id, "活跃分子")).fetchone()[0] >= 50, 30),
        ("大赢家", lambda: trigger_type == "big_win", 40)
    ]
    messages = []
    for name, condition, reward in achievements:
        c.execute("SELECT unlocked FROM achievements WHERE user_id = ? AND achievement_name = ?", (user_id, name))
        if c.fetchone() is None:
            c.execute("INSERT INTO achievements (user_id, achievement_name, progress) VALUES (?, ?, 0)", (user_id, name))
        c.execute("SELECT unlocked FROM achievements WHERE user_id = ? AND achievement_name = ?", (user_id, name))
        if c.fetchone()[0] == 0 and condition():
            c.execute("UPDATE achievements SET unlocked = 1 WHERE user_id = ? AND achievement_name = ?", (user_id, name))
            update_points(user_id, None, reward)
            messages.append(f"🎉 恭喜解锁成就：{name}！奖励 {reward} 积分！")
    conn.commit()
    conn.close()
    return messages

# 检查管理员
async def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    admins = await context.bot.get_chat_administrators(chat_id)
    return any(admin.user.id == user_id for admin in admins)

# 启动命令
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("欢迎体验分分彩机器人！\n/play [数字] [积分] 参与游戏\n/balance 查看积分\n/achievements 查看成就\n/leaderboard 排行榜")

# 发言加积分
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.startswith("/"):
        return
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    conn = sqlite3.connect("points.db")
    c = conn.cursor()
    c.execute("SELECT last_message FROM users WHERE user_id = ?", (user_id,))
    last_message = c.fetchone()
    now = datetime.now()
    if not last_message or (now - datetime.fromisoformat(last_message[0])).total_seconds() > 60:
        update_points(user_id, username, 1)
        c.execute("UPDATE users SET last_message = ? WHERE user_id = ?", (now.isoformat(), user_id))
        c.execute("UPDATE achievements SET progress = progress + 1 WHERE user_id = ? AND achievement_name = ?", (user_id, "活跃分子"))
        conn.commit()
        for msg in check_achievements(user_id, "message"):
            await update.message.reply_text(msg)
    conn.close()

# 分分彩游戏
async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if len(args) != 2:
        await update.message.reply_text("格式：/play [3位数字] [积分]，如 /play 123 10")
        return
    try:
        bet_number = int(args[0])
        points = int(args[1])
        if not (0 <= bet_number <= 999 and points > 0):
            raise ValueError
    except ValueError:
        await update.message.reply_text("请输入有效3位数字（000-999）和正整数积分！")
        return
    current_points = get_points(user_id)
    if points > current_points:
        await update.message.reply_text("积分不足！")
        return
    result = random.randint(0, 999)
    await update.message.reply_dice(emoji="🎲")
    update_points(user_id, update.effective_user.username, -points)
    conn = sqlite3.connect("points.db")
    c = conn.cursor()
    if bet_number == result:
        winnings = points * 10
        update_points(user_id, update.effective_user.username, winnings)
        c.execute("UPDATE users SET consecutive_wins = consecutive_wins + 1 WHERE user_id = ?", (user_id,))
        if winnings >= 500:
            check_achievements(user_id, "big_win")
        await update.message.reply_text(f"🎉 恭喜！结果：{result:03d}，完全匹配，赢得 {winnings} 积分！")
    elif str(bet_number)[:2] == str(result)[:2]:
        winnings = points * 2
        update_points(user_id, update.effective_user.username, winnings)
        c.execute("UPDATE users SET consecutive_wins = 0 WHERE user_id = ?", (user_id,))
        await update.message.reply_text(f"👍 结果：{result:03d}，前两位匹配，赢得 {winnings} 积分！")
    else:
        c.execute("UPDATE users SET consecutive_wins = 0 WHERE user_id = ?", (user_id,))
        await update.message.reply_text(f"😅 结果：{result:03d}，未中奖，扣除 {points} 积分。")
    conn.commit()
    conn.close()
    await update.message.reply_text(f"当前积分：{get_points(user_id)}")
    for msg in check_achievements(user_id, "play"):
        await update.message.reply_text(msg)

# 查看积分
async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    points = get_points(update.effective_user.id)
    await update.message.reply_text(f"你的积分：{points}")

# 查看成就
async def achievements(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect("points.db")
    c = conn.cursor()
    c.execute("SELECT achievement_name, unlocked FROM achievements WHERE user_id = ?", (user_id,))
    ach_list = c.fetchall()
    conn.close()
    if not ach_list:
        await update.message.reply_text("你还没有成就，快去玩游戏解锁吧！")
        return
    message = "🏅 你的成就：\n"
    for name, unlocked in ach_list:
        message += f"{name}: {'已解锁' if unlocked else '未解锁'}\n"
    await update.message.reply_text(message)

# 管理员加分
async def add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("仅管理员可使用此命令！")
        return
    args = context.args
    if len(args) != 2 or not args[0].startswith("@"):
        await update.message.reply_text("格式：/addpoints @用户名 积分数")
        return
    try:
        points = int(args[1])
        if points > 1000 or points < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("积分需为0-1000的整数！")
        return
    username = args[0][1:]
    conn = sqlite3.connect("points.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE username = ?", (username,))
    user = c.fetchone()
    if not user:
        await update.message.reply_text("用户不存在！")
        conn.close()
        return
    update_points(user[0], username, points)
    conn.close()
    await update.message.reply_text(f"已为 @{username} 添加 {points} 积分！")

# 排行榜
async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect("points.db")
    c = conn.cursor()
    c.execute("SELECT username, points FROM users ORDER BY points DESC LIMIT 5")
    top_users = c.fetchall()
    conn.close()
    if not top_users:
        await update.message.reply_text("暂无排行数据！")
        return
    message = "🏆 积分排行榜 🏆\n"
    for i, (username, points) in enumerate(top_users, 1):
        message += f"{i}. @{username}: {points} 积分\n"
    await update.message.reply_text(message)

def main():
    init_db()
    app = Application.builder().token("8137040207:AAH_MLmXOol3sQLNmOgfnabrywb4clZaVLg").build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("play", play))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("achievements", achievements))
    app.add_handler(CommandHandler("addpoints", add_points))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
