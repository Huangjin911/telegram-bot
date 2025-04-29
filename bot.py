from telegram import Update, Dice, BotCommand
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
    try:
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        admins = await context.bot.get_chat_administrators(chat_id)
        return any(admin.user.id == user_id for admin in admins)
    except Exception as e:
        await update.message.reply_text(f"检查管理员权限失败：{str(e)}")
        return False

# 设置指令菜单
async def set_bot_commands(application: Application):
    commands = [
        BotCommand("start", "开始使用，查看玩法"),
        BotCommand("play", "参与游戏：和值、三同号、二同号、单骰、大小单双"),
        BotCommand("balance", "查看积分"),
        BotCommand("achievements", "查看成就"),
        BotCommand("leaderboard", "查看排行榜"),
        BotCommand("addpoints", "管理员：为用户加分"),
        BotCommand("addallpoints", "管理员：为所有人加分")
    ]
    await application.bot.set_my_commands(commands)

# 启动命令
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("欢迎体验分分彩机器人！点击菜单（📋）查看玩法：\n/play sum 4~17 [积分] 猜总和\n/play triple 1~6/any [积分] 三同号\n/play pair X-X-Y/any [积分] 二同号\n/play single 1~6 [积分] 猜点数\n/play size big/small [积分] 总和大小\n/play parity odd/even [积分] 总和单双\n/balance 查看积分\n/achievements 查看成就\n/leaderboard 排行榜\n/addpoints @用户名 [积分] 管理员加分\n/addallpoints [积分] 给所有人加分")

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

# 分分彩游戏（3个骰子）
async def play(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    if len(args) != 3:
        await update.message.reply_text("格式：/play [玩法] [参数] [积分]\n示例：/play sum 4 10 或 /play triple 6 10")
        return
    mode = args[0].lower()
    bet = args[1].lower()
    try:
        points = int(args[2])
        if points <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("积分需为正整数！")
        return
    current_points = get_points(user_id)
    if points > current_points:
        await update.message.reply_text("积分不足！")
        return

    # 生成3个骰子
    dice1 = random.randint(1, 6)
    dice2 = random.randint(1, 6)
    dice3 = random.randint(1, 6)
    total = dice1 + dice2 + dice3
    result = [dice1, dice2, dice3]
    # 发送3个骰子动画
    await update.message.reply_dice(emoji="🎲")
    await update.message.reply_dice(emoji="🎲")
    await update.message.reply_dice(emoji="🎲")
    
    update_points(user_id, update.effective_user.username, -points)
    conn = sqlite3.connect("points.db")
    c = conn.cursor()
    
    if mode == "size":
        if bet not in ["big", "small"]:
            await update.message.reply_text("请猜 big 或 small！")
            return
        is_big = total >= 11
        result_str = "big" if is_big else "small"
        await update.message.reply_text(f"🎲 结果：{dice1}-{dice2}-{dice3} (总和 {total})，{'大' if is_big else '小'}")
        if bet == result_str:
            winnings = int(points * 1.8)
            update_points(user_id, update.effective_user.username, winnings)
            await update.message.reply_text(f"🎉 猜对！赢得 {winnings} 积分！")
            if winnings >= 500:
                check_achievements(user_id, "big_win")
        else:
            await update.message.reply_text(f"😅 猜错！扣除 {points} 积分。")
    
    elif mode == "parity":
        if bet not in ["odd", "even"]:
            await update.message.reply_text("请猜 odd 或 even！")
            return
        is_even = total % 2 == 0
        result_str = "even" if is_even else "odd"
        await update.message.reply_text(f"🎲 结果：{dice1}-{dice2}-{dice3} (总和 {total})，{'双' if is_even else '单'}")
        if bet == result_str:
            winnings = int(points * 1.8)
            update_points(user_id, update.effective_user.username, winnings)
            await update.message.reply_text(f"🎉 猜对！赢得 {winnings} 积分！")
            if winnings >= 500:
                check_achievements(user_id, "big_win")
        else:
            await update.message.reply_text(f"😅 猜错！扣除 {points} 积分。")
    
    elif mode == "sum":
        try:
            bet_sum = int(bet)
            if bet_sum < 3 or bet_sum > 18:
                raise ValueError
        except ValueError:
            await update.message.reply_text("总和需为 3-18 的整数！")
            return
        await update.message.reply_text(f"🎲 结果：{dice1}-{dice2}-{dice3} (总和 {total})")
        if bet_sum == total:
            odds = {4: 50, 17: 50, 5: 18, 16: 18, 6: 14, 15: 14, 7: 12, 14: 12, 8: 8, 13: 8, 9: 6, 12: 6, 10: 6, 11: 6}
            winnings = points * odds.get(total, 6)
            update_points(user_id, update.effective_user.username, winnings)
            await update.message.reply_text(f"🎉 猜对总和！赢得 {winnings} 积分！")
            if winnings >= 500:
                check_achievements(user_id, "big_win")
        else:
            await update.message.reply_text(f"😅 猜错！扣除 {points} 积分。")
    
    elif mode == "triple":
        is_triple = dice1 == dice2 == dice3
        await update.message.reply_text(f"🎲 结果：{dice1}-{dice2}-{dice3}")
        if bet == "any":
            if is_triple:
                winnings = points * 30
                update_points(user_id, update.effective_user.username, winnings)
                await update.message.reply_text(f"🎉 三同号通选中奖！赢得 {winnings} 积分！")
                if winnings >= 500:
                    check_achievements(user_id, "big_win")
            else:
                await update.message.reply_text(f"😅 未开豹子！扣除 {points} 积分。")
        else:
            try:
                bet_num = int(bet)
                if bet_num < 1 or bet_num > 6:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("三同号需为 1-6 或 any！")
                return
            if is_triple and dice1 == bet_num:
                winnings = points * 150
                update_points(user_id, update.effective_user.username, winnings)
                c.execute("UPDATE users SET consecutive_wins = consecutive_wins + 1 WHERE user_id = ?", (user_id,))
                await update.message.reply_text(f"🎉 三同号单选中奖！赢得 {winnings} 积分！")
                if winnings >= 500:
                    check_achievements(user_id, "big_win")
            else:
                c.execute("UPDATE users SET consecutive_wins = 0 WHERE user_id = ?", (user_id,))
                await update.message.reply_text(f"😅 未中！扣除 {points} 积分。")
    
    elif mode == "pair":
        await update.message.reply_text(f"🎲 结果：{dice1}-{dice2}-{dice3}")
        if bet == "any":
            if len(set(result)) == 2:
                winnings = points * 5
                update_points(user_id, update.effective_user.username, winnings)
                await update.message.reply_text(f"🎉 二同号复选中奖！赢得 {winnings} 积分！")
                if winnings >= 500:
                    check_achievements(user_id, "big_win")
            else:
                await update.message.reply_text(f"😅 未开对子！扣除 {points} 积分。")
        else:
            try:
                bet_numbers = [int(x) for x in bet.split("-")]
                if len(bet_numbers) != 3 or not all(1 <= x <= 6 for x in bet_numbers):
                    raise ValueError
            except ValueError:
                await update.message.reply_text("二同号需为 X-X-Y 格式，如 1-1-2！")
                return
            sorted_bet = sorted(bet_numbers)
            sorted_result = sorted(result)
            if sorted_bet == sorted_result and len(set(bet_numbers)) == 2:
                winnings = points * 25
                update_points(user_id, update.effective_user.username, winnings)
                c.execute("UPDATE users SET consecutive_wins = consecutive_wins + 1 WHERE user_id = ?", (user_id,))
                await update.message.reply_text(f"🎉 二同号单选中奖！赢得 {winnings} 积分！")
                if winnings >= 500:
                    check_achievements(user_id, "big_win")
            else:
                c.execute("UPDATE users SET consecutive_wins = 0 WHERE user_id = ?", (user_id,))
                await update.message.reply_text(f"😅 未中！扣除 {points} 积分。")
    
    elif mode == "single":
        try:
            bet_num = int(bet)
            if bet_num < 1 or bet_num > 6:
                raise ValueError
        except ValueError:
            await update.message.reply_text("猜点数需为 1-6 的整数！")
            return
        count = result.count(bet_num)
        await update.message.reply_text(f"🎲 结果：{dice1}-{dice2}-{dice3}")
        if count > 0:
            winnings = points * count
            update_points(user_id, update.effective_user.username, winnings)
            await update.message.reply_text(f"🎉 猜中 {count} 次！赢得 {winnings} 积分！")
            if winnings >= 500:
                check_achievements(user_id, "big_win")
        else:
            await update.message.reply_text(f"😅 未猜中！扣除 {points} 积分。")
    
    else:
        await update.message.reply_text("玩法错误！请用 size, parity, sum, triple, pair 或 single。")
        return
    
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

# 管理员给单一用户加分
async def add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("仅管理员可使用此命令！")
        return
    args = context.args
    if len(args) != 2 or not args[0].startswith("@"):
        await update.message.reply_text("格式：/addpoints @用户名 积分数\n示例：/addpoints @user 100")
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
        await update.message.reply_text(f"用户 @{username} 不存在！请确认用户已发言或参与游戏。")
        conn.close()
        return
    update_points(user[0], username, points)
    conn.close()
    await update.message.reply_text(f"已为 @{username} 添加 {points} 积分！")

# 管理员给所有人加分
async def add_all_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update, context):
        await update.message.reply_text("仅管理员可使用此命令！")
        return
    args = context.args
    if len(args) != 1:
        await update.message.reply_text("格式：/addallpoints 积分数\n示例：/addallpoints 50")
        return
    try:
        points = int(args[0])
        if points > 1000 or points < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("积分需为0-1000的整数！")
        return
    conn = sqlite3.connect("points.db")
    c = conn.cursor()
    c.execute("SELECT user_id, username FROM users")
    users = c.fetchall()
    if not users:
        await update.message.reply_text("暂无用户记录！")
        conn.close()
        return
    for user_id, username in users:
        update_points(user_id, username, points)
    conn.close()
    await update.message.reply_text(f"已为所有 {len(users)} 名用户各添加 {points} 积分！")

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
    # 设置指令菜单
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("play", play))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("achievements", achievements))
    app.add_handler(CommandHandler("addpoints", add_points))
    app.add_handler(CommandHandler("addallpoints", add_all_points))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    # 异步设置命令菜单
    import asyncio
    asyncio.run(set_bot_commands(app))
    app.run_polling()

if __name__ == "__main__":
    main()
