import os
import requests
import re
from flask import Flask, request, jsonify

app = Flask(__name__)

# هذه المتغيرات سنضبطها في Render للأمان
SALLA_TOKEN = os.getenv("SALLA_TOKEN")
META_TOKEN = os.getenv("META_TOKEN")
PHONE_ID = os.getenv("PHONE_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN", "aliza_secure_pass")

def send_whatsapp_message(to, text):
    url = f"https://graph.facebook.com/v17.0/{PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {META_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text}}
    requests.post(url, json=payload, headers=headers)

@app.route('/webhook', methods=['GET'])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Failed", 403

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.json
    try:
        msg_body = data['entry'][0]['changes'][0]['value']['messages'][0]['text']['body']
        sender = data['entry'][0]['changes'][0]['value']['messages'][0]['from']
        
        # استخراج رقم الطلب (بافتراض أنه رقم مكون من عدة خانات)
        order_id = re.findall(r'\d+', msg_body)
        
        if order_id:
            id_val = order_id[0]
            
            # --- كود الربط مع n8n المخصص لمتجر سلة ---
            n8n_webhook_url = "https://n8n-setup-x4if.onrender.com/webhook-test/get-salla-order"
            payload_to_n8n = {
                "order_id": id_val,
                "phone": sender,
                "message_text": msg_body
            }
            try:
                # إرسال رقم الطلب والبيانات إلى n8n فوراً
                requests.post(n8n_webhook_url, json=payload_to_n8n, timeout=5)
            except Exception as e:
                print(f"Error sending to n8n: {e}")
            # ----------------------------------------

            if "عنوان" in msg_body:
                send_whatsapp_message(sender, f"🔍 جاري فحص تفاصيل وجلب عنوان الطلب {id_val} من متجر أليزا عبر n8n...")
            elif "تم التنفيذ" in msg_body:
                send_whatsapp_message(sender, f"⚙️ جاري تحديث حالة الطلب {id_val} إلى تم التنفيذ عبر n8n...")
            else:
                # إذا أرسل الرقم فقط بدون كلمات مفتاحية
                send_whatsapp_message(sender, f"👍 تم استلام رقم الطلب {id_val}. جاري استخراج البيانات والتحقق من التفاصيل...")
        else:
            send_whatsapp_message(sender, "أهلاً بك، يرجى كتابة رقم الطلب مع الأمر (مثلاً: عنوان الطلب 123)")
            
    except Exception as e: 
        print(f"Webhook Error: {e}")
        
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
