import pandas as pd
import sys
import os

def process_excel_orders(file_path, output_path):
    try:
        df = pd.read_excel(file_path)
        
        # Filter for "قيد التنفيذ" with more flexibility - check all columns
        mask = df.apply(lambda row: row.astype(str).str.contains('قيد التنفيذ', na=False).any(), axis=1)
        df_in_progress = df[mask]
        
        # Log columns for debugging
        print(f"DEBUG: Found {len(df_in_progress)} orders with 'قيد التنفيذ'. Columns: {df.columns.tolist()}")
        
        # Categories
        city_col = 'المدينة' if 'المدينة' in df.columns else 'City'
        riyadh_orders = df_in_progress[df_in_progress[city_col].str.contains('Riyadh|الرياض', case=False, na=False)]
        other_orders = df_in_progress[~df_in_progress[city_col].str.contains('Riyadh|الرياض', case=False, na=False)]
        
        def format_order(row):
            # 1. Detailed Address Construction
            address_parts = []
            main_addr_col = next((col for col in df.columns if 'عنوان' in col or 'Address' in col), 'Address')
            short_addr_col = next((col for col in df.columns if 'short_address' in col), 'shipping_short_address')
            building_no_col = next((col for col in df.columns if 'building_number' in col), 'shipping_building_number')
            additional_no_col = next((col for col in df.columns if 'additional_number' in col), 'shipping_additional_number')
            postal_code_col = next((col for col in df.columns if 'postal_code' in col), 'postal_code')
            
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
            
            # 2. Recipient vs Customer Logic
            rec_name_col = next((col for col in df.columns if 'إسم المستلم الثاني' in col), 'إسم المستلم الثاني')
            rec_mobile_col = next((col for col in df.columns if 'receiver_mobile' in col), 'receiver_mobile')
            cust_name_col = next((col for col in df.columns if 'اسم العميل' in col or 'Customer Name' in col), 'Customer Name')
            cust_mobile_col = next((col for col in df.columns if 'رقم الجوال' in col or 'Mobile' in col), 'Mobile')
            
            recipient_name = str(row[rec_name_col]) if rec_name_col in row and pd.notna(row[rec_name_col]) and str(row[rec_name_col]).strip() != "" else str(row[cust_name_col])
            
            raw_mobile = str(row[rec_mobile_col]) if rec_mobile_col in row and pd.notna(row[rec_mobile_col]) and str(row[rec_mobile_col]).strip() != "" else str(row[cust_mobile_col])
            
            mobile_str = raw_mobile.split('.')[0].strip()
            
            # Ensure 966 is present
            if mobile_str.startswith('5') and len(mobile_str) == 9:
                mobile_str = '966' + mobile_str
            elif mobile_str.startswith('05') and len(mobile_str) == 10:
                mobile_str = '966' + mobile_str[1:]
            
            order_id_col = 'رقم الطلب' if 'رقم الطلب' in df.columns else 'Order ID'
            order_id = row[order_id_col]
            
            return f"العنوان / {full_address}\nرقم الطلبية/ {order_id}\nرقم المستلم / +{mobile_str}\nاسم المستلم/ {recipient_name}\n"

        output_text = "طلبات مدينة الرياض\n" + "="*20 + "\n\n"
        for _, row in riyadh_orders.iterrows():
            output_text += format_order(row) + "\n" + "-"*20 + "\n\n"

        output_text += "\nطلبات باقي المناطق\n" + "="*20 + "\n\n"
        for _, row in other_orders.iterrows():
            output_text += format_order(row) + "\n" + "-"*20 + "\n\n"

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output_text)
            
        print(f"Success: Processed {len(df_in_progress)} orders. Output saved to {output_path}")
        return True
    except Exception as e:
        print(f"Error: {str(e)}")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python process_orders.py <input_excel> <output_txt>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    process_excel_orders(input_file, output_file)
