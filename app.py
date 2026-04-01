import os
import random
from datetime import datetime, timezone, timedelta
import gspread
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi, ReplyMessageRequest,
    TextMessage, QuickReply, QuickReplyItem, MessageAction,
    FlexMessage, FlexContainer
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# --- 1. 設定 LINE & 密碼 ---
configuration = Configuration(access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
line_handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))
VERIFY_PASSWORD = "123" 

# --- 2. 設定 Google Sheets 連線 & 時區 ---
import json # 記得確保最上面有 import json

# --- 2. 設定 Google Sheets 連線 & 時區 ---
google_creds_str = os.getenv('GOOGLE_CREDENTIALS')
if google_creds_str:
    # 雲端 Vercel 模式：從環境變數讀取金鑰
    creds_dict = json.loads(google_creds_str)
    gc = gspread.service_account_from_dict(creds_dict)
else:
    # 本地電腦測試模式：讀取實體檔案
    gc = gspread.service_account(filename='credentials.json')

sh = gc.open('清大文物館_Bot資料庫')
sheet_q = sh.worksheet('題庫')
sheet_s = sh.worksheet('玩家狀態')

MAX_STAGE = 8 
tz = timezone(timedelta(hours=8)) # 設定為台灣時間 (UTC+8)

# --- 3. 展區資料定義 ---
ZONES = {
    1: {"title": "家學與知交：學者之脈，文人之風", "desc": "一窺『學霸家族』深厚的文化底蘊，感受兼容理性與感性的家庭氛圍。", "url": "https://nthumuseum.wixsite.com/yukawa-hideki/%E5%AE%B6%E5%AD%B8%E8%88%87%E7%9F%A5%E4%BA%A4"},
    2: {"title": "性情中的湯川：以詩書觀宇宙", "desc": "看湯川秀樹如何將對『時間、物質與能量』的科學觀察寫進詩歌裡。", "url": "https://nthumuseum.wixsite.com/yukawa-hideki/%E6%80%A7%E6%83%85%E4%B8%AD%E7%9A%84%E6%B9%AF%E5%B7%9D"},
    3: {"title": "入世的湯川：以和平為志業", "desc": "反思科學家的社會責任，感受物理學家對人類社會的深切關懷。", "url": "https://nthumuseum.wixsite.com/yukawa-hideki/%E5%85%A5%E4%B8%96%E7%9A%84%E6%B9%AF%E5%B7%9D"},
    4: {"title": "清大與原子能", "desc": "重返和平用途歷史現場，見證清大原子科學的傳承與蛻變。", "url": "https://nthumuseum.wixsite.com/yukawa-hideki/%E5%8E%9F%E5%AD%90%E8%83%BD%E5%92%8C%E5%B9%B3%E7%94%A8%E9%80%94"}
}

# --- 4. Flex Message 生成器 ---

def create_zone_flex(zone_id):
    z = ZONES[zone_id]
    bubble = {
        "type": "bubble",
        "header": {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": f"第 {zone_id} 展區導覽", "color": "#8b0000", "weight": "bold", "size": "sm"}]},
        "body": {"type": "box", "layout": "vertical", "contents": [
            {"type": "text", "text": z['title'], "weight": "bold", "size": "xl", "wrap": True},
            {"type": "text", "text": z['desc'], "margin": "md", "size": "sm", "color": "#666666", "wrap": True}
        ]},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
            {"type": "button", "action": {"type": "uri", "label": "查看詳細介紹", "uri": z['url']}, "style": "link", "color": "#8b0000"},
            {"type": "button", "action": {"type": "message", "label": "開始本區答題", "text": "確認開始答題"}, "style": "primary", "color": "#8b0000"}
        ]}
    }
    return FlexMessage(alt_text=f"展區介紹: {z['title']}", contents=FlexContainer.from_dict(bubble))

def create_question_flex(q_data):
    bubble = {
        "type": "bubble",
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": [
            {"type": "text", "text": str(q_data['題目']), "wrap": True, "weight": "bold", "size": "lg"},
            {"type": "separator"},
            {"type": "text", "text": f"A. {q_data.get('選項A', '')}", "wrap": True, "size": "md", "color": "#333333"},
            {"type": "text", "text": f"B. {q_data.get('選項B', '')}", "wrap": True, "size": "md", "color": "#333333"},
            {"type": "text", "text": f"C. {q_data.get('選項C', '')}", "wrap": True, "size": "md", "color": "#333333"}
        ]},
        "footer": {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": [
            {"type": "button", "style": "primary", "color": "#8b0000", "action": {"type": "message", "label": "A", "text": "A"}},
            {"type": "button", "style": "primary", "color": "#8b0000", "action": {"type": "message", "label": "B", "text": "B"}},
            {"type": "button", "style": "primary", "color": "#8b0000", "action": {"type": "message", "label": "C", "text": "C"}}
        ]}
    }
    return FlexMessage(alt_text="文物館挑戰題目", contents=FlexContainer.from_dict(bubble))

def create_wallet_flex(wallet_str):
    tickets = wallet_str.split(",") if wallet_str else []
    unredeemed_count = sum(1 for t in tickets if t.endswith(":No"))
    redeemed_count = sum(1 for t in tickets if t.endswith(":Yes"))
    
    has_ticket = unredeemed_count > 0

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#8b0000" if has_ticket else "#aaaaaa",
            "contents": [
                {"type": "text", "text": "🎟️ 我的兌換卷", "color": "#ffffff", "weight": "bold", "size": "xl", "align": "center"}
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "湯川秀樹特展・限量扭蛋", "weight": "bold", "size": "md", "align": "center"},
                {"type": "separator", "margin": "md"},
                {
                    "type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "未兌換可用票卷：", "size": "sm", "color": "#555555"},
                        {"type": "text", "text": f"{unredeemed_count} 張", "size": "md", "weight": "bold", "color": "#ff0000" if has_ticket else "#555555", "align": "end"}
                    ]
                },
                {
                    "type": "box", "layout": "horizontal", "contents": [
                        {"type": "text", "text": "歷史已兌換紀錄：", "size": "sm", "color": "#aaaaaa"},
                        {"type": "text", "text": f"{redeemed_count} 張", "size": "sm", "color": "#aaaaaa", "align": "end"}
                    ]
                },
                {"type": "separator", "margin": "md"},
                {"type": "text", "text": "兌換時間為每周三16:00~17:00，週六14:00~15:00" if has_ticket else "目前沒有可用的兌換卷喔！", "size": "xs", "align": "center", "color": "#aaaaaa", "wrap": True}
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#8b0000",
                    "action": {"type": "message", "label": "工作人員核銷", "text": "請工作人員輸入兌換密碼"}
                }
            ] if has_ticket else []
        }
    }
    return FlexMessage(alt_text="我的兌換卷", contents=FlexContainer.from_dict(bubble))

# --- 5. 邏輯功能 ---

def get_user_data(user_id):
    users = sheet_s.col_values(1)
    if user_id in users:
        row = users.index(user_id) + 1
        data = sheet_s.row_values(row)
        while len(data) < 7: data.append("") 
        return row, data
    else:
        sheet_s.append_row([user_id, 0, "", "", "", "", ""])
        return len(users) + 1, [user_id, 0, "", "", "", "", ""]

# 🌟 全新升級：具備長久記憶與防呆循環的抽題系統
def get_random_q_and_update_used(zone, used_str):
    records = sheet_q.get_all_records()
    pool = [r for r in records if str(r.get('展區', '')) == str(zone)]
    
    if not pool:
        return None, used_str # 題庫真的沒半題防呆
        
    used_list = used_str.split("|||") if used_str else []
    # 篩選出「還沒考過」的題目
    avail = [q for q in pool if str(q.get('題目', '')) not in used_list]
    
    # 如果該區所有的題目都考過了 (avail 為空)
    if not avail:
        # 只把「這一區」的歷史紀錄從大腦中刪除，讓他重新開始循環這區的題目
        zone_titles = [str(q.get('題目', '')) for q in pool]
        used_list = [u for u in used_list if u not in zone_titles]
        avail = pool # 重新開放全區題庫
        
    picked = random.choice(avail)
    used_list.append(str(picked['題目'])) # 把新抽到的題目加入記憶
    
    return picked, "|||".join(used_list)

# --- 6. 事件處理 ---

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try: line_handler.handle(body, signature)
    except InvalidSignatureError: abort(400)
    return 'OK'

@line_handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    user_id = event.source.user_id
    user_msg = event.message.text.strip()
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        row_idx, u_data = get_user_data(user_id)
        
        curr_stage = str(u_data[1])
        correct_ans = str(u_data[2]).upper()
        used_qs = str(u_data[4])
        last_play_date = str(u_data[5]) 
        wallet_str = str(u_data[6])     
        
        today_str = datetime.now(tz).strftime('%Y-%m-%d')
        
        reply_msgs = []

        # --- 兌換密碼鎖 (一鍵大量兌換) ---
        if user_msg == "請工作人員輸入兌換密碼":
            reply_msgs.append(TextMessage(text="[兌換模式] 請工作人員直接輸入「兌換密碼」以兌換所有可用兌換卷。"))
        elif user_msg == VERIFY_PASSWORD:
            if ":No" in wallet_str:
                tickets = wallet_str.split(",")
                unredeemed_count = sum(1 for t in tickets if t.endswith(":No"))
                
                new_wallet = wallet_str.replace(":No", ":Yes")
                sheet_s.update_cell(row_idx, 7, new_wallet)
                
                reply_msgs.append(create_wallet_flex(new_wallet))
                reply_msgs.append(TextMessage(text=f"兌換成功！共兌換了 {unredeemed_count} 張兌換卷。"))
            else:
                reply_msgs.append(create_wallet_flex(wallet_str))
                reply_msgs.append(TextMessage(text="目前沒有可用的兌換卷喔！"))

        # --- 呼叫我的兌換卷 ---
        elif user_msg == "我的兌換卷":
            reply_msgs.append(create_wallet_flex(wallet_str))

        # --- 啟動與規則說明 ---
        elif user_msg == "開始挑戰" or user_msg == "重新挑戰":
            sheet_s.update_cell(row_idx, 2, "Rules_Read") 
            rule_text = (
                "【挑戰規則說明】\n\n"
                "1. 連續答對8題即可獲得一張兌換卷，將自動存入「我的兌換卷」。\n"
                "2. 每人每日限領一張，歡迎每日來挑戰!\n"
                "3. 若途中答錯，挑戰將重新開始，請再接再厲!\n\n"
                "每週三16:00–17:00\n"
                "每週六14:00–15:00\n"
                "請於上述時間，憑「兌換卷」至文物館展覽廳扭蛋換取徽章!\n\n"
            )
            reply_msgs.append(TextMessage(
                text=rule_text,
                quick_reply=QuickReply(items=[
                    QuickReplyItem(action=MessageAction(label="確認規則並開始", text="確認規則並開始"))
                ])
            ))

        # --- 確認規則後發展區 1 ---
        elif user_msg == "確認規則並開始":
            sheet_s.update_cell(row_idx, 2, "Intro_1") 
            reply_msgs.append(create_zone_flex(1))

        # --- 確認開始答題 ---
        elif user_msg == "確認開始答題":
            zone = 1
            if curr_stage.startswith("Intro_"):
                zone = int(curr_stage.split("_")[1])
            elif curr_stage.isdigit():
                zone = (int(curr_stage) + 1) // 2
            
            # 🌟 呼叫具備記憶的新抽題系統
            q_data, new_used_qs = get_random_q_and_update_used(zone, used_qs)
            
            if q_data:
                new_stage = (zone * 2 - 1) if curr_stage.startswith("Intro_") else int(curr_stage)
                sheet_s.update_cell(row_idx, 2, new_stage)
                sheet_s.update_cell(row_idx, 3, str(q_data['正確答案']).upper())
                sheet_s.update_cell(row_idx, 4, str(q_data.get('提示', '')))
                sheet_s.update_cell(row_idx, 5, new_used_qs) # 直接將新記憶寫入資料庫
                
                reply_msgs.append(create_question_flex(q_data))
                if str(q_data.get('提示', '')).strip():
                    reply_msgs.append(TextMessage(text=f"提示：{q_data['提示']}"))

        # --- 答題處理 ---
        elif curr_stage.isdigit() and int(curr_stage) > 0 and user_msg in ["A", "B", "C"]:
            s_int = int(curr_stage)
            if user_msg == correct_ans:
                if s_int >= MAX_STAGE:
                    # ✅ 通關處理！
                    sheet_s.update_cell(row_idx, 2, "Completed")
                    
                    if last_play_date == today_str:
                        reply_msgs.append(TextMessage(text="恭喜！你已順利通關 8 道難題！\n\n(註：您今日已經領取過兌換卷囉，每天限領一張，歡迎明天再來挑戰收集！)"))
                        reply_msgs.append(create_wallet_flex(wallet_str))
                    else:
                        new_wallet = wallet_str + f",{today_str}:No" if wallet_str else f"{today_str}:No"
                        # 🌟 移除原本清空記憶的程式碼，讓使用者的答題紀錄永久保存
                        sheet_s.update_cell(row_idx, 6, today_str) 
                        sheet_s.update_cell(row_idx, 7, new_wallet) 
                        
                        reply_msgs.append(TextMessage(text="恭喜！你已順利通關 8 道難題！\n已將今日的限量扭蛋兌換卷存入「我的兌換卷」中 🎁"))
                        reply_msgs.append(create_wallet_flex(new_wallet))
                else:
                    next_s = s_int + 1
                    is_new_zone = (next_s % 2 != 0)
                    zone = (next_s + 1) // 2
                    
                    if is_new_zone:
                        sheet_s.update_cell(row_idx, 2, f"Intro_{zone}")
                        reply_msgs.append(TextMessage(text="答對了！進入下一區。"))
                        reply_msgs.append(create_zone_flex(zone))
                    else:
                        # 🌟 呼叫具備記憶的新抽題系統
                        q_data, new_used_qs = get_random_q_and_update_used(zone, used_qs)
                        sheet_s.update_cell(row_idx, 2, next_s)
                        sheet_s.update_cell(row_idx, 3, str(q_data['正確答案']).upper())
                        sheet_s.update_cell(row_idx, 4, str(q_data.get('提示', '')))
                        sheet_s.update_cell(row_idx, 5, new_used_qs) # 直接將新記憶寫入資料庫
                        
                        reply_msgs.append(TextMessage(text="答對了！繼續本區下一題。"))
                        reply_msgs.append(create_question_flex(q_data))
                        if str(q_data.get('提示', '')).strip():
                            reply_msgs.append(TextMessage(text=f"提示：{q_data['提示']}"))
            else:
                sheet_s.update_cell(row_idx, 2, 0)
                fail_qr = QuickReply(items=[QuickReplyItem(action=MessageAction(label="重新挑戰", text="重新挑戰"))])
                reply_msgs.append(TextMessage(text="答錯了！挑戰失敗，進度已歸零。請重新觀察展品後，再次挑戰吧！", quick_reply=fail_qr))

        # --- 預設導引 ---
        else:
            if curr_stage == "Completed":
                reply_msgs.append(create_wallet_flex(wallet_str))
                reply_msgs.append(TextMessage(text="想繼續挑戰收集兌換卷嗎？輸入「開始挑戰」。(每日限領一張)"))
            else:
                qr = QuickReply(items=[
                    QuickReplyItem(action=MessageAction(label="我的兌換卷", text="我的兌換卷")),
                    QuickReplyItem(action=MessageAction(label="開始挑戰", text="開始挑戰"))
                ])
                reply_msgs.append(TextMessage(text="歡迎來到清大文物館！\n您可以隨時點選選單或輸入「我的兌換卷」查看收集進度，或是點擊下方開始解謎之旅！", quick_reply=qr))

        line_bot_api.reply_message_with_http_info(ReplyMessageRequest(reply_token=event.reply_token, messages=reply_msgs))

if __name__ == "__main__":
    app.run(port=5000)
