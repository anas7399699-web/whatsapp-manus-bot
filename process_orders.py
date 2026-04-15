import pandas as pd
import sys
import os

def process_excel_orders_to_list(file_path):
    """
    يعالج ملف الإكسل ويعيد قائمة من الرسائل (رسالة لكل صف)
    """
    try:
        df = pd.read_excel(file_path)
        
        # تصفية الصفوف التي تحتوي على "قيد التنفيذ"
        mask = df.apply(lambda row: row.astype(str).str.contains('قيد التنفيذ', na=False).any(), axis=1)
        df_in_progress = df[mask]
        
        print(f"DEBUG: Found {len(df_in_progress)} orders with 'قيد التنفيذ'.")
        
        messages = []
        
        # تحديد أسماء الأعمدة بشكل مرن
        city_col = 'المدينة' if 'المدينة' in df.columns else 'City'
        main_addr_col = next((col for col in df.columns if 'عنوان' in col or 'Address' in col), 'Address')
        short_addr_col = next((col for col in df.columns if 'short_address' in col), 'shipping_short_address')
        building_no_col = next((col for col in df.columns if 'building_number' in col), 'shipping_building_number')
        additional_no_col = next((col for col in df.columns if 'additional_number' in col), 'shipping_additional_number')
        postal_code_col = next((col for col in df.columns if 'postal_code' in col), 'postal_code')
        rec_name_col = next((col for col in df.columns if 'إسم المستلم الثاني' in col), 'إسم المستلم الثاني')
        rec_mobile_col = next((col for col in df.columns if 'receiver_mobile' in col), 'receiver_mobile')
        cust_name_col = next((col for col in df.columns if 'اسم العميل' in col or 'Customer Name' in col), 'Customer Name')
        cust_mobile_col = next((col for col in df.columns if 'رقم الجوال' in col or 'Mobile' in col), 'Mobile')
        order_id_col = 'رقم الطلب' if 'رقم الطلب' in df.columns else 'Order ID'

        def format_order(row):
            # 1. بناء العنوان بالتفصيل
            address_parts = []
            main_address = str(row[main_addr_col]) if main_addr_col in row and pd.notna(row[main_addr_col]) else ""
            if main_address:
                address_parts.append(main_address)
            else:
                address_parts.append(str(row[city_col]) if city_col in row and pd.notna(row[city_col]) else "")
            
            if short_addr_col in row and pd.notna(row[short_addr_col]):
                address_parts.append(f"العنوان المختصر {row[short_addr_col]}")
            if building_no_col in row and pd.notna(row[building_no_col]):
                b_no = str(row[building_no_col]).split('.')[0]
                address_parts.append(f"رقم المبنى {b_no}")
            if additional_no_col in row and pd.notna(row[additional_no_col]):
                a_no = str(row[additional_no_col]).split('.')[0]
                address_parts.append(f"الرقم الاضافي {a_no}")
            if postal_code_col in row and pd.notna(row[postal_code_col]):
                p_code = str(row[postal_code_col]).split('.')[0]
                address_parts.append(f"الرمز البريدي {p_code}")
                
            full_address = " ".join(address_parts)
            
            # 2. منطق المستلم مقابل العميل
            recipient_name = str(row[rec_name_col]) if rec_name_col in row and pd.notna(row[rec_name_col]) and str(row[rec_name_col]).strip() != "" else str(row[cust_name_col])
            raw_mobile = str(row[rec_mobile_col]) if rec_mobile_col in row and pd.notna(row[rec_mobile_col]) and str(row[rec_mobile_col]).strip() != "" else str(row[cust_mobile_col])
            mobile_str = str(raw_mobile).split('.')[0].strip()
            
            # التأكد من وجود كود الدولة 966
            if mobile_str.startswith('5') and len(mobile_str) == 9:
                mobile_str = '966' + mobile_str
            elif mobile_str.startswith('05') and len(mobile_str) == 10:
                mobile_str = '966' + mobile_str[1:]
            
            order_id = row[order_id_col] if order_id_col in row else "غير متوفر"
            
            return f"العنوان / {full_address}\nرقم الطلبية/ {order_id}\nرقم المستلم / +{mobile_str}\nاسم المستلم/ {recipient_name}"

        # معالجة كل صف وإضافته للقائمة
        for _, row in df_in_progress.iterrows():
            messages.append(format_order(row))
            
        return messages
    except Exception as e:
        print(f"Error: {str(e)}")
        return None

# للحفاظ على التوافق مع أي استدعاء قديم (اختياري)
def process_excel_orders(file_path, output_path):
    messages = process_excel_orders_to_list(file_path)
    if messages:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("\n\n---\n\n".join(messages))
        return True
    return False
